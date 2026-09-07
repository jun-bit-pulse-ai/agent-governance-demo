# Building this repo with several agents

The operational plan for the next increment of `agt-demo`: who works on what, in
what order, what is frozen before anyone starts, and what actually stops an agent
standing on another agent's work.

Every rule below is taken from a real incident in another project. That project
is `crunched-kiss` — an Excel add-in built by three agents in parallel on one
trunk — and its evidence lives in three documents in that repository:
`docs/AGENT_WORKFLOW.md` (what broke and the playbook that worked),
`docs/TEAM_PROMPT.md` (the prompt actually sent to the three agents) and
`docs/FINAL_PLAN.md` (the spec they built from). Sections are cited throughout as
`AGENT_WORKFLOW 4.1`, `TEAM_PROMPT rule 2`, and so on. Where this plan departs
from that report, it says so and says why.

One thing to be clear about at the top. This repository demonstrates the Agent
Governance Toolkit. AGT is **not** the thing governing this build. AGT's
`govern()` intercepts a Python call to a callable you wrapped
(`agentmesh/governance/govern.py:240` is `return self._fn(*args, **kwargs)`); it
does not see an agent's `Edit`, `Write` or `Bash`. Using AGT to police this repo
would mean writing a facet extractor in Python and then reviewing the extractor
instead of the policy. AGT is the subject of this build. Git and CI are its
governors.

---

## 1. Is parallelism justified here at all

Do this arithmetic before writing any issues. If it comes out badly, run one
agent and stop reading.

```
$ git ls-files | grep -v docs/images | wc -l          12
$ git ls-files '*.py' '*.yaml' | xargs wc -l         630
```

