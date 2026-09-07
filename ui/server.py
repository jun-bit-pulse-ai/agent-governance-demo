"""A browser console for the AGT demo — stdlib HTTP only, real verdicts only.

This is a thin HTTP façade over exactly what `demo.py` does: `tools.py`'s raw
callables wrapped by `agentmesh.governance.govern()` against the YAML in
`policies/`. Nothing here decides anything. Every verdict this server reports
was produced by AGT's policy engine during a real, enforced call. If a verdict
cannot be obtained honestly, the endpoint returns an error instead of a guess.

Run it:

    .venv/bin/python ui/server.py

Dependencies: Python standard library + the already-installed `agentmesh`.
No web framework. `requirements.txt` is unchanged.


HOW THE HONEST BITS WORK
────────────────────────

Three things the contract asks for are not handed to a caller of `govern()`.
Each is obtained by observing the real enforcement path, never by re-deciding:

1. THE DECISION ON AN ALLOW.  `GovernedCallable.__call__` puts the
   `PolicyDecision` in `GovernanceDenied` only when it denies; on allow it
   returns the tool result and drops the decision on the floor. Rather than
   evaluate the policy a second time (two evaluations can disagree, and a
   second one would be a *different* verdict from the one that was enforced),
   `_install_decision_recorder()` wraps the engine's `evaluate` with a
   recorder that calls the real `PolicyEngine.evaluate`, stashes the exact
   `PolicyDecision` object it returned, and returns it unmodified. The gate
   is untouched: `GovernedCallable` still evaluates once and still decides.
   We are reading the decision it made, not making one.

   Because the recorder sees the decision *before* `_handle_approval()`
   rewrites it, `require_approval` stays visible as a verdict in its own
   right — which is what the contract's `verdict` field wants — while
   `allowed` reports what enforcement actually did (did the tool run?).

2. THE SQL FACETS.  `GovernedCallable._build_context()` stores dict kwargs in
   the evaluation context *by reference*, and the engine's
   `extract_protocol_facets()` mutates that sub-dict in place. So the
   `{"query": ...}` dict this module passes as the `sql=` kwarg comes back
   carrying the engine's own `verb`/`target`/`tables` — the very facets the
   rule was evaluated against. We read them off the dict we handed in.

3. THE SHARED AUDIT CHAIN.  `govern()` gives every callable a private
   `AuditLog()` and takes no shared-sink argument (`demo.py`'s Act 6 note
   says as much), so six governed callables would mean six one-entry chains.
   `Session._build()` therefore assigns the private attribute
   `gc._audit = self.audit` immediately after construction, replacing the
   log `GovernedCallable.__init__` just made. That is the one private
   attribute this file writes to, and it is stated here rather than hidden.
   The public `.audit_log` property is read-only, and `AuditLog` has a
   `sink=` argument but a sink only mirrors entries outward — it would not
   give us one hash chain. A production deployment would point every
   callable at one `FileAuditSink` instead.

The approval handler is the same stand-in `demo.py` Act 4 uses (a security-team
rota that signs off scoped sends and refuses campaign-sized ones). The verdict
that *routes* to it — `require_approval` — is the engine's; only the human's
answer is simulated, exactly as in the terminal demo.

Ungoverned calls (`"governed": false`) invoke the raw function out of
`tools.py` directly. No policy engine is consulted, so the response carries a
null verdict rather than a fabricated one. That is the before/after.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

warnings.filterwarnings("ignore", category=DeprecationWarning)

# ── Paths ────────────────────────────────────────────────────────────────

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # the agt-demo checkout
STATIC_DIR = os.path.join(HERE, "static")
INDEX_HTML = os.path.join(STATIC_DIR, "index.html")
POLICY_DIR = os.path.join(ROOT, "policies")

# Import tools.py / agentmesh the same way demo.py does, from the repo root.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agentmesh.governance import (  # noqa: E402
    ApprovalDecision,
    AuditLog,
    CallbackApproval,
    GovernanceDenied,
    PolicyDecision,
    govern,
)

import tools  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765

# ── The estate, the agents, the tools ────────────────────────────────────

AGENTS: list[dict[str, str]] = [
    {
        "id": "did:mesh:support-nova",
        "label": "Nova · support",
        "policy": "support-agent",
    },
    {
        "id": "did:mesh:analytics-atlas",
        "label": "Atlas · analytics",
        "policy": "analytics-agent",
    },
]

# Exactly the shape the frontend contract specifies.
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "db_query",
        "args": [{"name": "sql", "type": "sql", "label": "SQL"}],
    },
    {
        "name": "send_email",
        "args": [
            {"name": "to", "type": "string"},
            {"name": "recipients", "type": "int"},
            {"name": "body", "type": "string"},
        ],
    },
    {
        "name": "export_dataset",
        "args": [
            {"name": "destination", "type": "string"},
            {"name": "rows", "type": "int"},
            {"name": "contains_pii", "type": "bool"},
        ],
    },
]

RAW_TOOLS: dict[str, Callable[..., dict]] = {
    "db_query": tools.db_query,
    "send_email": tools.send_email,
    "export_dataset": tools.export_dataset,
}

AGENTS_BY_ID = {a["id"]: a for a in AGENTS}


class BadRequest(Exception):
    """A client-side problem — rendered as 400 with {"error": ...}."""


# ── Argument marshalling ─────────────────────────────────────────────────
#
# The UI sends flat args (`sql`, `rows`, `contains_pii`). tools.py and the
# policy rules want the nested shapes demo.py uses: sql={"query": ...} so the
# engine's SQL facet extractor finds it, and data={"contains_pii": ...} so
# `data.contains_pii` resolves. Same call, same kwargs, same policy view.


def _need(args: dict, name: str) -> Any:
    if name not in args:
        raise BadRequest(f"missing argument '{name}'")
    return args[name]


def _as_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise BadRequest(f"argument '{name}' must be an integer")
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        raise BadRequest(f"argument '{name}' must be an integer, got {value!r}")


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise BadRequest(f"argument '{name}' must be a boolean, got {value!r}")


def _as_str(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise BadRequest(f"argument '{name}' must be a string")
    return value


def build_kwargs(tool: str, args: dict) -> dict:
    """Turn the UI's flat args into the kwargs demo.py passes."""
    if not isinstance(args, dict):
        raise BadRequest("'args' must be an object")

    if tool == "db_query":
        # This dict is handed straight into the governed call; the engine
        # mutates it in place with the parsed facets, which is how we report
        # verb/target/tables without parsing anything ourselves.
        return {
            "action": "db_query",
            "sql": {"query": _as_str(_need(args, "sql"), "sql")},
        }

    if tool == "send_email":
        return {
            "action": "send_email",
            "to": _as_str(_need(args, "to"), "to"),
            "recipients": _as_int(_need(args, "recipients"), "recipients"),
            "body": _as_str(args.get("body", ""), "body"),
        }

    if tool == "export_dataset":
        return {
            "action": "export_dataset",
            "data": {
                "contains_pii": _as_bool(
                    _need(args, "contains_pii"), "contains_pii"
                )
            },
            "rows": _as_int(_need(args, "rows"), "rows"),
            "destination": _as_str(_need(args, "destination"), "destination"),
        }

    raise BadRequest(f"unknown tool '{tool}'")


