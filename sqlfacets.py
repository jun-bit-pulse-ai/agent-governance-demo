"""A replacement SQL facet extractor for AGT.

AGT parses SQL into policy facets so a rule can read `sql.verb` instead of
substring-matching the query text. It ships one extractor
(`agentmesh.governance.protocol_facets._extract_sql_facets`) registered under
the "sql" context key. Two defects in it, both verified against AGT 4.1.0:

1. **It only inspects `statements[0]`.** `sqlglot.parse()` returns a list, and
   the extractor reads element zero. So `SELECT 1; DROP TABLE customers`
   reports `verb=SELECT` and any verb-based rule waves it through — a bypass a
   crude substring denylist would have caught.

2. **It dereferences `sqlglot.exp.AlterTable`,** which sqlglot removed long ago
   (absent in both 25.x and 30.x). ALTER, TRUNCATE and GRANT therefore raise
   `AttributeError` inside the extractor. `FacetRegistry.extract` swallows it,
   so *no* `sql` facet is set at all and every verb rule silently stops
   matching. Under a `default_action: allow` policy — which AGT's own Quick
   Start uses — that fails open.

`default_registry.register` is a documented extension point, so rather than
pin an ancient sqlglot we register a replacement. It reads every statement and
reports the most dangerous verb found across all of them.
"""

from __future__ import annotations

from agentmesh.governance.protocol_facets import default_registry

# Most dangerous first: the verb reported for a multi-statement script is the
# worst one it contains, so appending a SELECT cannot launder a DROP.
BY_SEVERITY = (
    "DROP", "TRUNCATE", "ALTER", "GRANT", "DELETE",
    "UPDATE", "MERGE", "INSERT", "CREATE", "SELECT",
)

# sqlglot expression class names change between releases; normalise to a verb.
_CLASS_TO_VERB = {
    "DROP": "DROP",
    "TRUNCATE": "TRUNCATE", "TRUNCATETABLE": "TRUNCATE",
    "ALTER": "ALTER", "ALTERTABLE": "ALTER", "ALTERCOLUMN": "ALTER",
    "GRANT": "GRANT",
    "DELETE": "DELETE", "UPDATE": "UPDATE", "MERGE": "MERGE",
    "INSERT": "INSERT", "CREATE": "CREATE", "SELECT": "SELECT",
    "UNION": "SELECT", "SUBQUERY": "SELECT", "WITH": "SELECT",
}

_EMPTY = {"verb": "", "verbs": "", "target": "", "tables": "", "statements": 0}
_UNKNOWN = {"verb": "UNKNOWN", "verbs": "UNKNOWN", "target": "", "tables": "", "statements": 0}


def _verb_of(stmt) -> str:
    """Normalise one parsed statement to an uppercase SQL verb."""
    import sqlglot
    from sqlglot import exp

    if isinstance(stmt, exp.Command):
        head = (str(stmt.this) or "").strip().upper().split()
        if head:
            return _CLASS_TO_VERB.get(head[0], head[0])

    name = type(stmt).__name__.upper()
    if name in _CLASS_TO_VERB:
        return _CLASS_TO_VERB[name]
    # Fall back to the leading keyword of the rendered statement.
    try:
        head = stmt.sql().strip().upper().split()
        if head:
            return _CLASS_TO_VERB.get(head[0], head[0])
    except Exception:
        pass
    return name or "UNKNOWN"


def extract_sql_facets(sql_ctx: dict) -> dict:
    """Return facets for every statement in ``sql_ctx["query"]``."""
    query = sql_ctx.get("query", "")
    if not query or not query.strip():
        return dict(_EMPTY)

    try:
        import sqlglot
        from sqlglot import exp
    except ImportError:
        # Fail closed and loudly: an UNKNOWN verb matches no allow rule, and
        # every policy here is deny-by-default.
        return dict(_UNKNOWN)

    try:
        statements = [s for s in sqlglot.parse(query) if s is not None]
    except Exception:
        return dict(_UNKNOWN)
    if not statements:
        return dict(_UNKNOWN)

    verbs = [_verb_of(s) for s in statements]
    worst = next((v for v in BY_SEVERITY if v in verbs), verbs[0])
    tables = [t.name for s in statements for t in s.find_all(exp.Table) if t.name]

    return {
        "verb": worst,
        "verbs": ",".join(verbs),
        "target": tables[0] if tables else "",
        "tables": ",".join(tables),
        "statements": len(statements),
    }


def install() -> None:
    """Replace AGT's built-in sql facet extractor with this one.

    `FacetRegistry.register` only appends to a list and there is no unregister
    or replace, so registering "sql" again leaves AGT's broken extractor in
    place ahead of ours. It would still run first, still raise, and still log a
    traceback on every ALTER, TRUNCATE and GRANT — ours would merely overwrite
    the result afterwards. So we drop the built-in from the private
    `_extractors` list before appending. That private access is the only way to
    do this through the library as it stands; it is called out here rather than
    hidden.
    """
    registry = default_registry
    registry._extractors = [
        (key, fn)
        for key, fn in registry._extractors
        if not (key == "sql" and fn.__module__ == "agentmesh.governance.protocol_facets")
    ]
    registry.register("sql", extract_sql_facets)