630 lines of Python and YAML across 12 non-image files. For comparison,
`crunched-kiss` fanned out to three agents over 79 tracked files and 4,495 lines
of TypeScript and Python — roughly seven times this. One of its three lanes
(TEAM_PROMPT section B, issue #5) touched no code at all.

So the honest position is: this repo supports **two implementation lanes and one
documentation lane**, not three implementation lanes. The coupling graph decides
it, not the head count.

| Module | Imports | Imported by | Lines |
|---|---|---|---|
| `tools.py` | nothing | `demo.py` | 74 |
| `theatre.py` | `rich` | `demo.py`, `capture.py` | 62 |
| `demo.py` | `agentmesh`, `theatre`, `tools` | `capture.py` | 308 |
| `capture.py` | `rich`, `theatre`, `demo` | nothing | 106 |
| `policies/*.yaml` | `baseline.yaml` (via `extends`) | `demo.py`, by path | 80 |

Two genuine leaves (`tools.py`, `theatre.py`), one hub (`demo.py`), one consumer
sitting above the hub (`capture.py`), and one data surface (`policies/`). That is
three islands, and only if `theatre.py`'s signatures are frozen first — see
section 3.

**The split must be genuine ownership islands, not busywork to justify a number.**
Two decompositions were considered and rejected:

- **Split `demo.py` by act.** Tempting: six acts, six agents. Wrong: the acts
  share module-global state through `tools.reset()`, `act_two` returns `safe_db`
  which `act_three` and `act_six` consume, and one `main()` sequences them. Six
  agents on six acts is six agents on one 308-line file — the exact shape of
  AGENT_WORKFLOW 4.1.
- **Give `theatre.py` and `capture.py` to a separate agent from `README.md`.**
  Wrong for a different reason: it manufactures a third lane out of 168 lines
  that one agent finishes in twenty minutes, and it puts a two-sided contract
  (`theatre`'s API, `demo`'s act names) across two owners. AGENT_WORKFLOW 5.1.3
  calls both sides of a contract "the single most conflict-prone thing in a
  parallel build".

If you have only two agents, fold lane C into the integrator's integration pass
(stage 7). The plan degrades cleanly. It does not degrade cleanly if you add a
fourth.

---

## 2. The pipeline

Eight stages, adapted from AGENT_WORKFLOW section 2. The ordering matters more
than any individual tool.

```
1 survey  →  2 freeze the contracts  →  3 write issues that name files
                                                  │
                          ┌───────────────────────┼───────────────────────┐
                       4 agent A                agent B                agent C
                       policies              demo runtime            docs + theatre
                          └───────────────────────┴───────────────────────┘
                                                  │
   5 merge in order  →  6 standing QA  →  7 integration: regenerate images, live run
                                                  │
                                        8 cleanup: branches, PRs, issues
```

Two differences from the field report's nine stages, both deliberate.

Its stage 6 was a **design pass** on a user interface. This repo's user interface
is a terminal transcript, and the design pass is folded into lane C, which owns
`theatre.py`. Its stage 8 was **hardening** — accessibility and security on a web
pane. There is no pane here; the equivalent hardening is the contract test in
stage 2, which is why it moves to the front.

---

## 3. What is frozen before fan-out

Per AGENT_WORKFLOW 5.1.3: *"Freeze the contract first... land them before fanning
out."* In `crunched-kiss` the frozen thing was `backend/app/tools.py` (schemas)
and `dispatchExcelTool` (executors). Here there are four, and they must all be
committed and pushed to `main` before the first agent branches.

### 3.1 `policies/baseline.yaml`

Both child policies carry `extends: ["baseline.yaml"]`. It is this repo's
tool-name contract. Integrator-owned; it does not appear in `OWNERS`, which by
the unlisted-means-integrator rule (section 4.2) makes it uncommittable by any
agent.

There is an invariant here that is not obvious and must be pinned by test rather
than trusted. AGT's `extends` protection filters child rules by **name**:
`policy.py:363-374` builds `parent_deny_names` from the parent's deny rules and
skips a child rule only if its name collides. A child rule with a *different*
name and a higher priority beats a parent deny under `priority_first_match` —
which is `PolicyEngine.__init__`'s own default (`policy.py:491`). What actually
holds the baseline is `govern()`'s separate default of `deny_overrides`
(`govern.py:117`). That is a configuration default, not an inherited guarantee.
Assert it.

### 3.2 The facet-name set

The conditions in the YAML read exactly five paths out of the context dict that
`govern()` builds: `sql.verb`, `action.type`, `data.contains_pii`,
`recipients.value`, `rows.value`. This is the second, invisible contract, and it
binds the policy files (lane A) to the demo code (lane B).

Rename one and the rule silently stops firing. Reproduced on a copy of
`baseline.yaml`: changing `data.contains_pii` to `data.has_pii` turns a
12,000-row PII export from

```
[DENY] Personal data may not leave the platform via an agent
       matched rule: block-pii-export
```

into

```
[ALLOW] exported
```

No error, no warning, no audit entry. `default_action: deny` does not save you,
because the child's `allow-anonymised-export` rule catches the call at
`rows.value <= 50000`. This is AGENT_WORKFLOW 5.6's silent-failure class —
"agents drop conventions under context pressure, and the failure is silent" —
living inside the enforcement layer itself.

The grammar is narrower than it looks and every way of missing it fails to
`False` rather than to an error. Verified against `PolicyRule.evaluate` with
`rows.value = 100` in context:

| Written | Evaluates | Note |
|---|---|---|
| `rows.value >= 100` | `True` | ranges work |
| `rows.value == 100` | `False` | numeric equality is impossible |
| `rows.value == '100'` | `False` | quoted numeric equality too — int vs str |
| `(action.type == 'write_file')` | `False` | every regex is `re.match`-anchored; a leading paren never matches |
| `not data.contains_pii` | `False` | there is no negation |
| `sql.verb startswith 'SEL'` | `False` | no startswith, glob, regex or field-to-field compare |
| `data.has_pii` | `False` | a misspelt path is just absent |

`policy.py:111-183` is the whole grammar. There is no way to assert that a
boolean facet is *false*.

### 3.3 `theatre.py`'s public API, and `demo.py`'s act names

`demo.py` calls into `theatre` at roughly fifty sites (`t.outcome` twelve times,
`t.console` eleven, `t.call` nine, `t.act`, `t.note`, `t.estate`, `t.banner`).
`capture.py` reaches the other way: it rebinds `theatre.console` to a recording
console and then calls `demo.act_one` through `demo.act_six` by name.

That is a circular contract between lane B and lane C. Freeze it as a list of
names and signatures on `main` before fan-out:

```
theatre.console
theatre.act(number: int, title: str, subtitle: str) -> None
theatre.call(label: str, detail: str) -> None
theatre.outcome(verdict: str, message: str, rule: str | None = None) -> None
theatre.note(text: str) -> None
theatre.banner(text: str, style: str = "cyan") -> None
theatre.estate(tables: dict[str, int], caption: str) -> None
demo.act_one() ... demo.act_six()   # act_two returns (safe_db,)
```

Lane C may change what those functions render. It may not change their names,
parameters or return shapes. Lane B may call them and may not edit them. If
either side needs the contract widened, it goes to the integrator, on `main`,
per TEAM_PROMPT rule 6.

### 3.4 The harness: `OWNERS`, `check_contracts.py`, `scripts/`, `.github/`

These must be on `main` first for a mechanical reason: the ownership check reads
the manifest from `origin/main:OWNERS`, not from the working tree. An `OWNERS`
file that exists only in a branch enforces nothing, and one read from the working
tree lets an agent widen its own grant with a local edit.

**A note on naming.** The earlier design for this work proposed two files, a
`check_policy.py` for the policy rules and something later for `OWNERS`
resolution. That is one file. AGENT_WORKFLOW 5.1.4 asks us to "reject any
addition that increases file count... unless the issue asks for it", so the two
halves live in `check_contracts.py` together.

---

## 4. What actually enforces this, and what only looks like it does

### 4.1 CI is the enforcement layer; git hooks are a fast local signal

The instinct is to put the ownership check in a `pre-commit` hook. Do install one
— it turns a five-minute mistake into a five-second one — but do not count it.
Reproduced failures, all on git 2.50.1:

- `git commit --no-verify` skips it in one flag, and because a hook reading
  `HEAD:OWNERS` reads the agent's own branch tip, a single `--no-verify` commit
  that appends a line to `OWNERS` is a **permanent** privilege escalation: every
  later hooked commit then passes.
- `core.hooksPath` pointed at a directory inside the repo means the agent owns
  the hook file. Overwriting `.githooks/pre-commit` with `exit 0` in the working
  tree — no commit, nothing in the diff — makes the next commit land clean.
- `core.hooksPath` pointed at a missing directory fails **open**, silently. No
  warning; the commit succeeds.
- A clean (non-conflicting) `git merge main` creates its merge commit without
  running `pre-commit` at all.

So: read `OWNERS` from `origin/main`, put `core.hooksPath` at an absolute path
outside every worktree, and treat the local hook as advice. The check that
decides anything runs in CI against the pull request's branch diff, where
`--no-verify` does not reach and the agent cannot edit the runner's copy of the
script.

Identity in CI comes from the branch name, which the agent does not choose
arbitrarily — the issue assigns it and the PR carries it. Fail closed on anything
unrecognised:

```sh
#!/bin/sh
# scripts/check-ownership.sh   — integrator-owned, run in CI
# usage: check-ownership.sh <head-branch>
set -eu
branch="${1:?head branch required}"
case "$branch" in
  agent-a|agent-b|agent-c) actor="$branch" ;;
  integrator/*)            actor="integrator" ;;
  *) echo "no ownership lane for branch '$branch'" >&2; exit 1 ;;
esac

TAB=$(printf '\t')
manifest=$(git show origin/main:OWNERS | sed 's/#.*//')

owner_of() {   # longest matching prefix wins; unlisted means integrator
  path="$1"; best=""; owner="integrator"
  IFS='
'
  for line in $manifest; do
    prefix=${line%%"$TAB"*}
    who=${line#*"$TAB"}
    [ -n "$prefix" ] && [ "$prefix" != "$line" ] || continue
    case "$path" in "$prefix"*)
      if [ ${#prefix} -gt ${#best} ]; then best=$prefix; owner=$who; fi ;;
    esac
  done
  unset IFS
  printf '%s' "$owner"
}

rc=0
IFS='
'
for path in $(git diff --name-only "origin/main...HEAD"); do
  who=$(owner_of "$path")
  if [ "$who" != "$actor" ]; then
    echo "$actor may not change $path (owner: $who)"
    rc=1
  fi
done
exit $rc
```

Run against the `OWNERS` in section 4.2 with a stubbed diff, this gives
`agent-a` exit 0 on `policies/support-agent.yaml`, exit 1 with
`agent-a may not change demo.py (owner: agent-b)` on B's file, exit 0 for
`integrator/freeze` on `policies/baseline.yaml`, and exit 1 with
`no ownership lane for branch 'ck-issue-3'` on an unrecognised branch. Check
those four before you trust it.

Note the `for` loop over a variable rather than `while read` from a pipe. A
`while read` loop on the right of a pipe runs in a subshell in POSIX `sh`, so
`best` and `owner` never escape it and `owner_of` silently returns the default
for every path — a check that passes everything while looking correct. Avoid
`done < <(…)` too: process substitution is bash-only, and git hooks and CI
runners frequently run `sh`, where it is a syntax error that makes the script
exit **0**.

Two details that are not cosmetic.

**Longest prefix wins, not any prefix.** An earlier version of this check granted
on the first matching prefix, and with the manifest in section 4.2 that let
agent-a commit the frozen `policies/baseline.yaml` with exit 0 and no warning.
Same silent-failure class as section 3.2, sitting inside the thing meant to catch
it. `check_contracts.py` must test `OWNERS` resolution, not only policy rules.

**Newline-only `IFS`.** Word-splitting `git diff --name-only` turns
`demo 2.py` into two paths. That is not a hypothetical filename: it is the exact
shape of AGENT_WORKFLOW 4.5's iCloud conflict copies (`FINAL_PLAN 2.md`,
`agent 2.py`). With newline-only `IFS` it parses as one path and is refused.
(`core.quotePath=false` does *not* help here — it only affects non-ASCII names,
where it prevents a false refusal. A name containing a literal newline is quoted
by git regardless and fails closed.)