def policy_path(name: str) -> str:
    return os.path.join(POLICY_DIR, f"{name}.yaml")


# ── The governed session ─────────────────────────────────────────────────


class Session:
    """One AuditLog and one governed callable per (agent, tool).

    Rebuilt wholesale by /api/reset so the hash chain starts clean.
    """

    def __init__(self) -> None:
        self.audit = AuditLog()
        self.callables: dict[tuple[str, str], Any] = {}
        # Filled during a single governed call, read straight after it.
        self._decisions: list[PolicyDecision] = []
        self._approvals: list[dict[str, Any]] = []
        self._build()

    # -- construction --------------------------------------------------

    def _build(self) -> None:
        for agent in AGENTS:
            path = policy_path(agent["policy"])
            if not os.path.isfile(path):
                raise RuntimeError(f"policy file not found: {path}")
            for tool_name, fn in RAW_TOOLS.items():
                governed = govern(
                    fn,
                    policy=path,
                    agent_id=agent["id"],
                    approval_handler=CallbackApproval(self._security_team),
                )
                # See module docstring, note 3: the ONE private attribute this
                # file writes. govern() hands each callable its own AuditLog
                # and offers no shared-sink argument, so without this the six
                # callables keep six separate one-entry chains and /api/audit
                # could not show a real chain. Replaced immediately after
                # construction, before any call is made.
                governed._audit = self.audit
                self._install_decision_recorder(governed)
                self.callables[(agent["id"], tool_name)] = governed

    def _install_decision_recorder(self, governed: Any) -> None:
        """Observe (never replace) the decision the gate actually enforced.

        `governed.engine` is the public accessor for the PolicyEngine. We
        shadow its bound `evaluate` with a wrapper that delegates to the real
        method and records the returned PolicyDecision. The engine still
        decides; enforcement still flows through GovernedCallable.__call__;
        the policy is still evaluated exactly once per call.
        """
        engine = governed.engine
        real_evaluate = engine.evaluate

        def recording_evaluate(agent_did, context, stage="pre_tool"):
            decision = real_evaluate(agent_did, context, stage)
            self._decisions.append(decision)
            return decision

        engine.evaluate = recording_evaluate

    # -- the human on the rota (identical to demo.py's Act 4 stand-in) ---

    def _security_team(self, request) -> ApprovalDecision:
        recipients = request.context.get("recipients", {}).get("value", 0)
        if recipients > 1000:
            decision = ApprovalDecision(
                approved=False,
                approver="security-team:priya",
                reason="Campaign-sized send from an agent — refused pending review",
            )
        else:
            decision = ApprovalDecision(
                approved=True,
                approver="security-team:priya",
                reason="Scoped announcement, signed off",
            )
        self._approvals.append(
            {
                "requested": True,
                "approver": decision.approver,
                "approved": decision.approved,
                "reason": decision.reason,
            }
        )
        return decision

    # -- audit ----------------------------------------------------------

    def entries(self) -> list:
        """Live AuditEntry objects, oldest first (mutating one forges it)."""
        return self.audit.query(limit=1_000_000)


