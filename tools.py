"""The agent's raw tools — deliberately powerful, completely ungoverned.

Nothing in this file knows that policy exists. That is the whole point:
governance is applied from the outside, at the call boundary, without
touching tool code.
"""

from __future__ import annotations

# ── Fake production state the agent can damage ────────────────────────────

TABLES: dict[str, int] = {"customers": 1284, "tickets": 9310, "invoices": 4002}
OUTBOX: list[dict] = []
EXPORTS: list[dict] = []


def reset() -> None:
    """Restore the fake production estate between acts."""
    TABLES.clear()
    TABLES.update({"customers": 1284, "tickets": 9310, "invoices": 4002})
    OUTBOX.clear()
    EXPORTS.clear()


def _verb(query: str) -> str:
    """Best-effort SQL verb, used only to simulate the tool's side effect."""
    try:
        import sqlglot
        from sqlglot import exp

        stmt = sqlglot.parse(query)[0]
        for kind, name in (
            (exp.Drop, "DROP"),
            (exp.Delete, "DELETE"),
            (exp.Select, "SELECT"),
        ):
            if isinstance(stmt, kind):
                return name
        return type(stmt).__name__.upper()
    except Exception:
        return "UNKNOWN"


# ── Tools ─────────────────────────────────────────────────────────────────


def db_query(*, action: str, sql: dict) -> dict:
    """Run SQL against the production database. No guardrails."""
    query = sql["query"]
    verb = _verb(query)

    if verb == "DROP":
        for table in list(TABLES):
            if table in query.lower():
                rows = TABLES.pop(table)
                return {"verb": verb, "dropped": table, "rows_destroyed": rows}
        return {"verb": verb, "dropped": None, "rows_destroyed": 0}

    if verb == "SELECT":
        return {"verb": verb, "rows": 42}

    return {"verb": verb, "rows_affected": 0}


def send_email(*, action: str, to: str, recipients: int, body: str) -> dict:
    """Send mail on the company's behalf. No guardrails."""
    OUTBOX.append({"to": to, "recipients": recipients, "body": body})
    return {"sent": True, "to": to, "recipients": recipients}


def export_dataset(*, action: str, data: dict, rows: int, destination: str) -> dict:
    """Ship a dataset to an external destination. No guardrails."""
    EXPORTS.append({"destination": destination, "rows": rows, "pii": data})
    return {"exported": True, "destination": destination, "rows": rows}