### 4.2 `OWNERS`

One flat TSV, path prefix to owner. Unlisted means the integrator, which is how
"one owner for installs" (AGENT_WORKFLOW 4.1) is expressed: `requirements.txt`
has no agent owner, therefore no agent can change dependencies.

```
policies/	agent-a
policies/baseline.yaml	integrator
demo.py	agent-b
tools.py	agent-b
README.md	agent-c
theatre.py	agent-c
```

Everything else — `capture.py`, `check_contracts.py`, `scripts/`, `.github/`,
`requirements*.txt`, `OWNERS` itself, `docs/` — belongs to the integrator by
omission.

The first two lines are deliberately the awkward case: agent-a owns the
`policies/` directory *except* for the frozen baseline inside it. That only works
because the longest matching prefix wins. Under an any-prefix implementation
agent-a owns `baseline.yaml` too, silently, and the freeze in section 3.1 is
gone. This pair is the reason `check_contracts.py` has to cover `OWNERS`
resolution and not only policy rules.

### 4.3 Rules that map to a control

| TEAM_PROMPT rule | Where it is enforced | What holds |
|---|---|---|
| 1 — never commit or push to `main` | GitHub branch protection | Server-side. A local `pre-push` hook dies to `--no-verify`. |
| 1 — never force-push | Branch protection, "block force pushes" | Server-side, unbypassable. Force-pushing your *own* branch stays allowed and should. |
| 2 — edit only your exclusive files | CI ownership check on the branch diff | Judges the result, not the request, so it also catches a write made by shell redirect that never touched an `Edit` tool. |
| 3 — no new dependencies | Same check; `requirements.txt` is unlisted | The cleanest half of the rule. Reformatting is a diff-shape property and is not caught. |
| 5 — suites green before push | Required status check | Gates the merge, not the push. |
| 6 — freeze the contract | Same check; `baseline.yaml` unlisted | Ownership only. Drift is a different property and needs `check_contracts.py`. |
| 8 — rebase before merge | "Require branches to be up to date" | Server-side. Does not stop an hour's work on a stale base, only its landing. |
| 10 — every changed file inside your list | Same check, whole branch diff | |