# ── Estate snapshots ─────────────────────────────────────────────────────


def estate() -> dict[str, Any]:
    return {
        "tables": dict(tools.TABLES),
        "outbox": len(tools.OUTBOX),
        "exports": len(tools.EXPORTS),
    }


def state_payload() -> dict[str, Any]:
    payload = {"agents": AGENTS, "tools": TOOL_SPECS}
    payload.update(estate())
    return payload


def audit_payload(session: Session) -> dict[str, Any]:
    entries = session.entries()
    ok, err = session.audit.verify_integrity()
    return {
        "entries": [
            {
                "index": i,
                "agent": e.agent_did,
                "action": e.action,
                "outcome": e.outcome,
                "rule": (e.data.get("rule") or None),
                "hash": e.entry_hash,
                "previousHash": e.previous_hash,
                "timestamp": e.timestamp.isoformat(),
            }
            for i, e in enumerate(entries)
        ],
        "integrity": {"ok": bool(ok), "error": err},
    }


# ── The call endpoint ────────────────────────────────────────────────────


def do_call(session: Session, body: dict) -> dict[str, Any]:
    agent_id = body.get("agent")
    tool_name = body.get("tool")
    governed_flag = body.get("governed", True)

    if agent_id not in AGENTS_BY_ID:
        raise BadRequest(f"unknown agent '{agent_id}'")
    if tool_name not in RAW_TOOLS:
        raise BadRequest(f"unknown tool '{tool_name}'")
    if not isinstance(governed_flag, bool):
        raise BadRequest("'governed' must be a boolean")

    kwargs = build_kwargs(tool_name, body.get("args", {}))

    response: dict[str, Any] = {
        "governed": governed_flag,
        "verdict": None,
        "allowed": False,
        "matchedRule": None,
        "policyName": None,
        "reason": None,
        "evaluationMs": None,
        "facets": None,
        "approval": None,
        "result": None,
        "error": None,
        "audit": None,
    }

    # ── Ungoverned: the raw tool from tools.py, no gate in front of it ──
    if not governed_flag:
        result = RAW_TOOLS[tool_name](**kwargs)
        response["allowed"] = True          # nothing was there to say no
        response["result"] = result
        response.update(estate())
        return response

    # ── Governed: the real gate ────────────────────────────────────────
    session._decisions.clear()
    session._approvals.clear()
    before = len(session.entries())

    denial: Optional[PolicyDecision] = None
    result = None
    try:
        result = session.callables[(agent_id, tool_name)](**kwargs)
        allowed = True
    except GovernanceDenied as exc:
        denial = exc.decision
        allowed = False

    if not session._decisions:
        # The gate ran but we never saw a decision — refuse to invent one.
        raise RuntimeError(
            "no PolicyDecision was recorded for this call; refusing to report "
            "a verdict that did not come from the policy engine"
        )

    decision = session._decisions[-1]        # pre-approval, as enforced

    response["verdict"] = decision.action
    response["allowed"] = allowed
    response["matchedRule"] = decision.matched_rule or None
    response["policyName"] = decision.policy_name or None
    # On a denial the enforced reason is the one carried by the exception —
    # for a rejected approval that is the approver's words, not the rule's.
    response["reason"] = (denial.reason if denial is not None else decision.reason) or None
    response["evaluationMs"] = (
        round(decision.evaluation_ms, 3) if decision.evaluation_ms is not None else None
    )
    response["result"] = result

    # Facets, read back off the dict the engine mutated in place.
    sql_ctx = kwargs.get("sql")
    if isinstance(sql_ctx, dict) and "verb" in sql_ctx:
        response["facets"] = {
            "sql": {
                "verb": sql_ctx.get("verb", ""),
                "target": sql_ctx.get("target", ""),
                "tables": sql_ctx.get("tables", ""),
            }
        }

    if session._approvals:
        response["approval"] = session._approvals[-1]

    # The policy_evaluation entry this call just appended to the chain.
    new_entries = session.entries()[before:]
    written = [e for e in new_entries if e.event_type == "policy_evaluation"]
    if written:
        entry = written[-1]
        response["audit"] = {
            "entryId": entry.entry_id,
            "hash": entry.entry_hash,
            "previousHash": entry.previous_hash,
            "outcome": entry.outcome,
        }

    response.update(estate())
    return response


