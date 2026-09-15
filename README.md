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

## The browser console

The same engine, driven from a page instead of a script:

```bash
.venv/bin/python ui/server.py      # then open http://127.0.0.1:8765/
```

It runs **the same call down two lanes at once** — one ungoverned, one through
`govern()` — and shows the estate afterwards on both sides. A denial's only
evidence is an absence, so the governed column stamps `±0` on every counter.
Each value on the page names the engine field it came from
(`decision.matched_rule`, `decision.evaluation_ms`, `audit.entryHash`) so a
sceptic can check it against the raw JSON at the foot of the same column.

Two honest limits, stated on the page as well as here:

- **The approver is a stand-in.** The `require_approval` *verdict* is the
  engine's, but there is no approval endpoint — who signs off is decided
  server-side. The page labels it rather than implying a person was asked.
- **The audit drawer shows one chain.** Per finding 7, `govern()` gives each
  callable its own `AuditLog` and takes no shared-sink argument.

Everything else on the page is a real verdict from a real evaluation. The
console calls `sqlfacets.install()` for the same reason `demo.py` does; without
it the governed lane would allow destructive SQL the terminal demo denies.

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
| 5 | One policy file, one query, two `agent_id`s → different verdict. Identity is self-asserted; see finding 6. |
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

Regenerating them needs `pip install -r requirements-dev.txt`. The PNG step shells
out to macOS `qlmanage`, so elsewhere `capture.py` writes the SVGs and skips the
PNGs. One caveat on "re-running changes the pictures": Act 6 prints audit-entry
hashes, which include a timestamp, so `06-audit.*` differs on every capture run
whether or not anything changed.

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

Nova's line above is her full grant: read, routine mail, approval-gated bulk
mail, and bounded anonymised export. `analytics-agent.yaml` is a second scoped
example, exercised by `check_policy.py` rather than by the demo.

```text
```

Both policies set `default_action: deny`, so anything not explicitly granted is
refused. That is not enough on its own. A condition naming a field the caller
never supplies evaluates `False`, so a *permissive* rule with only upper bounds —
`recipients.value < 50` — is satisfied by a missing, null or negative value and
grants the call before the deny default is ever consulted. Every allow rule here
therefore asserts what it needs (`recipients.value >= 1 and ... < 50`), and the
export rule requires a positive `data.anonymised` rather than the mere absence of
`data.contains_pii`. `check_policy.py` has a regression case for each.

Worth noticing which facets can be trusted. `sql.verb` is *derived* from the
payload by parsing it, so a caller cannot lie about it. `contains_pii`, `rows` and
`recipients` are *asserted* by the caller and reflected verbatim into the context
(`govern.py:364-386`). A policy is only as honest as the facts it reads. Act 5's denial (`No matching rules, using default`) is that fail-closed
default doing its job.

## Findings from building this

Eight defects in AGT 4.1.0, each reproduced against the installed package. Nothing
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

Both halves land on AGT's own Quick Start policy. Copied verbatim from upstream
`README.md` lines 97-110 and linted:

```
$ agt lint-policy policy.yaml
policy.yaml:1: error: Missing required field 'version'
policy.yaml:12: error: Rule 'require-approval-for-send': unknown action 'require_approval'
2 error(s) found.                                            # exit 1
```

The runtime loads that same file without complaint (`version` defaults to `"1.0"`,
`policy.py:214`).

The linter is also blind in the other direction. All of its condition checking is
gated on `isinstance(condition, dict)` (`lint_policy.py:403`), and every condition
in this repo — and in upstream's Quick Start — is a **string**. So no facet name,
operator or literal is ever inspected:

```
$ agt lint-policy nonsense.yaml     # condition: "sql.vrb in ['DROP'] AND totally.made.up.field == 'zzz'"
No issues found.                                             # exit 0
```

It rejects a valid policy and accepts a meaningless one. That asymmetry is why
this repo tests policies by evaluating them ([`check_policy.py`](check_policy.py))
rather than by linting them.

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

### 8. `warn` and `log` deny, and the code says they shouldn't

`PolicyDecision.allowed` is computed as `allowed=(rule.action == "allow")`
(`policy.py:620`). Everything that is not literally `allow` blocks —
`require_approval` is special-cased in `govern.py`, but `warn` and `log` are not.
A rule written to flag without blocking silently blocks:

```
action: warn   -> DENIED
action: log    -> DENIED
action: allow  -> tool ran
```

Twelve lines above, the code builds this rule's own reason string:

```python
f"Warning from policy '{policy.name}': ... Action allowed but logged for review."
```

The message says allowed; the verdict is denied. This is more severe than finding 1,
because it fails in production rather than in CI, and the intent is unambiguous.

Findings 2-4 are why this repo ships its own extractor rather than pinning an
ancient sqlglot. Treat the runtime as the source of truth; this demo does.

## Files

- `tools.py` — the raw, ungoverned tools. Knows nothing about policy.
- `policies/` — baseline plus one policy per agent.
- `demo.py` — the six acts.
- `theatre.py` — terminal presentation only, no governance logic.
- `sqlfacets.py` — a replacement SQL facet extractor; see findings 2-4.
- `check_policy.py` — condition-coverage test for the policies; see finding 5.
- `ui/server.py` — stdlib-only HTTP server; the console's real verdicts.
- `ui/static/index.html` — the console page; one file, no framework, no build.
- `docs/ARCHITECTURE.md` — design for governing a multi-agent build.
- `docs/PARALLEL_AGENT_PLAN.md` — how several agents would build this repo.
- `capture.py` — re-renders the README images from a real run.
- `docs/images/` — generated; do not hand-edit.
