"""Condition-coverage test for the policies in ./policies.

Why this exists. AGT's condition grammar fails silently: an expression that
references a field name nothing puts in the context simply evaluates False, and
the rule stops matching. Nothing warns you. Rename `data.contains_pii` to
`data.has_pii` in baseline.yaml and a 12,000-row PII export goes from DENIED to
ALLOWED — `default_action: deny` does not save you, because a sibling allow rule
picks it up instead.

Neither of AGT's own tools catches that: `agt lint-policy` validates structure,
not field names, and reports a false positive on `require_approval` anyway.

So every rule gets at least one case that must match it, and one that must not.
A rule no case can reach is a failure, because a rule nothing can reach is a
rule that is not protecting anything.

    python check_policy.py        # exit 0 pass, 1 fail
"""

from __future__ import annotations

import sys
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

import sqlfacets  # noqa: E402
from agentmesh.governance import PolicyEngine  # noqa: E402

sqlfacets.install()

NOVA = "did:mesh:support-nova"
ATLAS = "did:mesh:analytics-atlas"

SUPPORT = "policies/support-agent.yaml"
ANALYTICS = "policies/analytics-agent.yaml"


def sql(query: str) -> dict:
    return {"action": {"type": "db_query"}, "sql": {"query": query}}


def email(recipients: int) -> dict:
    return {"action": {"type": "send_email"}, "recipients": {"value": recipients}}


def export(pii: bool, rows: int) -> dict:
    return {
        "action": {"type": "export_dataset"},
        "data": {"contains_pii": pii},
        "rows": {"value": rows},
    }


# (label, policy, agent, context, expected_action, expected_rule)
# expected_rule None means "no named rule — the policy default decided".
CASES = [
    # baseline: block-schema-destructive-sql
    ("disguised DROP",     SUPPORT, NOVA, sql('/* x */ dRoP TaBLE "customers"'), "deny", "block-schema-destructive-sql"),
    ("stacked DROP",       SUPPORT, NOVA, sql("SELECT 1; DROP TABLE customers"), "deny", "block-schema-destructive-sql"),
    ("TRUNCATE",           SUPPORT, NOVA, sql("TRUNCATE TABLE customers"),       "deny", "block-schema-destructive-sql"),
    ("ALTER",              SUPPORT, NOVA, sql("ALTER TABLE customers ADD c INT"), "deny", "block-schema-destructive-sql"),
    ("GRANT",              SUPPORT, NOVA, sql("GRANT ALL ON customers TO bob"),  "deny", "block-schema-destructive-sql"),
    # baseline: block-row-deletion
    ("DELETE",             SUPPORT, NOVA, sql("DELETE FROM invoices"),           "deny", "block-row-deletion"),
    ("UPDATE",             SUPPORT, NOVA, sql("UPDATE invoices SET x = 1"),      "deny", "block-row-deletion"),
    # baseline: db-query-is-select-only (the backstop)
    ("INSERT",             SUPPORT, NOVA, sql("INSERT INTO tickets VALUES (1)"), "deny", "db-query-is-select-only"),
    ("CREATE",             SUPPORT, NOVA, sql("CREATE TABLE t (a INT)"),         "deny", "db-query-is-select-only"),
    ("unparseable SQL",    SUPPORT, NOVA, sql("!!! not sql at all"),             "deny", "db-query-is-select-only"),
    # baseline: block-pii-export — the rule the silent-typo bug disables
    ("PII export",         SUPPORT, NOVA, export(True, 900),                     "deny", "block-pii-export"),
    ("PII export, large",  SUPPORT, NOVA, export(True, 12_000),                  "deny", "block-pii-export"),
    # support: the allow paths
    ("innocent SELECT",    SUPPORT, NOVA, sql("SELECT id FROM tickets WHERE subject = 'drop shipment delayed'"), "allow", "allow-read-queries"),
    ("routine email",      SUPPORT, NOVA, email(3),                              "allow", "allow-routine-email"),
    ("bulk email",         SUPPORT, NOVA, email(2_500),                          "require_approval", "approve-bulk-email"),
    ("clean export",       SUPPORT, NOVA, export(False, 12_000),                 "allow", "allow-anonymised-export"),
    ("oversized export",   SUPPORT, NOVA, export(False, 90_000),                 "deny", None),
    # analytics: a strictly smaller grant
    ("Atlas may read",     ANALYTICS, ATLAS, sql("SELECT 1 FROM tickets"),       "allow", "allow-read-queries"),
    ("Atlas may not mail", ANALYTICS, ATLAS, email(1),                           "deny", None),
    ("Atlas DROP",         ANALYTICS, ATLAS, sql("DROP TABLE customers"),        "deny", "block-schema-destructive-sql"),
    # analytics inherits the whole baseline, so the baseline rules must be
    # reachable there too — inheritance that is never exercised is a claim,
    # not a control.
    ("Atlas DELETE",       ANALYTICS, ATLAS, sql("DELETE FROM invoices"),        "deny", "block-row-deletion"),
    ("Atlas INSERT",       ANALYTICS, ATLAS, sql("INSERT INTO tickets VALUES (1)"), "deny", "db-query-is-select-only"),
    ("Atlas PII export",   ANALYTICS, ATLAS, export(True, 900),                  "deny", "block-pii-export"),
    # identity scoping: a policy scoped to one agent must not apply to another
    ("wrong agent id",     SUPPORT, ATLAS, sql("SELECT 1 FROM tickets"),         "deny", None),
]


def main() -> int:
    engines, rules = {}, {}
    for path in (SUPPORT, ANALYTICS):
        engine = PolicyEngine(conflict_strategy="deny_overrides")
        policy = engine.load_yaml_file(path)
        engines[path] = engine
        rules[path] = {r.name for r in policy.rules if r.enabled}

    failures = []

    for label, path, agent, context, want_action, want_rule in CASES:
        got = engines[path].evaluate(agent, dict(context))
        if got.action != want_action or got.matched_rule != want_rule:
            failures.append(
                f"{label}: expected {want_action}/{want_rule}, "
                f"got {got.action}/{got.matched_rule}"
            )

    # Coverage: every enabled rule must be reachable by at least one case.
    # This is what catches a renamed or misspelled field in a condition —
    # the rule stops matching and no case can produce it any more.
    for path, names in rules.items():
        covered = {r for label, p, *_rest, r in CASES if p == path and r}
        for name in sorted(names - covered):
            failures.append(f"{path}: rule '{name}' is not reachable by any case")

    total = len(CASES) + sum(len(v) for v in rules.values())
    if failures:
        print(f"FAIL — {len(failures)} of {total} checks\n")
        for f in failures:
            print(f"  {f}")
        return 1

    print(f"ok — {len(CASES)} cases, {sum(len(v) for v in rules.values())} rules all reachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
