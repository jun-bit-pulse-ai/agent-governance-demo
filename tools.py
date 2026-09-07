"""The agent's raw tools — deliberately powerful, completely ungoverned.

Nothing in this file knows that policy exists. That is the whole point:
governance is applied from the outside, at the call boundary, without
touching tool code.
"""

from __future__ import annotations

from sqlfacets import extract_sql_facets

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


def db_query(*, action: str, sql: dict) -> dict:
    """Run SQL against the production database. No guardrails.

    The verb and target come from the same AST parse the policy engine uses
    (sqlfacets), so the simulated side effect can never disagree with the
    verdict — and the return shape is uniform, so a caller never has to guess
    which keys are present.
    """
    facets = extract_sql_facets(sql)
    verb, target = facets["verb"], facets["target"]
    result = {"verb": verb, "dropped": None, "rows_destroyed": 0, "rows": 0}

    if verb == "DROP" and target in TABLES:
        result["dropped"] = target
        result["rows_destroyed"] = TABLES.pop(target)
    elif verb == "SELECT":
        result["rows"] = 42
    return result


def send_email(*, action: str, to: str, recipients: int, body: str) -> dict:
    """Send mail on the company's behalf. No guardrails."""
    OUTBOX.append({"to": to, "recipients": recipients, "body": body})
    return {"sent": True, "to": to, "recipients": recipients}


def export_dataset(*, action: str, data: dict, rows: int, destination: str) -> dict:
    """Ship a dataset to an external destination. No guardrails."""
    EXPORTS.append({"destination": destination, "rows": rows, "pii": data})
    return {"exported": True, "destination": destination, "rows": rows}