### 4.4 Rules that do not, and are not pretended to

| Rule | Why nothing enforces it |
|---|---|
| 4.4 — never delete another agent's branch | A local hook on `reference-transaction` does refuse `git branch -D`, but `git push origin --delete` succeeds with no flag and hooks silent — and stage 8 cleanup is *remote* cleanup driven off `gh pr list`. Worse, the same hook aborts `git gc` outright, because `git pack-refs` emits an `old=<sha> new=0000…` transaction for every branch in the repo and there is no way to tell that apart from a deletion. **The hook is not in this plan.** Instead: `git config gc.auto 0`, push every agent branch to `origin` on first commit so the work is recoverable from the remote, and restrict deletions on the agent namespace with a server-side ruleset if your plan has them. |
| 4.4 — never discard uncommitted work | `git worktree remove` refuses a dirty tree on its own; `git worktree remove --force` against a peer's worktree succeeds and is unhookable. `man githooks` on git 2.50.1 lists 24 hooks and none fires on worktree removal. |
| 4.4 — the unrecoverable stash | The object was never written to the store. No ref transaction, nothing to intercept. |
| 4.3 — two agents ship the same fix | Argued out of existence at this scale: three exclusive lists over twelve files leave no work item two agents could both pick up. At larger scale this is a distributed-lock problem. |
| 7 — small commits, one concern | A `commit-msg` regex can check the `feat|fix|docs|test: … (#N)` prefix. Nothing judges "one concern". Enforcing the prefix and calling the rule enforced would be theatre. |
| 9 — blocked over 15 minutes | No observable signature. An agent can burn an hour without staging a file. |
| 10 — "then stop" | An agent continuing to work is indistinguishable from an agent working. |
| AGENT_WORKFLOW 5.1.1 — write the architecture yourself | The most important rule in the playbook, and no software touches it. |

`git log` is the record of who did what. It is an audit trail, not a
tamper-evident one: an agent with write access rewrites history as easily as it
edits `OWNERS`. Signed commits plus a protected branch are the fix and are out of
scope for a 630-line demo. AGT's own chain is no better here —
`GovernanceConfig.audit_file` is declared at `govern.py:100` and `:113` and read
nowhere in the package, `govern()` does not accept it, and the log dies at process
exit.

---

## 5. One owner for installs and long-running processes

Straight from AGENT_WORKFLOW 4.1 and 4.2, and the rule this repo most needs,
because it is currently in the failure topology.

```
$ ls -d agt-demo/.venv
ls: agt-demo/.venv: No such file or directory
$ sed -n 1,3p ../.venv/pyvenv.cfg
home = /opt/homebrew/opt/python@3.13/bin
version = 3.13.13
```

There is **one** virtual environment and it sits at the repository's parent,
outside every worktree, shared by anything pointed at it. That is precisely the
shape that produced 4.1 (three parties rebuilding one `node_modules`) and 4.2 (a
venv recreated with an app-bundled Python 3.12 while the packages sat in the
3.14 directory). Fix it before fan-out by giving each worktree its own.

The rules:

1. **One environment per worktree, inside the worktree.** `.gitignore` line 1 is
   already `.venv/`, so nothing further is needed to keep it untracked. Cost
   measured: about 1s to create and 6s to install per agent, 126 MB each. Three
   agents is 21 seconds and roughly 378 MB. That is the whole ceremony budget.
2. **The interpreter is pinned by absolute path and asserted before installing.**
   `setup-agent.sh` uses `/opt/homebrew/opt/python@3.13/bin/python3.13` and dies
   if `sys.version_info[:2] != (3, 13)`. This does not prevent an agent
   hand-rolling a venv with whatever Python it happens to be running; it contains
   the blast radius to that agent's own worktree, which is the actual improvement.
3. **No agent changes `requirements.txt` or `requirements-dev.txt`.** Both are
   unlisted in `OWNERS`. `sqlglot` in particular must stay: without it
   `_extract_sql_facets` returns `verb="UNKNOWN"` with only a `logger.warning`.
   That is not a silent failure — `UNKNOWN` matches no allow rule either, so
   `default_action: deny` then denies the innocent `SELECT` too and Act 3 goes
   visibly wrong in both directions.
4. **The shared checkout belongs to the integrator, and to nobody else.** This is
   the rule with no mechanism behind it, and it is the one that matters most.
   AGENT_WORKFLOW 4.1 did not happen for want of worktrees — `crunched-kiss`
   mandated one per agent in TEAM_PROMPT rule 1 and had nine of them. It happened
   in the *shared checkout*, where the owner ran the dev server and the integrator
   ran `npm ci`. No hook, no CI check and no policy in this plan observes an
   `npm install` or a `pip install` at all. If an agent session is pointed at the
   shared checkout, this plan is not protecting you.
5. **`ui/server.py` runs, if it runs at all, in the integrator's checkout only.**
   It is a long-running process by the same rule.

---

## 6. Worktree setup

`scripts/setup-agent.sh`, integrator-owned, run by the agent itself in stage 4.

```sh
#!/bin/sh
set -eu
name="${1:?agent name, e.g. agent-a}"
root=$(git rev-parse --show-toplevel)
py=/opt/homebrew/opt/python@3.13/bin/python3.13

git -C "$root" worktree add "$root/../agt-$name" -b "$name" origin/main
cd "$root/../agt-$name"

"$py" -m venv .venv
.venv/bin/python -c 'import sys; assert sys.version_info[:2] == (3,13), sys.version'
.venv/bin/pip install -q -r requirements.txt

git config core.hooksPath "$HOME/.agt-demo-hooks"   # absolute, outside the repo
git config gc.auto 0                                # see section 4.4

.venv/bin/python demo.py >/dev/null && echo "baseline green: $name"
```