def do_tamper(session: Session, body: dict) -> dict[str, Any]:
    """Forge one entry in the live chain, exactly as demo.py's Act 6 does."""
    index = body.get("index")
    outcome = body.get("outcome")

    if not isinstance(index, int) or isinstance(index, bool):
        raise BadRequest("'index' must be an integer")
    if not isinstance(outcome, str) or not outcome:
        raise BadRequest("'outcome' must be a non-empty string")

    entries = session.entries()
    if not entries:
        raise BadRequest("the audit chain is empty — make a governed call first")
    if not 0 <= index < len(entries):
        raise BadRequest(
            f"index {index} out of range (chain has {len(entries)} entries)"
        )

    # Edit the record in place and leave entry_hash alone — that is the whole
    # point: the stored hash no longer matches the contents, and every later
    # entry chains off it.
    entries[index].outcome = outcome
    return audit_payload(session)


def do_policy(name: Optional[str]) -> dict[str, Any]:
    if not name:
        raise BadRequest("missing 'name' query parameter")
    # Whitelist by what is actually in policies/ — no path traversal.
    available = sorted(
        os.path.splitext(f)[0]
        for f in os.listdir(POLICY_DIR)
        if f.endswith(".yaml")
    )
    if name not in available:
        raise BadRequest(f"unknown policy '{name}' (have: {', '.join(available)})")
    with open(policy_path(name), "r", encoding="utf-8") as fh:
        return {"name": name, "yaml": fh.read()}


