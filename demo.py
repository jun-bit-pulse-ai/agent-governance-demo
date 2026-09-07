"""Agent Governance Toolkit — a live demo in six acts.

Everything here runs against the real `agent-governance-toolkit` package.
No verdict in this script is simulated: every ALLOW, DENY and APPROVAL is
produced by AGT's policy engine evaluating the YAML in ./policies.

    python demo.py
"""

from __future__ import annotations

import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

from agentmesh.governance import (  # noqa: E402
    CallbackApproval,
    ApprovalDecision,
    GovernanceDenied,
    govern,
)

import sqlfacets  # noqa: E402
import theatre as t  # noqa: E402
import tools  # noqa: E402

# Replace AGT's SQL facet extractor before any policy is evaluated. See
# sqlfacets.py for the two defects in the shipped one that this fixes.
sqlfacets.install()

NOVA = "did:mesh:support-nova"
ATLAS = "did:mesh:analytics-atlas"

SUPPORT_POLICY = "policies/support-agent.yaml"
ANALYTICS_POLICY = "policies/analytics-agent.yaml"

# A SELECT whose *text* contains the word "drop" — a naive denylist would
# wrongly block this. And a DROP disguised with case and comment tricks —
# a naive denylist would wrongly allow it.
INNOCENT_SELECT = "SELECT id FROM tickets WHERE subject = 'drop shipment delayed'"
DISGUISED_DROP = '/* nightly cleanup */ dRoP   TaBLE  "customers"'
# A stacked statement. AGT's own facet extractor reads only the first one and
# would report SELECT, letting this through — the case a crude substring
# denylist actually catches. sqlfacets.py reads every statement.
STACKED_DROP = "SELECT 1; DROP TABLE customers"


def _attempt(call, describe) -> None:
    """Run a governed call and report whichever verdict the engine returned."""
    try:
        t.outcome("allow", describe(call()))
    except GovernanceDenied as exc:
        t.outcome("deny", str(exc.decision.reason), exc.decision.matched_rule)


# ── Act 1 ─────────────────────────────────────────────────────────────────

def act_one() -> None:
    t.act(1, "The agent you have today", "Capable tools, no control surface.")
    t.estate(tools.TABLES, "production")

    t.call("db_query", DISGUISED_DROP)
    result = tools.db_query(action="db_query", sql={"query": DISGUISED_DROP})
    t.outcome(
        "allow",
        f"executed — dropped '{result['dropped']}', "
        f"{result['rows_destroyed']:,} rows destroyed",
    )

    t.call("export_dataset", "12,000 customer records → s3://partner-bucket")
    tools.export_dataset(
        action="export_dataset",
        data={"contains_pii": True},
        rows=12_000,
        destination="s3://partner-bucket",
    )
    t.outcome("allow", "exported — 12,000 rows of personal data left the building")

    t.estate(tools.TABLES, "production, moments later")
    t.note("The tools did exactly what they were told. Nothing was there to say no.")


# ── Act 2 ─────────────────────────────────────────────────────────────────

def act_two() -> tuple:
    t.act(2, "Two lines of governance", "The tool code does not change at all.")
    tools.reset()

    t.banner(
        "[bold]safe_db = govern(tools.db_query, policy=SUPPORT_POLICY, agent_id=NOVA)[/]",
        style="green",
    )

    safe_db = govern(tools.db_query, policy=SUPPORT_POLICY, agent_id=NOVA)

    safe_export = govern(tools.export_dataset, policy=SUPPORT_POLICY, agent_id=NOVA)

    t.call("safe_db", DISGUISED_DROP)
    _attempt(lambda: safe_db(action="db_query", sql={"query": DISGUISED_DROP}),
             lambda _: "executed")

    t.call("safe_export", "12,000 customer records → s3://partner-bucket")
    _attempt(
        lambda: safe_export(
            action="export_dataset",
            data={"contains_pii": True},
            rows=12_000,
            destination="s3://partner-bucket",
        ),
        lambda _: "exported",
    )

    t.call("safe_export", "the same query, anonymised — legitimate work still flows")
    _attempt(
        lambda: safe_export(
            action="export_dataset",
            data={"contains_pii": False},
            rows=12_000,
            destination="s3://partner-bucket",
        ),
        lambda r: f"exported {r['rows']:,} anonymised rows",
    )

    t.estate(tools.TABLES, "production, intact")
    t.note(
        "Neither denied call reached its tool. Governance is a gate in front of "
        "execution, not a warning after it."
    )
    return safe_db,