Each agent runs, once, from the shared checkout:

```bash
scripts/setup-agent.sh agent-a     # or agent-b, agent-c
```

Two things to know about this step.

**Serialise it.** `.git/config.lock` is shared across worktrees, unlike the index.
A concurrent `git worktree add` from two agents fails with "could not lock config
file". Run the three setups one after another, not at once.

**The index-lock incident does not generalise.** AGENT_WORKFLOW 4.6 reports a
stale `.git/index.lock` freezing every agent. Each worktree has its own index at
`.git/worktrees/<name>/index`, and a stale lock in the shared checkout blocks only
the shared checkout. If every agent froze, they were sharing a checkout. Refs and
the object database *are* still shared, so ref-lock contention and `gc` remain
genuine cross-agent hazards — hence `gc.auto 0`.

---

## 7. The issues

TEAM_PROMPT section B style: one block per agent, exclusive files, checkboxes,
one acceptance test. Send each agent only its own block plus the common rules in
sections 4 to 6.

### Agent A — issue #1: the policy surface and its cases

Worktree `../agt-agent-a`, branch `agent-a`.

```bash
scripts/setup-agent.sh agent-a
```

**Exclusive files:** everything under `policies/` **except**
`policies/baseline.yaml` — so `support-agent.yaml`, `analytics-agent.yaml`, and
the new `policies/cases/` directory.

You do not own `policies/baseline.yaml` and you do not own `check_contracts.py`.
Read both before you start. `check_contracts.py` is the frozen test that will
judge your work and you may not edit it.

- [ ] Read section 3.2 of this document first. The condition grammar is narrower
      than it looks and every mistake in it evaluates to `False` rather than
      raising. That is the whole reason your lane exists.
- [ ] Keep `support-agent.yaml`'s four rules and `analytics-agent.yaml`'s one.
      Every condition must reduce to a form the grammar supports: `path ==
      'quoted string'`, `!=`, `in ['a','b']`, `>` `<` `>=` `<=` against a bare
      number, or a bare truthy path, joined with ` and ` / ` or `.
- [ ] Never write a parenthesised condition. Never write an equality against a
      number, quoted or unquoted — use two range clauses. There is no negation,
      so a rule that needs "not PII" must be written as a separate allow rule
      that the deny rule outranks, which is how `allow-anonymised-export` and
      `block-pii-export` already work.
- [ ] `analytics-agent.yaml` stays strictly read-only. Do not add a `send_email`
      or `export_dataset` grant. Act 5 depends on Atlas being refused, and on
      that refusal having `matched_rule: None` with reason "No matching rules,
      using default" — the default firing, not a rule.
- [ ] Add one case file per rule name under `policies/cases/`. Eight rule names
      exist across the three files; `demo.py` currently exercises four. The
      three with no coverage at all are `block-row-deletion`,
      `allow-routine-email` and `allow-anonymised-export`.
- [ ] Every case names a **positive** form (the rule fires) and a **negative**
      form (it does not), and states the expected `matched_rule`, not just the
      verdict. A rule that has silently vanished still produces `deny` via
      `default_action`, for the wrong reason. Where the correct answer is the
      default, pin `matched_rule: null` explicitly.
- [ ] Do not add `agt lint-policy` or `agt test` to anything, and do not "fix"
      the policies to make them pass. Both tools are wrong about these files in
      both directions. `agt lint-policy` exits 1 on `support-agent.yaml` because
      it rejects `require_approval`, the spelling the runtime executes — and it
      exits 0 on a policy using `action: block`, which the runtime refuses to
      load at all (`lint_policy.py:28` knows six action names, `policy.py:58`
      accepts five, and they overlap on two). `agt test` exits 1 without
      evaluating a single case, because it loads a different engine whose
      `condition` is a dict.
- [ ] If you need a change in `baseline.yaml`, do not make it. Write the exact
      diff you want as a comment on your issue and carry on, per TEAM_PROMPT
      rule 2.

**Acceptance.** From the worktree root, `.venv/bin/python check_contracts.py`
exits 0 and reports a positive and a negative case for all eight rule names.
`.venv/bin/python demo.py` still runs all six acts and exits 0. Every file in
your branch diff is inside your exclusive list.

### Agent B — issue #2: the demo runtime and the ungoverned tools

Worktree `../agt-agent-b`, branch `agent-b`.

**Exclusive files:** `demo.py`, `tools.py`.

- [ ] `demo.py` has one owner: you. Do not split it by act and do not accept a
      suggestion to — see section 1 for why.
- [ ] Print the matched rule on **allow** as well as deny. Today `_attempt`
      passes a rule only on the `GovernanceDenied` path, so twelve `t.outcome`
      calls exist and six allow paths pass nothing. The data is already
      reachable: `govern()` exposes `.audit_log`, whose entries carry the rule,
      and Act 6 already renders it as a column.
- [ ] Render the default-deny case distinctly. Act 5's *correct* output is a deny
      with `matched_rule = None`. Printing `—` in a rule column makes a correct
      default indistinguishable from a rule that has silently disappeared, which
      is exactly the confusion the demo exists to remove.