# ── HTTP ─────────────────────────────────────────────────────────────────

MISSING_INDEX_HTML = """<!doctype html>
<meta charset="utf-8"><title>Console page not built</title>
<body style="background:#0d1117;color:#c9d1d9;font:14px ui-monospace,monospace;padding:3rem">
<h1 style="color:#f85149">503 — console page not found</h1>
<p>The API is up, but the page it serves was not found on disk:</p>
<pre>{path}</pre>
<p>The server reads that file at request time, so create it and reload —
no restart needed. The JSON endpoints
(<code>/api/state</code>, <code>/api/call</code>, <code>/api/audit</code>)
are already answering.</p>
</body>
"""

SESSION = Session()
LOCK = threading.RLock()          # one fake estate + one chain, many requests


class Handler(BaseHTTPRequestHandler):
    server_version = "AGTDemoConsole/1.0"
    protocol_version = "HTTP/1.1"

    # -- plumbing -------------------------------------------------------

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _error(self, code: int, message: str) -> None:
        self._json({"error": message}, code)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequest(f"invalid JSON body: {exc}")
        if not isinstance(parsed, dict):
            raise BadRequest("request body must be a JSON object")
        return parsed

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("  %s\n" % (fmt % args))

    # -- routes ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        path = url.path
        try:
            if path == "/":
                return self._serve_index()
            if path == "/api/state":
                with LOCK:
                    return self._json(state_payload())
            if path == "/api/audit":
                with LOCK:
                    return self._json(audit_payload(SESSION))
            if path == "/api/policy":
                params = parse_qs(url.query)
                name = (params.get("name") or [None])[0]
                return self._json(do_policy(name))
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):])
            return self._error(404, f"no such endpoint: {path}")
        except BadRequest as exc:
            self._error(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._error(500, f"{type(exc).__name__}: {exc}")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = self._read_body()
            if path == "/api/call":
                with LOCK:
                    return self._json(do_call(SESSION, body))
            if path == "/api/audit/tamper":
                with LOCK:
                    return self._json(do_tamper(SESSION, body))
            if path == "/api/reset":
                with LOCK:
                    return self._json(self._reset())
            return self._error(404, f"no such endpoint: {path}")
        except BadRequest as exc:
            self._error(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self._error(500, f"{type(exc).__name__}: {exc}")

    # -- handlers -------------------------------------------------------

    def _reset(self) -> dict:
        global SESSION
        tools.reset()
        SESSION = Session()          # fresh AuditLog, fresh governed callables
        return state_payload()

    def _serve_index(self) -> None:
        # Read at request time: the page is owned by another author and may
        # appear or change while this process is running.
        try:
            with open(INDEX_HTML, "rb") as fh:
                body = fh.read()
        except FileNotFoundError:
            page = MISSING_INDEX_HTML.format(path=INDEX_HTML)
            return self._send(503, page.encode("utf-8"), "text/html; charset=utf-8")
        self._send(200, body, "text/html; charset=utf-8")

    def _serve_static(self, relative: str) -> None:
        """Courtesy: serve sibling assets out of ui/static/ if any exist."""
        target = os.path.normpath(os.path.join(STATIC_DIR, relative))
        if not target.startswith(STATIC_DIR + os.sep) or not os.path.isfile(target):
            return self._error(404, f"no such file: /static/{relative}")
        types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".woff2": "font/woff2",
        }
        ext = os.path.splitext(target)[1].lower()
        with open(target, "rb") as fh:
            self._send(200, fh.read(), types.get(ext, "application/octet-stream"))


def main() -> None:
    port = int(os.environ.get("AGT_DEMO_PORT", PORT))
    httpd = ThreadingHTTPServer((HOST, port), Handler)
    url = f"http://{HOST}:{port}/"
    print(
        f"\n  Agent Governance Toolkit — console\n"
        f"  {url}\n"
        f"  Every verdict served here comes from AGT's policy engine.\n"
        f"  Ctrl-C to stop.\n",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  stopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