# ── Act 3 ─────────────────────────────────────────────────────────────────

def act_three(safe_db) -> None:
    t.act(
        3,
        "Structure, not string matching",
        "AGT parses SQL into an AST and evaluates policy on the verb.",
    )

    for label, query in (
        ("a SELECT that merely contains the word 'drop'", INNOCENT_SELECT),
        ("a DROP disguised with case and comments", DISGUISED_DROP),
        ("a DROP stacked behind an innocent SELECT", STACKED_DROP),
    ):
        t.call(label, query)
        try:
            res = safe_db(action="db_query", sql={"query": query})
            t.outcome("allow", f"executed — returned {res['rows']} rows")
        except GovernanceDenied as exc:
            t.outcome("deny", str(exc.decision.reason), exc.decision.matched_rule)

    t.note(
        "A substring denylist gets the first two wrong, in both directions. But it "
        "would catch the third, and AGT's own extractor does not — it reads only the "
        "first statement. sqlfacets.py replaces it and reports the worst verb across "
        "all of them. Parsing beats substrings only when you parse the whole input."
    )


# ── Act 4 ─────────────────────────────────────────────────────────────────

def _security_team(request) -> ApprovalDecision:
    """Stands in for a human on the security rota."""
    recipients = request.context.get("recipients", {}).get("value", 0)
    t.console.print(
        f"    [yellow]⧗ approval requested[/] — rule '{request.rule_name}', "
        f"approvers {request.approvers}, {recipients:,} recipients"
    )
    if recipients > 1000:
        return ApprovalDecision(
            approved=False,
            approver="security-team:priya",
            reason="Campaign-sized send from an agent — refused pending review",
        )
    return ApprovalDecision(
        approved=True, approver="security-team:priya", reason="Scoped announcement, signed off"
    )


def act_four() -> None:
    t.act(4, "A human in the loop", "require_approval routes the decision to a person.")

    safe_email = govern(
        tools.send_email,
        policy=SUPPORT_POLICY,
        agent_id=NOVA,
        approval_handler=CallbackApproval(_security_team),
    )

    sends = [
        ("one customer reply", "ana@example.com", 1),
        ("a 200-person maintenance notice", "status@example.com", 200),
        ("a 40,000-person campaign", "all-users@example.com", 40_000),
    ]
    for label, to, recipients in sends:
        t.call("safe_email", f"{label} → {to}")
        try:
            safe_email(action="send_email", to=to, recipients=recipients, body="…")
            t.outcome("allow", f"sent to {recipients:,} recipient" + ("s" if recipients != 1 else ""))
        except GovernanceDenied as exc:
            t.outcome("deny", str(exc.decision.reason), exc.decision.matched_rule)

    t.note(f"Outbox holds {len(tools.OUTBOX)} of {len(sends)} attempted sends.")


# ── Act 5 ─────────────────────────────────────────────────────────────────