- [ ] Resolve policy paths from `__file__`. `govern(policy=<path>)` falls through
      to parsing the path *string* as YAML when the file is not found, and dies
      with `AttributeError: 'str' object has no attribute 'get'` at
      `policy.py:1011` — not `FileNotFoundError`. Today `demo.py` runs clean from
      the worktree root and crashes from one directory up.
- [ ] `tools.py` stays policy-unaware. It is the ungoverned surface the demo
      wraps, and that ignorance is the point of Acts 1 and 2. Keep `reset()` and
      the module-level `TABLES` / `OUTBOX` / `EXPORTS`: `check_contracts.py`
      asserts against them, per section 10.
- [ ] `theatre`'s public API is frozen (section 3.3). Call it; do not change it.
      If you need a new rendering helper, comment the signature you want on your
      issue and let agent C add it on `main` afterwards.
- [ ] Do not change `requirements.txt`. You do not own it and CI will refuse the
      pull request. `sqlglot` in particular must stay installed.

**Acceptance.** `.venv/bin/python demo.py` exits 0 through all six acts from the
worktree root **and** from its parent directory. Act 3 allows the innocent
`SELECT` and denies the disguised `DROP`; Act 4 delivers 2 of 3 sends; Act 6
verifies the chain and then reports a hash mismatch after the forgery. Every
ALLOW line names a rule or explicitly says the default fired.
`check_contracts.py` still exits 0.

### Agent C — issue #3: the README, the governance section, and presentation

Worktree `../agt-agent-c`, branch `agent-c`.

**Exclusive files:** `README.md`, `theatre.py`.

