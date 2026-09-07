# Agent Governance Toolkit — live demo

> An independent demo built on Microsoft's MIT-licensed
> [agent-governance-toolkit](https://github.com/microsoft/agent-governance-toolkit).
> Not affiliated with, endorsed by, or supported by Microsoft.

A working prototype built on the real
[microsoft/agent-governance-toolkit](https://github.com/microsoft/agent-governance-toolkit)
Quick Start. **Every verdict the demo prints is produced by AGT's policy engine**
evaluating the YAML in `policies/` — nothing is simulated or stubbed.

Verified against `agent-governance-toolkit==4.1.0`, Python 3.13.

![Governance blocking a destructive query and a PII export](docs/images/02-governed.png)

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python demo.py
```

AGT's `[full]` extra does not install `sqlglot`, yet AGT needs it to parse SQL
into policy facets, so `requirements.txt` pins it explicitly. Without it every
`sql.*` facet becomes `UNKNOWN` and SQL rules stop matching. That is fail-closed
here, because all three policies are `default_action: deny` — but it fails **open**
under an allow-default policy, which is the posture AGT's own Quick Start uses.
AGT logs a warning, not an error, so nothing stops you shipping it.

## The scenario

Two agents at a fictional company share a set of genuinely dangerous tools
(`tools.py`): run SQL, send mail, export datasets. The tools have no guardrails
and never learn about policy — governance is applied from the outside, at the
call boundary.

| Act | What it shows |
|-----|---------------|
| 1 | The ungoverned agent drops a table and exports 12,000 PII records. Nothing says no. |
| 2 | `govern()` wraps the same tools. Both attacks are blocked; the legitimate anonymised export still flows. |
| 3 | Policy reads a **parsed SQL AST**, not the query string — including the case where parsing alone is not enough. |
| 4 | `require_approval` routes a bulk send to a human, who approves one and rejects another. |
| 5 | Same tool, same arguments, different agent identity → different verdict (identity is self-asserted; see finding 6). |
| 6 | The audit chain is verified, then forged, and the forgery is detected. |

## What it looks like

Every image below is a real run, rendered straight from the demo's own output by
`capture.py` — not a mockup. Re-running it after a policy change changes the pictures.

**Act 1 — the ungoverned agent.** The tools do exactly what they are told.

![The ungoverned agent drops a table and exports PII](docs/images/01-ungoverned.png)

**Act 3 — structure, not string matching.** One rule, both cases right.

![A SELECT containing the word drop is allowed; a disguised DROP is denied](docs/images/03-sql-ast.png)

**Act 4 — a human in the loop.** `require_approval` routes the decision to a person.

![A routine send allowed, a 200-person notice approved, a 40,000-person campaign rejected](docs/images/04-approval.png)

**Act 6 — the audit chain, verified and then forged.**

![The chain verifies, then fails with Entry 0 hash mismatch after tampering](docs/images/06-audit.png)

Act 5 is in [docs/images/05-identity.png](docs/images/05-identity.png). Each image is
also written as SVG alongside the PNG.

## The part worth pausing on (Act 3)

AGT parses SQL into an AST and exposes `sql.verb` as a policy facet. One rule
gets both of these right, where a substring denylist gets both wrong:

```sql
-- ALLOWED: a SELECT that merely contains the word "drop"
SELECT id FROM tickets WHERE subject = 'drop shipment delayed'

-- DENIED: a DROP disguised with case and comment tricks
/* nightly cleanup */ dRoP   TaBLE  "customers"
```

The honest part of this argument is the third case, which the demo also runs:

```sql
-- a DROP stacked behind an innocent SELECT
SELECT 1; DROP TABLE customers
```

A substring denylist catches that one. **AGT's shipped extractor does not** — it
reads `sqlglot.parse()[0]` and reports `verb=SELECT`, so a verb rule lets it
through. Parsing beats substring matching only if you parse the whole input, so
this repo replaces the extractor ([`sqlfacets.py`](sqlfacets.py)) with one that
reads every statement and reports the most dangerous verb across all of them.
See the findings below.

## Policy layout

`policies/baseline.yaml` holds organization-wide rules. Both agent policies
inherit it with `extends:`. Inheritance is close to additive-only, but the
guarantee is narrower than it first appears, so state it precisely: a child
policy cannot redefine an inherited deny **under the same rule name** as an
`allow` or `log` — that case is caught and logged (`policy.py:361-364`). Two
things are not covered. Parent rules are deduped by name with first-parent-wins,
so a same-named permissive rule in an *earlier* `extends` parent silently
shadows a later baseline deny. And a differently-named child `allow` at higher
priority beats the baseline deny under `priority_first_match`, `allow_overrides`
and `most_specific_wins` — only `deny_overrides` holds. `govern()` defaults to
`deny_overrides`, but `PolicyEngine` itself defaults to `priority_first_match`.

```
baseline.yaml ── block destructive SQL, block writes, block PII export
    ├── support-agent.yaml   (Nova)  + read, + routine mail, + approval-gated bulk mail
    └── analytics-agent.yaml (Atlas) + read only
```

Both policies set `default_action: deny`, so anything not explicitly granted is
refused. Act 5's denial (`No matching rules, using default`) is that fail-closed
default doing its job.

## Findings from building this

Seven defects in AGT 4.1.0, each reproduced against the installed package. Nothing
here is inferred from documentation.

### 1. `agt lint-policy` rejects policies the runtime executes

The linter and the policy runtime disagree about which rule actions exist, and
the two vocabularies are **disjoint apart from `allow` and `deny`**:

| | Accepted actions |
|---|---|
| Runtime (`agentmesh.governance.policy.PolicyRule.action`) | `allow`, `deny`, `warn`, `require_approval`, `log` |
| Linter (`agent_compliance.lint_policy.KNOWN_ACTIONS`) | `allow`, `deny`, `audit`, `block`, `escalate`, `rate_limit` |

So `agt lint-policy` reports `unknown action 'require_approval'` — on a policy the
engine executes correctly, and on the spelling **AGT's own README Quick Start uses**
(README.md:109 upstream). Conversely every action the linter uniquely accepts
(`audit`, `block`, `escalate`, `rate_limit`) raises a Pydantic `ValidationError` at
load time.

This matters because the linter is a documented gate, though not in the README:
`docs/tutorials/progressive-governance.md:39` says "Run `agt lint-policy` and
`agt test` in CI", and `.pre-commit-hooks.yaml` ships `id: validate-policy` with
`entry: agt lint-policy` and a `files` glob of
`(^|/)(manifest|.*polic.*)\.(yaml|yml|json)$` — which matches every file in this
repo's `policies/`, and upstream's own Quick Start `policy.yaml`.

The linter also requires a `version:` field the runtime happily defaults.

### 2. The SQL facet extractor crashes on any current sqlglot

`protocol_facets._extract_sql_facets` dereferences `sqlglot.exp.AlterTable`, which
sqlglot removed long ago — it is absent in 25.x and 30.x alike. `ALTER`, `TRUNCATE`
and `GRANT` therefore raise `AttributeError` inside the extractor.

`FacetRegistry.extract` catches and logs it, so **no `sql` facet is set at all**.
A rule reading `sql.verb` silently stops matching. Under this repo's deny-default
policies that fails closed; under an allow-default policy it fails open.

### 3. The extractor reads only the first statement

`sqlglot.parse()` returns a list and the extractor uses element zero, so
`SELECT 1; DROP TABLE customers` reports `verb=SELECT`. A verb rule allows it —
the one case a crude substring denylist would have caught.

### 4. Facet extractors cannot be replaced

`FacetRegistry.register` appends to a list and there is no `unregister` or replace.
Registering `"sql"` again leaves the broken built-in ahead of yours: it still runs,
still raises, still logs a traceback per call. Overriding it means mutating the
private `_extractors` list, which [`sqlfacets.py`](sqlfacets.py) does and documents.

Reproduce all four:

```bash
.venv/bin/agt lint-policy policies/     # finding 1
.venv/bin/python check_policy.py        # passes; break a field name to see finding 5
```

### 5. A misspelled field in a condition silently turns a deny into an allow

The condition grammar has no notion of an unknown field. An expression naming a
field nothing puts in the context evaluates `False`, the rule stops matching, and
nothing warns you. Rename `data.contains_pii` to `data.has_pii` in
`baseline.yaml` and a 12,000-row PII export goes from DENIED to **ALLOWED**:

```
PII export: expected deny/block-pii-export, got allow/allow-anonymised-export
```

`default_action: deny` does not save you — a sibling `allow` rule picks the call
up instead. `agt lint-policy` validates structure, not field names, so it passes
the broken policy.

This is why [`check_policy.py`](check_policy.py) exists: one case that must match
per rule, one that must not, and a coverage assertion that every enabled rule is
reachable. A rule nothing can reach is not protecting anything. Run it in CI —
it is the control that turns this silent failure into a loud one.

### 6. `agent_id` is self-asserted

`Policy.applies_to` is a plain string comparison against the `agent_id` passed to
`govern()`. Nothing authenticates it. Per-agent scoping — Act 5 in this demo — is
therefore an honour system: it correctly routes a *cooperative* agent to its own
policy, but an agent that names itself something else gets that something else's
grants. Useful for separating concerns; not a security boundary.

### 7. `GovernanceConfig.audit_file` does nothing

Declared at `govern.py:100` and `:113`, read nowhere in the package. Setting it
silently has no effect, and `govern()` has no shared-sink parameter either — so
every governed callable keeps a private in-memory `AuditLog`. That is why Act 6
shows one tool's ledger rather than a fleet-wide one.

Findings 2-4 are why this repo ships its own extractor rather than pinning an
ancient sqlglot. Treat the runtime as the source of truth; this demo does.

## Files

- `tools.py` — the raw, ungoverned tools. Knows nothing about policy.
- `policies/` — baseline plus one policy per agent.
- `demo.py` — the six acts.
- `theatre.py` — terminal presentation only, no governance logic.
- `sqlfacets.py` — a replacement SQL facet extractor; see findings 2-4.
- `check_policy.py` — condition-coverage test for the policies; see finding 5.
- `docs/ARCHITECTURE.md` — design for governing a multi-agent build.
- `docs/PARALLEL_AGENT_PLAN.md` — how several agents would build this repo.
- `capture.py` — re-renders the README images from a real run.
- `docs/images/` — generated; do not hand-edit.