def act_five() -> None:
    t.act(5, "Which agent did this?", "Identity decides which policy applies.")

    atlas_db = govern(tools.db_query, policy=ANALYTICS_POLICY, agent_id=ATLAS)
    atlas_email = govern(tools.send_email, policy=ANALYTICS_POLICY, agent_id=ATLAS)

    t.call("Atlas · db_query", "SELECT — within its grant")
    try:
        atlas_db(action="db_query", sql={"query": INNOCENT_SELECT})
        t.outcome("allow", "executed")
    except GovernanceDenied as exc:
        t.outcome("deny", str(exc.decision.reason), exc.decision.matched_rule)

    t.call("Atlas · send_email", "one customer reply — Nova may do this, Atlas may not")
    try:
        atlas_email(action="send_email", to="ana@example.com", recipients=1, body="…")
        t.outcome("allow", "sent")
    except GovernanceDenied as exc:
        t.outcome("deny", str(exc.decision.reason), exc.decision.matched_rule)

    t.note(
        "Same tool, same arguments, different agent identity — different verdict. "
        "Both policies inherit the ACME baseline, and under govern()'s default "
        "deny_overrides strategy a baseline deny cannot be overridden."
    )


# ── Act 6 ─────────────────────────────────────────────────────────────────

def act_six(safe_db) -> None:
    t.act(6, "Can you prove what happened?", "Every decision is hash-chained.")

    # Each governed callable keeps its own AuditLog (govern() exposes it via
    # .audit_log but takes no shared-sink argument), so this is Nova's
    # db_query ledger. A real deployment would point every callable at one
    # FileAuditSink.
    log = safe_db.audit_log
    entries = log.query(limit=100)

    from rich.table import Table

    table = Table(box=None, header_style="dim", pad_edge=False)
    table.add_column("#", style="dim", justify="right")
    table.add_column("agent", style="cyan")
    table.add_column("action")
    table.add_column("verdict")
    table.add_column("rule", style="dim")
    table.add_column("hash", style="dim")

    for i, e in enumerate(entries):
        style = "green" if e.outcome == "allow" else "red"
        table.add_row(
            str(i),
            e.agent_did.replace("did:mesh:", ""),
            e.action,
            f"[{style}]{e.outcome}[/]",
            str(e.data.get("rule") or "—"),
            e.entry_hash[:12] + "…",
        )
    t.console.print(table)
    t.console.print()

    ok, err = log.verify_integrity()
    t.console.print(f"  [green]✓ chain verified[/] — {len(entries)} entries, integrity={ok}")
    t.console.print()

    # Now forge the record: flip a denial into an approval, the way someone
    # covering their tracks would.
    t.console.print("  [dim]Someone edits the log to hide the denial…[/]")
    victim = next(e for e in entries if e.outcome == "deny")
    victim.outcome = "allow"
    victim.data["rule"] = "allow-read-queries"

    ok, err = log.verify_integrity()
    if ok:
        # Never reached with the tamper above, but printing a failure we did not
        # observe would make this act the one simulated thing in the demo.
        t.console.print("  [bold yellow]! chain still verifies[/] — the tamper did not take")
    else:
        t.console.print(f"  [bold red]✗ chain verification failed[/] — {err}")
    t.console.print()
    t.note(
        "The forged entry's stored hash no longer matches its contents, and every "
        "later entry chains off it. Tampering is detectable, not preventable — "
        "which is exactly what an auditor needs."
    )


# ── Runner ────────────────────────────────────────────────────────────────

def main() -> None:
    t.console.print()
    t.banner(
        "[bold]Agent Governance Toolkit — live demo[/]\n"
        "[dim]Real package (agent-governance-toolkit 4.1.0). Every verdict below is\n"
        "produced by AGT's policy engine, not by this script.[/]",
    )

    act_one()
    (safe_db,) = act_two()
    act_three(safe_db)
    act_four()
    act_five()
    act_six(safe_db)

    t.console.print()
    t.banner(
        "[bold green]The tools never changed.[/] Governance was applied at the call "
        "boundary:\n"
        "policy in YAML, identity per agent, approval routed to a human, and a\n"
        "tamper-evident record of every decision.",
        style="green",
    )
    t.console.print()


if __name__ == "__main__":
    main()