You do not own `capture.py` and you do not regenerate `docs/images/`. That is the
integrator's job in stage 7, after A and B have merged; regenerating now renders
a stale demo. This is the field report's validated code-free lane pattern
(TEAM_PROMPT section B, issue #5: "No code files touched") with one leaf module
attached.

- [ ] Add a GOVERNANCE section to `README.md` documenting `OWNERS`, the CI
      ownership check, `scripts/setup-agent.sh`, and this document. State plainly
      that AGT is the subject of this demo and not its enforcement layer, and why
      (`govern.py:240`).
- [ ] Document the three traps so the next reader does not lose a session to
      them: `agt lint-policy` is wrong in both directions; `agt test` cannot load
      these policies at all; and running `demo.py` from the wrong directory
      raises `AttributeError`, not `FileNotFoundError`.
- [ ] Document what Act 6 does and does not prove. `AuditLog.verify_integrity`
      catches a forged entry and a deleted middle entry. It reports a
      **truncated tail** and a **fully emptied log** as valid — its own docstring
      at `audit.py:604` reads "Always valid." Say so; the demo's credibility
      rests on not overclaiming here.
- [ ] `theatre.py`: you may change what it renders. You may not change the names
      or signatures in section 3.3. It must keep importing only `rich` — not
      `demo`, not `tools`, not `agentmesh`.
- [ ] Write for the post-merge state and mark anything that has not merged yet,
      exactly as TEAM_PROMPT section B told the README lane to.

**Acceptance.** Someone with a fresh clone and no context can follow the README
start to finish. `.venv/bin/python demo.py` and `check_contracts.py` both still
pass. No file outside your list appears in your branch diff.

---

## 8. Merge order and the integrator

The integrator merges every pull request and decides anything that crosses an
issue boundary. No agent merges anything.

| Window | What happens |
|---|---|
| T+0:00 | Integrator pushes the freeze (section 3) to `main`. Everyone branches from that commit. |
| T+0:00–1:00 | Build. Draft PR by T+0:30, ready by T+1:00. |
| T+1:00–1:20 | Squash-merge in order: **#1 (A), then #2 (B), then #3 (C)**. Each rebased on `origin/main`, each green. |
| T+1:20–1:45 | Integration on `main`: regenerate `docs/images/` with `capture.py`, run the demo end to end, fix only small breakages as direct `fix: … (#4)` commits. |
| T+1:45–2:00 | Final README pass, cleanup, push. |

**Why that order.** A first, because `check_contracts.py` gains its case coverage
there and every later merge is then tested against a fuller contract. B second,
because C's images render B's output. C last for the same reason.

**Rebase, never merge, from `main` into an agent branch.** This is not
stylistic. During a *conflicted* `git merge main`, `git diff --cached
--name-only` lists every path the merge brought in, including integrator-owned
ones, so the ownership check refuses the commit and the agent is stuck in
`MERGING` with only `--no-verify` or `git merge --abort` as exits. Under `git
rebase main` the same conflict stages only the conflicting path and
`git rebase --continue` succeeds. TEAM_PROMPT rule 8 already mandates rebase; this
is the mechanical reason to hold the line on it. In CI, exempt merge commits and
judge the branch diff against the merge base rather than commit by commit.

**The integrator is exempt, structurally.** Every rule in section 4 that names an
agent lane must pass the integrator through, because the integrator's job is
squash-merging and pushing `main`. Branch protection with the integrator as the
one who merges pull requests does this without a special case. Do not write a
`pre-push` hook that refuses all pushes to `main`: it locks out the only person
who needs to push there.

**Cleanup, per AGENT_WORKFLOW section 2 stage 9.** `git branch --merged` lies
after a squash merge — the branch's commits are never ancestors of `main`. Key
deletion off pull request state:

```bash
gh pr list --state merged --json headRefName -q '.[].headRefName'
```

and delete only branches whose PR you can see merged. Per 4.4, whoever cleans up
touches only branches they created or can prove are merged.

---

## 9. The QA agent's standing brief

AGENT_WORKFLOW 5.3 calls this the highest-value seat, and the reason is worth
repeating: the QA loop found five real bugs in `crunched-kiss`, none of which was
visible from the diff alone. All were found by running the thing.

Run one agent on a fixed interval from its own worktree. It has authority to open
pull requests. It never commits to trunk, and it has no entry in `OWNERS`, which
means the ownership check refuses any branch it pushes that touches an owned
file — it can only open PRs the integrator then reassigns.

Standing brief:

- [ ] `git fetch && git rebase origin/main`.
- [ ] Run `check_contracts.py` and `demo.py`. Report the exit codes, not a
      summary of the output.
- [ ] **Check contract drift.** Diff the five facet names in section 3.2 against
      the strings the policy YAML actually reads, and diff `theatre.py`'s public
      names against the `t.*` calls in `demo.py` and the `demo.act_*` names in
      `capture.py`. This is the drift that produces no error.
- [ ] Mutate one policy condition and confirm something fails. If renaming a
      facet leaves `check_contracts.py` green, the contract test is not testing
      what it claims and that is a bug worth a PR on its own.
- [ ] Read the last few commits' diffs and hunt for real bugs, not style.
- [ ] Check `git status` in the shared checkout for untracked directories.
      `git status` collapses them, so `git add -A` can sweep in build output or a
      stale copy of live source without anyone noticing (AGENT_WORKFLOW 4.5).
- [ ] Open a PR per finding, with the reproduction command in the body.

---

## 10. Verification by consequence

AGENT_WORKFLOW 5.4: the strongest verification is an observable side effect
outside the app. For a spreadsheet agent that meant driving the pane and then
reading the saved `.xlsx` from disk. A screenshot showed a plausible number
either way.

The equivalent question here is: **what observable side effect proves a
governance control worked?** Not the verdict string — the verdict string is the
screenshot. A denial can be printed for the wrong reason, and section 3.2 shows a
rule that has silently vanished still printing `deny` because the default fired.

This repo has three consequences worth asserting on, in increasing distance from
the code.

### 10.1 The estate did not change

`tools.py` keeps the fake production state the tools mutate: `TABLES`, `OUTBOX`,
`EXPORTS`. A deny is proved by the absence of the mutation.

```
tools.reset()
safe_db(action="db_query", sql={"query": DISGUISED_DROP})
```
```
verdict: deny   rule: block-schema-destructive-sql
estate: {'customers': 1284, 'tickets': 9310, 'invoices': 4002}
```

`customers` is still there with all 1,284 rows. That is the assertion: not that
AGT said no, but that the table survived. Run against Act 1's ungoverned path the
same call returns `{'dropped': 'customers', 'rows_destroyed': 1284}` and the
dict loses a key. The two runs differ in the estate, not only in the transcript.

### 10.2 The allow path still mutates

A control that denies everything passes every deny test. So assert the positive
too:

```
safe_export(action="export_dataset", data={"contains_pii": False},
            rows=1000, destination="s3://x")
len(tools.EXPORTS) == 1
```

and in Act 4, `len(tools.OUTBOX) == 2` after three attempted sends — one customer
reply allowed, one 200-person notice approved by the callback, one 40,000-person
campaign refused. Two of three arrived. Count the outbox; do not read the line
that says so.

Together these are what `check_contracts.py` asserts, per rule name, alongside
`matched_rule`. Verdict plus rule plus estate. Any two of the three can be right
while the policy is broken.

### 10.3 The canary pull request

The consequence that proves the *build* controls, rather than the demo's, sits
outside the repository entirely: a red check on GitHub.

Before trusting the ownership check, break it on purpose. From `agent-a`'s
worktree, edit `demo.py` — a file agent-a does not own — push, open a pull
request, and watch the check fail with

```
agent-a may not change demo.py (owner: agent-b)
```

Then close it. Do this every time the harness changes, because a check that has
stopped running looks exactly like a check that is passing, and there are several
quiet ways for it to stop: the workflow not triggering on `pull_request`, the job
not actually marked required in branch protection, a `continue-on-error` left in
from debugging, a shallow checkout with no `origin/main` for the `...HEAD` diff to
resolve against, or the script exiting 0 on a condition nobody considered.

The local `pre-commit` hook has its own set, listed in section 4.1, and two of
them — a `core.hooksPath` pointing at a directory that does not exist, and a hook
file an agent has overwritten with `exit 0` — produce no output at all. That is
AGENT_WORKFLOW 5.6 applied to the enforcement layer: the failure is silent, so
the only way to know is to provoke it.

### 10.4 What the audit chain does not prove

Act 6 verifies a hash chain and then forges an entry to show the verification
failing. That is real. What it does not establish is completeness: `AuditLog`
reports a truncated tail and an emptied log as valid. If you want the chain as
evidence, the consequence to assert on is the entry *count* against the number of
governed calls made, not `verify_integrity()`.

---

## 11. What this plan does not prevent

Stated plainly, because a plan that omits its boundaries is worse than one that
admits them.

- **An agent overwriting a peer's file on disk.** Ownership is judged on the
  branch diff, so the damage is caught when it tries to land, not when it
  happens. A peer who has not committed can still lose work in between.
- **Anything in the shared checkout.** AGENT_WORKFLOW 4.1 happened there, with
  worktrees in place. No control here observes it. The rule is that only the
  integrator has a session pointed at it, and the rule is prose.
- **`npm install` and `pip install`.** Nothing sees them. "One owner for
  installs" is enforced only as "who may commit `requirements.txt`", which is a
  different failure from the one in 4.1.
- **`git worktree remove --force` against a peer's dirty worktree.** No hook
  exists in git that fires on worktree removal. Uncommitted work is lost and the
  hooks stay silent.
- **`git push origin --delete`** of a peer's branch. Pushing every branch to
  `origin` on first commit makes the *commits* recoverable; it does not stop the
  deletion.
- **iCloud conflict copies** (4.5). They are created by a sync daemon; there is
  no tool call to intercept at any layer. This repo happens to live under
  `~/Library/Application Support`, not `~/Documents`, which is an accident of
  where it sits rather than an achievement of this plan. Add the
  `* [0-9].*` / `* [0-9]/` ignore rule as a backstop anyway.
- **Reformatting code you did not change** (rule 3). A diff-shape property, not a
  path property.
- **`git log` as evidence.** An audit trail, not a tamper-evident one.
- **Two of the design decisions behind this plan were not testable here.** The
  claim that Claude Code's `PreToolUse` hooks block only on exit code 2 (and so
  fail open), and the tiering of GitHub push rulesets with path restrictions, were
  taken from documentation rather than reproduced. `/Library/Application
  Support/ClaudeCode/managed-settings.json` does not exist on this machine, so the
  argument that managed settings cannot differ per agent on one Mac is untested
  here too. They are plausible and they informed the choice of CI over hooks, but
  they are not on the same footing as the rest of this document.

---

## 12. Pre-flight checklist

TEAM_PROMPT section C style. About ten minutes, before you send anything.

**1. Deal with the untracked directory first.** Right now:

```
$ git status --short
?? ui/
```

`ui/server.py` is 25 KB and untracked, and it appeared in the working tree during
the survey that produced this document. Decide before fan-out whether it is
tracked, ignored, or deleted. `git status` collapses untracked directories, so a
later `git add -A` sweeps it in silently — that is 4.5's mechanism exactly, and
it is live in this repo today. Nobody can branch from a clean `main` until this
is resolved.

**2. Push the freeze, so everyone branches from one commit.** Per TEAM_PROMPT
section C's opening: uncommitted work in the shared checkout is how agents end up
redoing each other's work.

```bash
git add OWNERS check_contracts.py scripts/ .github/ docs/PARALLEL_AGENT_PLAN.md
git commit -m "chore: freeze contracts and ownership before fan-out"
git push origin main
```

**3. Prove the ownership check fails on something.** Do not assume it runs. The
branch-name argument is the only input, so the fail-closed case is testable from
anywhere:

```bash
sh scripts/check-ownership.sh ck-issue-3; echo "rc=$?"
# expect: no ownership lane for branch 'ck-issue-3'   rc=1
```

That case matters more than it looks. `crunched-kiss` names its worktrees and
branches after the *task* — `ck-issue-3`, `issue-4-fixture`, `docs/agent-workflow`
— not after the agent. Any scheme that infers identity from a name and defaults
the unrecognised case to "integrator" grants those branches everything. Default to
refusal.

Then the real test: from `agent-a`'s worktree, edit `demo.py`, push, and confirm
the canary pull request in section 10.3 goes red. Once, for real, before you trust
it.

**4. Prove `check_contracts.py` fails on a broken policy.** Rename
`data.contains_pii` to `data.has_pii` in a scratch copy of `baseline.yaml` and
confirm the test goes red. If it stays green the test is decorative.

**5. Set branch protection on `main`**: require a pull request, require the CI
status check, require branches up to date before merging, block force pushes,
squash-merge only. Note that the GitHub MCP server failed to connect during this
survey (`400: Authorization header is badly formatted`), so do this in the web UI
or with `gh api` and confirm it stuck. `gh auth status` reports the account is
logged in and `origin` is
`https://github.com/jun-bit-pulse-ai/agent-governance-demo.git`, so this is
actionable rather than hypothetical.

**6. `git config gc.auto 0`** in the shared checkout and in `setup-agent.sh`
(section 4.4).

**7. Add the conflict-copy backstop to `.gitignore`**, per AGENT_WORKFLOW 4.5.
Two lines, and `crunched-kiss` ships exactly these:

```
* [0-9].*
* [0-9]/
```

**8. Fix the environment topology.** `.venv` is at the repository's *parent*
today. Run `scripts/setup-agent.sh` for each agent, one at a time — not
concurrently, because `.git/config.lock` is shared (section 6). Confirm three
`.venv` directories inside three worktrees and no shared one in use.

**9. Confirm the baseline is green before anyone changes anything**, in each
worktree:

```bash
.venv/bin/python demo.py && .venv/bin/python check_contracts.py
```

Both must exit 0. `demo.py` takes about 0.5 seconds; the whole gate is
sub-second, and per-agent setup is about 7 seconds. Ceremony is not what will
slow this sprint.

**10. Decide the fan-out honestly.** Section 1 says two implementation lanes and
one documentation lane over 630 lines. If you find yourself adding a fourth,
re-read AGENT_WORKFLOW 5.1.4: parallel agents are additive by default, and the
plan that describes them is not exempt from that.

**During the sprint.** Merge in the order #1, #2, #3. Require a rebase and green
checks on each. If two pull requests somehow touch the same file, merge the
smaller and make the other rebase. Watch for an agent editing outside its list —
that is the failure mode this document exists to prevent, and CI catching it is
the only part of that sentence you can rely on.
