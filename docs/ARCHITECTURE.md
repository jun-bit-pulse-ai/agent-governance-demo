# Governing the agents that build this repo

An architecture for turning the ten rules in `crunched-kiss/docs/TEAM_PROMPT.md`
into things a machine checks, and for saying plainly which of them a machine
cannot check. Section references of the form "AGENT_WORKFLOW 4.1" point at
`crunched-kiss/docs/AGENT_WORKFLOW.md`, the field report this is designed against.

This document is about the repository, not the demo. The demo — `demo.py`,
`tools.py`, `policies/` — is what gets built. This is how two agents and an
integrator build it without repeating the six incidents in AGENT_WORKFLOW
section 4.

Every capability claim below was run against the installed
`agent-governance-toolkit==4.1.0` and against git 2.50.1 (Apple Git-155) in a
throwaway repository. Where a claim did not reproduce, it is marked as such.
Section 11 lists what was checked and how.

---

## 1. The problem

AGENT_WORKFLOW 5.6 makes an argument about model output:

> Anything the model must emit for the app to parse should be a tool with a JSON
> schema, not a string convention in the system prompt. Agents drop conventions
> under context pressure, and the failure is silent.

The evidence given is the clarifying-question parser: the model was told to reply
with lettered options in a blockquote, the `> ` prefix defeated the parser, the
text still rendered, and every test fixture used the unprefixed form. Nothing
threw. It just stopped working.

TEAM_PROMPT's ten rules are that same shape. "Edit only your Exclusive files",
"one owner for installs", "never touch another agent's branch", "both suites green
before every push" are string conventions in a system prompt, addressed to a
stochastic system, with no parser at all. Sections 4.1, 4.2, 4.4, 4.5 and 4.6 are
what happened when they were dropped: two agents rebuilding one `node_modules`, a
venv recreated with an app-bundled Python 3.12 against packages installed for
3.14, a cleanup pass pruning a branch whose only commit lived there, iCloud
conflict copies of live source, and a stale `.git/index.lock` that froze everyone.

AGT's thesis is the same one from the other end: a policy in the prompt is a
request, and a policy evaluated at the call boundary is a control. This repository
is a demo of that thesis. It would be odd to build it under prompt conventions.

So the design question is which of the ten rules can become an enforced check,
and — the part that matters more — which cannot, and should be labelled rather
than dressed up.

---

## 2. What AGT is here, and what it is not

AGT is the subject of this repository. It is **not** in the enforcement path for
the agents that write it. That is a deliberate decision and it rests on four
things I verified rather than assumed.

**`govern()` intercepts exactly one thing: a Python call to a callable you
wrapped.** `govern.py:240` is `return self._fn(*args, **kwargs)`, immediately
after the deny branch. Claude Code's `Edit`, `Write` and `Bash` are not Python
callables inside a process that imported `agentmesh`. Routing an agent's file
writes through AGT would mean denying the built-in tools and rebuilding them as
an MCP server — a large amount of new software whose failure mode is that agents
cannot edit files.

**The condition grammar cannot express file ownership.** `policy.py:111-183` is
the whole of `_eval_expression`. It supports `path == 'quoted'`, `!=`,
`in ['a','b']`, `> < >= <=` against a bare number, and a bare truthy path, split
naively on `" or "` then `" and "`. There is no `startswith`, no glob, no regex,
no negation, and no field-to-field comparison. Confirmed live:
`sql.verb startswith 'SEL'` evaluates `False`; so does `not data.contains_pii`
against a context where `data.contains_pii` is `True`. Every ownership decision
would therefore live in a hand-written Python facet extractor, and the reviewable
artefact would be the extractor, not the policy. A one-line ownership check in a
git hook is a smaller and more honest object.

**The audit chain cannot be persisted through the public API.**
`GovernanceConfig.audit_file` is declared at `govern.py:100` (docstring) and
`govern.py:113` (field) and read nowhere else in the package — those are the only
two occurrences. `govern()` does not accept the parameter. The log is in memory
and dies with the process. Three agents in three processes would produce three
logs that vanish.

**Facet extraction is fail-open by design.** `protocol_facets.py:47-58` catches
every exception inside an extractor "so a broken parser never blocks policy
evaluation". A control plane whose enrichment layer fails open is not the thing
to build a boundary out of.

None of that is a criticism of AGT for the job it does inside `demo.py`, which is
real and which the demo shows working. It is a statement about which job it is
for.

---

## 3. Target shape

Four rings. Each ring is named for what it observes, and each is honest about
whether it can be walked around.

```
   an agent decides to change a file
                 │
 ══ RING 0 ══════╪═══════════════════════════════════════ STRUCTURE
   per-agent worktree, in-worktree .venv, pinned interpreter
   nothing to intercept here: the shared resource does not exist
 ════════════════╪═══════════════════════════════════════
                 ▼
      Edit / Write / Bash / sed / a shell redirect
                 │
       the write lands on disk        ◄── NOT intercepted, by design
                 │
          git add ; git commit
                 │
 ══ RING 1 ══════╪═══════════════════════════════════════ LOCAL, ADVISORY
   pre-commit ─► staged tree × OWNERS@origin/main ─► refuse
   pre-push   ─► branch diff, target ref, both suites ─► refuse
   walked around by: --no-verify, core.hooksPath, editing the hook
 ════════════════╪═══════════════════════════════════════
                 ▼
            git push origin
                 │
 ══ RING 2 ══════╪═══════════════════════════════════════ CI — AUTHORITATIVE
   ownership check on main...HEAD, keyed on the PR head branch
   check_policy.py as a required status check
   branch protection: no direct push to main, no force-push, no deletion
 ════════════════╪═══════════════════════════════════════
                 ▼
        squash-merge to main
```

Ring 3 is the artefact, and lives entirely inside one Python process:

```
   demo.py ─► safe_db = govern(tools.db_query, policy=…, agent_id=NOVA)
                  │
                  ├─ _build_context(kwargs)     govern.py:364
                  │     action= → action.type ; dict kwarg → passthrough
                  │     scalar kwarg → {"value": …}
                  ├─ extract_protocol_facets    policy.py:830
                  │     sql:{query} → sql.verb from the sqlglot AST
                  ├─ PolicyEngine.evaluate      deny_overrides
                  └─ allow ─► self._fn(*args, **kwargs)      govern.py:240
```

The organising principle across rings 1 and 2 is **judge the consequence, not the
request**. Ring 1 does not ask "which tool is the agent calling"; it asks "what is
in the staged tree". That is why it survives a write made by `printf >> tools.py`
with no `Edit` call anywhere, and why it works the same for Claude Code, Cursor
and Kimi — the field report's actual fleet was all three.

The same rule is checked twice on purpose: locally for speed, in CI for
authority. Ring 1 exists to tell an agent within a second that it has strayed.
Ring 2 exists because ring 1 is one flag from irrelevance.

---

## 4. The components

### 4.1 Per-agent worktree with an in-worktree venv — `scripts/setup-agent.sh`

Roughly fourteen lines, integrator-owned, run once per agent:

```sh
git worktree add "$ROOT/../agt-$LANE" -b "$LANE" origin/main
/opt/homebrew/opt/python@3.13/bin/python3.13 -m venv "$ROOT/../agt-$LANE/.venv"
"$ROOT/../agt-$LANE/.venv/bin/python" -c \
  'import sys; assert sys.version_info[:2] == (3, 13), sys.version'
"$ROOT/../agt-$LANE/.venv/bin/pip" install -r requirements.txt
git -C "$ROOT/../agt-$LANE" config core.hooksPath "$HOOKS_ABS"
```

This is the highest-leverage component and it is not a control. It deletes the
shared resource rather than guarding it. The interpreter is pinned by absolute
path and asserted before anything installs, which is 4.2 stated as an assertion
rather than as a rule.

Cost, measured: 1s to create the venv, 6s to install, 112 MB on disk per lane.
Fourteen seconds and 224 MB for the two lanes in section 8. This is worth stating
because "the ceremony will slow the sprint" is the obvious objection and it is
wrong: setup is seconds, and the whole pre-push gate — `check_policy.py` plus
`demo.py` — is under a second. What actually costs a sprint is a workflow the
controls cannot express, which is why the merge-commit exemption in 4.3 is not
optional.

Right now this repository has **no** `.venv` of its own. The single environment
sits at the repository's parent and is shared by everything under it; its
`pyvenv.cfg` names `/opt/homebrew/Cellar/python@3.13/3.13.13_1/…/python3.13`.
That is precisely the 4.1/4.2 topology, live, today. `.gitignore` line 1 is
already `.venv/`, so moving it inside each worktree needs no other change.

**Honest limit, and it is the important one.** Worktrees were already the practice
in the field report — TEAM_PROMPT rule 1 mandates one per agent and section B
gives the three `git worktree add` commands — and 4.1 happened anyway, because the
collision was in the *shared checkout*, where rule 4 puts the dev server and where
section C shows the integrator running `npm ci`. Worktrees do not fix 4.1 on their
own. What fixes it is that no agent session is ever pointed at the shared
checkout, plus one named owner for installs. The first is a staffing decision. The
second is expressed here as "nobody but the integrator can commit
`requirements.txt`", which is a weaker statement than "nobody but the integrator
runs `pip install`", and the gap is real: **no layer in this design observes a
`pip install` or an `npm install` at all.**

### 4.2 `OWNERS` — one flat TSV, integrator-owned

```
demo.py                        engine
tools.py                       engine
policies/support-agent.yaml    engine
policies/analytics-agent.yaml  engine
theatre.py                     presentation
capture.py                     presentation
README.md                      presentation
docs/images/                   presentation
```

Path prefix, tab, lane. Two rules that are not optional:

1. **Longest prefix wins.** A naive "any matching prefix grants" loop is a
   verified silent failure: with `policies/` granted to a lane and
   `policies/baseline.yaml` reserved to the integrator, the lane commits the frozen
   baseline with exit 0 and no warning. That is AGENT_WORKFLOW 5.6's failure class
   sitting inside the enforcement layer. The resolution rule is part of the
   contract, and `check_policy.py` tests it.
2. **Unlisted means integrator.** `requirements.txt`, `requirements-dev.txt`,
   `policies/baseline.yaml`, `check_policy.py`, `.github/`, `scripts/` and `OWNERS`
   itself appear nowhere, so no lane can commit them. This is how "one owner for
   installs" and "freeze the contract" are said in one mechanism.

`OWNERS` is read from `origin/main`, never from the working tree and never from
`HEAD`. Reading it from the working tree lets an agent widen its own grant with an
uncommitted edit. Reading it from `HEAD` is barely better: one
`git commit --no-verify` that appends a line to `OWNERS` makes `HEAD` the agent's
own branch tip, and every subsequent hooked commit passes. That was reproduced.
`origin/main` is the only version an agent cannot move alone.

### 4.3 `pre-commit` — the fast local signal

About twenty lines of POSIX `sh`. Reject a commit staging any path whose
longest-matching `OWNERS` prefix names another lane.

Three details that are load-bearing:

- **Newline-only `IFS`.** The obvious `for f in $(git diff --cached --name-only)`
  splits `demo 2.py` into two paths. That is the literal filename shape of
  incident 4.5. Verified: with newline-only `IFS` the iCloud-shaped name parses as
  one path and is refused. (`core.quotePath=false` does *not* help here — it
  matters only for non-ASCII names, where it prevents a false refusal. A name
  containing a literal newline stays quoted under both settings and fails closed.)
- **No `done < <(...)`.** Process substitution is bash-only and git runs hooks
  under `sh`; the syntax error makes the hook exit 0, which is fail-open.
- **Exempt merge commits.** `test -f "$(git rev-parse --git-dir)/MERGE_HEAD"` and
  return 0. Without this the hook is a sprint-stopper: during a conflicted
  `git merge main`, `git diff --cached --name-only` lists every path the merge
  brought in, including integrator-owned ones, so the commit is refused and the
  agent is stuck in `MERGING` with only `--no-verify` or `git merge --abort` as
  exits. Normalising `--no-verify` is what actually kills this design, so the
  workflow must never require it. A clean merge does not run `pre-commit` at all,
  so the exemption costs nothing that was being checked anyway. The branch diff is
  judged at push and in CI, where merge-brought content is correctly attributed to
  the merge base.

Installed by pointing `core.hooksPath` at an **absolute** path outside every
worktree. Two reasons. An in-repo `.githooks/` is a file the governed agent owns:
overwriting `pre-commit` with `exit 0` needs no flag, creates no commit, and
leaves nothing in the diff for review to catch — reproduced. And a `core.hooksPath`
pointing at a directory that does not exist **fails open silently** — I confirmed
that the commit lands with exit 0 and no warning.

One claim I could not reproduce and will not repeat: a relative `core.hooksPath`
is said to resolve against the invocation directory, so that committing from
`policies/` skips the hook. On git 2.50.1 it resolves against the worktree
top-level and the hook ran correctly from a subdirectory. The absolute path is
still the right choice, for the two reasons above.

### 4.4 `pre-push` — target ref, deletion, and the suites

About fifteen lines. Refuse a push whose target ref is `refs/heads/main` unless
the pusher is the integrator; refuse a refspec whose local sha is all zeros, which
is how a branch deletion arrives on stdin; run `check_policy.py` and `demo.py` and
refuse on non-zero.

The integrator exemption is not a nicety. The integrator's job in TEAM_PROMPT
section C, at T+1:30–1:50, is squash-merging and pushing `main`. A rule that
refuses every push to `main` locks out the only person who is supposed to do it,
which was reproduced against a naive version.

The zero-sha check is the replacement for a branch-deletion hook. `git push
origin --delete <branch>` is the deletion that actually loses work, because stage
9 cleanup in AGENT_WORKFLOW section 2 is remote cleanup driven off
`gh pr list --state merged --json headRefName`. It succeeds with exit 0 and no
flag. A pre-push hook does see it — the refspec arrives as
`lsha=0000000000000000000000000000000000000000` — and can refuse it.

`git push --no-verify` skips this hook. It is a signal, not a boundary.

### 4.5 CI — the only ring that holds

A GitHub Actions job on every pull request:

```sh
lane=$(printf '%s' "$GITHUB_HEAD_REF" | cut -d/ -f2)
git diff --name-only "origin/$GITHUB_BASE_REF...HEAD" |
  while IFS= read -r path; do owner_of "$path"; done   # longest prefix
```

Lane branches are named `lane/<name>/<topic>`, which is what makes that one
line work. Fourteen lines, the same longest-prefix resolution as the hook, and no identity
function at all: the lane comes from the pull request's head branch, which the
agent cannot rewrite from inside its worktree. It cannot be defeated by
`--no-verify`, by a `core.hooksPath` override, or by editing a hook.

Alongside it, `check_policy.py` and `demo.py` as required status checks, and
branch protection on `main`: require a pull request, block force pushes, block
deletions, require branches up to date before merging, squash-only.

Branch protection is the only unbypassable layer in the whole design. Everything
local is advisory. It is worth stating that in those words, because the
alternative — writing "structurally impossible" over a `pre-commit` hook — is the
same overclaim the field report warns about in a different register.

### 4.6 `check_policy.py` — the one piece of real software

About fifty lines, integrator-owned, frozen, deliberately not owned by the lane
that writes the policies it tests. It asserts, against the same `PolicyEngine`
that `govern()` uses:

- every rule name in every policy fires on a positive case and does **not** fire
  on a negative one, checked on `matched_rule` and not merely on the verdict;
- Act 5's correct behaviour, which is a deny with `matched_rule` of `None` — so
  the assertion is on the expected value, not on "a rule matched";
- `conflict_strategy == "deny_overrides"`, because `PolicyEngine.__init__` at
  `policy.py:491` defaults to `priority_first_match` and only `govern()`'s own
  default at `govern.py:117` supplies the safer one;
- the `OWNERS` longest-prefix resolution, with the `policies/` versus
  `policies/baseline.yaml` case that silently failed above.

It exists because I reproduced the failure it catches. Renaming
`data.contains_pii` to `data.has_pii` in `policies/baseline.yaml` — one word —
flips a 12,000-row PII export from `[DENY] Personal data may not leave the
platform via an agent` to `[ALLOW] exported`. `default_action: deny` does not save
you: the child's `allow-anonymised-export` rule catches it at `rows.value <=
50000`. `demo.py` exits 0 either way.

Neither of AGT's own policy tools notices. `agt lint-policy` is worse than a false
positive generator; it is a **false negative** generator. Its `KNOWN_ACTIONS`
accepts `block`, and a policy using `action: block` lints "No issues found" while
the runtime refuses to load it at all with a Pydantic `Input should be 'allow',
'deny', 'warn', 'require_approval' or 'log'`. `agt test` exits 1 without
evaluating a single fixture, dying on this repository's own working policy with
`Input should be a valid dictionary or instance of PolicyCondition
[input_type=str]`, because it loads `agent_os.policies.schema.PolicyDocument` — a
different engine whose `condition` is a dict. Neither belongs in CI.

`demo.py` already does half this job: it exercises four of the eight distinct rule
names and already prints `matched_rule` on a denial. What is missing is an
assertion, not a subsystem. `check_policy.py` is small because it is only the
missing half.

---

## 5. Control matrix

Every rule in TEAM_PROMPT section A and every incident in AGENT_WORKFLOW section
4. "Layer" is where the check lives. "Does not do" is the residual, per rule, not
per design.

| Rule / incident | Source | Layer | What enforces it | What it does not do |
|---|---|---|---|---|
| 1a Never commit or push to `main` | TEAM_PROMPT 1; 5.2 row 1 | pre-push + branch protection | Refuse a push to `refs/heads/main` from a lane; branch protection requires a pull request | The hook dies to `--no-verify`. Only branch protection holds, and it fires at push time, after the work |
| 1b Never force-push | TEAM_PROMPT 1 | branch protection | "Block force pushes" on `main`, server-side | Force-pushing a lane's own branch is not prevented, and arguably should not be |
| 1c Never run git in the shared checkout | TEAM_PROMPT 1 | none — staffing | No agent session is pointed at it; `setup-agent.sh` never hands a lane that path | Nothing detects an agent that goes there anyway. This is the sharpest edge in the design; see section 9 |
| 1d Never touch another agent's branch | TEAM_PROMPT 1; 4.4 | pre-push + branch ruleset | Refuse a zero-sha refspec; a GitHub ruleset restricting deletions on the lane namespace | `git branch -D` of a peer's local branch is unguarded — see section 10 on why the `reference-transaction` hook was cut |
| 2 Edit only your exclusive files | TEAM_PROMPT 2; 5.2 row 3 | pre-commit + CI | Longest-prefix `OWNERS` match over the staged tree locally, and over `main...HEAD` in CI keyed on the PR head branch | Does not intercept the write. A peer's file can be clobbered on disk; the damage surfaces at commit, and a peer who has not committed can still lose work in between |
| 3a No new dependencies | TEAM_PROMPT 3 | pre-commit + CI | `requirements*.txt` are unlisted in `OWNERS`, so no lane can commit them | Does not observe `pip install`. An agent can install anything into its own venv; it just cannot record it |
| 3b No renames | TEAM_PROMPT 3 | pre-commit + CI, partially | A rename appears as an add and a delete; the deleted path is ownership-checked like any other | Renaming a file you own is permitted and unpoliced |
| 3c No reformatting code you did not change | TEAM_PROMPT 3 | UNENFORCEABLE | — | This is a diff-shape property, not a path property. Nothing here detects it |
| 4 One owner for installs and long-running processes | TEAM_PROMPT 4; 4.1 | Ring 0, partially | One dependency tree per worktree, so there is no shared `node_modules` or `.venv` to rebuild underneath anyone | **Does not prevent 4.1.** The field incident was in the shared checkout, where this design applies no control, and no layer observes an install command |
| 4.2 Venv rebuilt with the wrong interpreter | AGENT_WORKFLOW 4.2 | Ring 0 | Absolute interpreter path plus a `sys.version_info` assertion before install; the venv lives inside the worktree | Does not prevent the mistake — contains it. An agent that hand-rolls its own venv breaks only its own lane. The assertion runs only when the script runs |
| 4.3 Two agents ship the same fix | AGENT_WORKFLOW 4.3 | UNENFORCEABLE at this scale | Two lanes over eight owned files leaves no work item both could pick up | The field instance was integrator-versus-agent, and the integrator bypasses everything here. A claim-lock would cost more than the duplicate it prevents on a repo this size |
| 4.4a Cleanup orphans a peer's commits | AGENT_WORKFLOW 4.4 | pre-push + ruleset + habit | Refuse zero-sha refspecs; push every lane branch to `origin` on first commit so the commits are always recoverable from the remote | A local `git branch -D` of a peer branch is not caught. `git update-ref refs/heads/<peer> HEAD` orphans commits identically and no hook fires |
| 4.4b Cleanup destroys a peer's uncommitted work | AGENT_WORKFLOW 4.4 | git itself, partially | `git worktree remove` refuses a dirty tree on its own: `fatal: … contains modified or untracked files, use --force` | `git worktree remove --force` succeeds and is **unhookable** — git 2.50.1 exposes 24 hooks and none fires on worktree removal |
| 4.4c Unrecoverable stash | AGENT_WORKFLOW 4.4 | UNENFORCEABLE | — | The object was never written to the store. No ref transaction, no hook, nothing to intercept or recover |
| 4.5 iCloud conflict copies | AGENT_WORKFLOW 4.5 | location + gitignore + pre-commit | This repository already sits under Application Support, not `~/Documents`; `* [0-9].*` and `* [0-9]/` ignore rules; an unowned conflict copy is refused at commit | The location is an accident of where the repo sits, not an achievement of this design. The files are made by a sync daemon — there is no tool call to intercept at any layer |
| 4.6 Stale `index.lock` froze everyone | AGENT_WORKFLOW 4.6 | Ring 0, partially | Each worktree has its own index under `.git/worktrees/<name>/`; a stale lock in the main checkout does not block a worktree | **The class survives.** `.git/config.lock` is shared and blocks `git config` and `git worktree add` from every worktree — the fan-out step itself. Ref locks are shared too |
| 5 Both suites green before every push | TEAM_PROMPT 5 | pre-push + required status check | `check_policy.py` and `demo.py` locally, re-run server-side | `--no-verify` skips the hook. The status check gates the merge, not the push. AGT has no notion of sequencing: `SessionState.inject_context` is never called by `govern()` |
| 6 Freeze the contract; only its owner changes it | TEAM_PROMPT 6; 5.1.3 | pre-commit + CI for *who*; `check_policy.py` for *drift* | `policies/baseline.yaml` and `theatre.py`'s public API are unlisted or reserved in `OWNERS` | Ownership and drift are different properties. Only ownership is a path check; drift needs the test |
| 7a Commit message format | TEAM_PROMPT 7 | commit-msg hook, if wanted | A regex on `feat\|fix\|docs\|test: … (#N)` | Enforcing the prefix and calling the rule enforced would be theatre |
| 7b Commits small, one concern each | TEAM_PROMPT 7 | UNENFORCEABLE | — | No mechanical signature for "one concern". It is a review judgement |
| 8 Branch off `origin/main`, rebase before merge | TEAM_PROMPT 8; 5.2 row 5 | branch protection | "Require branches to be up to date before merging", squash-only | A merge gate, not a work gate. It does not stop an hour of work on a stale base. The T+0:45 / T+1:30 timings are unenforceable |
| 9a Never widen your scope | TEAM_PROMPT 9 | pre-commit + CI | A scope-widening edit lands outside the lane's `OWNERS` prefixes and is refused | Catches the symptom after the fact, not the behaviour |
| 9b Blocked >15 min: comment and move on | TEAM_PROMPT 9 | UNENFORCEABLE | — | An agent can burn an hour blocked without staging a file. There is no observable signature |
| 10a Done means rebased, green, files inside your list | TEAM_PROMPT 10 | CI | Branch protection plus the required status check plus the ownership check on the branch diff | — |
| 10b Then stop | TEAM_PROMPT 10 | UNENFORCEABLE | — | An agent continuing to work is indistinguishable from an agent working |
| 5.1.1 Write the architecture yourself | AGENT_WORKFLOW 5.1.1 | UNENFORCEABLE | — | No call boundary and no diff shape corresponds to "the human chose the loop and the state boundaries". It is the most important rule in the playbook and no software touches it |
| 5.1.4a Reject additions that increase file count | AGENT_WORKFLOW 5.1.4 | pre-commit + CI | A new file outside a lane's prefixes cannot be committed — which is how the guided tour, ELI5 mode, clarifying buttons and follow-up chips would have been stopped at birth | — |
| 5.1.4b Prefer refining to abstracting | AGENT_WORKFLOW 5.1.4 | UNENFORCEABLE | — | A qualitative judgement. Half this rule is mechanism and half is prose; the design should not blur them |
| 5.3 QA agent may open PRs, never commit to trunk | AGENT_WORKFLOW 5.3 | CI, expressed exactly | A QA lane with no `OWNERS` entry can commit nothing and can only open pull requests | Does not make a QA agent exist or make it good. Staffing, not control |
| 5.6 A deny rule must not silently disappear | AGENT_WORKFLOW 5.6 | `check_policy.py` | Positive and negative cases per rule, asserted on `matched_rule` | The only thing that catches this class. AGT's own tools cannot be used — see 4.6 |
| Tamper-evident record of who did what | 4.4; AGT's audit chain | `git log` — and it is **not** tamper-evident | Commit authorship | An agent with repo write access rewrites history as easily as it edits `OWNERS`. Signed commits plus a protected branch are the real fix, and are out of scope here. AGT is no better: its `audit_file` is dead config, and its verifier reports tail truncation and a fully emptied log as valid |

Thirty-one rows. Nine are unenforceable outright. Eleven more are partial,
contained rather than prevented, or enforced only by where the repository happens
to sit. That ratio is the finding, not a defect in the design — a control matrix
that reported thirty-one greens would be describing an aspiration as a practice.

---

## 6. The policy design

### 6.1 What the YAML looks like

Three files. `policies/baseline.yaml` is `scope: global`, `default_action: deny`,
and holds the three rules nobody may weaken. Both agent policies carry
`extends: ["baseline.yaml"]`, `scope: agent`, an `agents:` list of one DID, and
their own `default_action: deny`.

```yaml
# policies/baseline.yaml — organisation-wide, integrator-owned, frozen
default_action: deny
rules:
  - name: block-schema-destructive-sql
    condition: "sql.verb in ['DROP', 'TRUNCATE', 'ALTER', 'GRANT']"
    action: deny
    priority: 100
  - name: block-pii-export
    condition: "action.type == 'export_dataset' and data.contains_pii"
    action: deny
    priority: 90
```

```yaml
# policies/support-agent.yaml — one lane owns this
extends: ["baseline.yaml"]
agents: ["did:mesh:support-nova"]
default_action: deny
rules:
  - name: approve-bulk-email
    condition: "action.type == 'send_email' and recipients.value >= 50"
    action: require_approval
    approvers: ["security-team"]
    priority: 50
```

### 6.2 Where the facets come from

This is the part that decides what a rule can say, and it is smaller than it
looks. `govern.py:364` builds the evaluation context straight from the wrapped
function's keyword arguments:

| Kwarg shape | Becomes | Example |
|---|---|---|
| `action="db_query"` | `action.type` | `action.type == 'db_query'` |
| a dict kwarg | passed through unchanged | `data={"contains_pii": True}` → `data.contains_pii` |
| a scalar kwarg | wrapped in `{"value": …}` | `rows=12000` → `rows.value` |

Then `policy.py:830` calls `extract_protocol_facets`, which runs a registry keyed
by **kwarg name**. Only two keys are registered (`protocol_facets.py:264`): `sql`
and `k8s`. A kwarg literally named `sql` holding `{"query": …}` is enriched in
place with `verb`, `target`, `tables` and `functions` from the sqlglot AST. That
is the entire mechanism behind Act 3.

So the facet namespace this demo needs is:

```
action.type        from action=            all three tools
sql.verb           from sql={"query":…}    db_query, via sqlglot
data.contains_pii  from data={…}           export_dataset
rows.value         from rows=              export_dataset
recipients.value   from recipients=        send_email
```

**The policy YAML is coupled to the tool function signatures.** Renaming the
`sql` kwarg in `tools.py` to anything else silently removes `sql.verb` and every
SQL rule stops matching. Renaming `recipients` to `to_count` silently removes the
approval gate. This is the same shape as the `backend/app/tools.py` ↔
`dispatchExcelTool` contract in AGENT_WORKFLOW 5.1.3, and it is invisible: there
is no schema, no error, and no audit entry. It is the second contract to freeze,
and it is why `check_policy.py` pins facet names rather than only verdicts.

### 6.3 Grammar traps a policy author must know

Verified live against `PolicyRule.evaluate` with a context of
`{action:{type:"export_dataset"}, data:{contains_pii:True}, rows:{value:12000}}`:

| Written | Evaluates | Why |
|---|---|---|
| `data.contains_pii` | `True` | bare truthy path |
| `data.has_pii` | `False` | missing path returns False, no error |
| `action.type == 'export_dataset'` | `True` | — |
| `(action.type == 'export_dataset')` | `False` | every regex is anchored with `re.match`; none accepts a leading paren |
| `rows.value >= 12000` | `True` | — |
| `rows.value == 12000` | `False` | equality only matches a **quoted** literal |
| `rows.value == '12000'` | `False` | quoted equality compares int against str |
| `not data.contains_pii` | `False` | there is no negation; the whole expression is unparseable and returns False |
| `sql.verb startswith 'SEL'` | `False` | no such operator |

Two consequences worth writing down. Numeric equality is impossible — express it
as two range clauses. Asserting that a boolean is *false* is impossible at all;
restructure the rule so the true case carries the decision.

### 6.4 Inheritance is thinner than it reads

`extends` additive-only protection is implemented at `policy.py:361` as a set of
parent rule **names**:

```python
parent_deny_names = {r.name for r in parent_rules if r.action == "deny"}
...
if rule.name in parent_deny_names and rule.action in ("allow", "log"):
    continue   # ignored
```

A child rule with a *different* name and a higher priority is never filtered.
Under `priority_first_match` it would beat the parent deny outright. It does not,
here, only because `govern()` passes `deny_overrides` (`govern.py:117`) while
`PolicyEngine.__init__` (`policy.py:491`) defaults to `priority_first_match`. That
is a configuration invariant, not an inherited guarantee, and `check_policy.py`
asserts it explicitly.

---

## 7. Contracts to freeze before fan-out

Per AGENT_WORKFLOW 5.1.3, both sides of a contract land on `main` before any lane
branches.

1. **`policies/baseline.yaml`.** Both child policies extend it. Integrator-owned,
   unlisted in `OWNERS`, changed only on `main`, with the `deny_overrides`
   invariant asserted in `check_policy.py`.
2. **The tool signatures in `tools.py`.** As section 6.2 shows, the kwarg names
   *are* the facet names. This is the demo's `tools.py`/`dispatchExcelTool` pair
   and it is the least visible contract in the repository.
3. **`theatre.py`'s public API** — `console`, `act`, `call`, `outcome`, `note`,
   `banner`, `estate`. `demo.py` calls into it at roughly fifty sites and the two
   files sit in different lanes. This is a genuine cross-lane contract that an
   earlier draft of this design missed entirely while listing others.
4. **`demo.py`'s act names** — `act_one` through `act_six`, which `capture.py`
   imports. Together with (3) this is a circular dependency between the two lanes,
   which is exactly why both halves are frozen before anyone starts rather than
   negotiated during.
5. **`OWNERS`, including the longest-prefix resolution rule.** It must be on
   `origin/main` before fan-out, because that is where the hook and CI read it
   from. An `OWNERS` file that exists only in a working tree enforces nothing.
6. **`check_policy.py`**, integrator-owned and frozen, including its `OWNERS`
   resolution cases.
7. **The hooks directory and `scripts/setup-agent.sh`**, at an absolute path
   outside every worktree.

---

## 8. The work split

Two lanes and an integrator, not three lanes.

| Lane | Owns | Why it is a lane |
|---|---|---|
| `engine` | `demo.py`, `tools.py`, `policies/support-agent.yaml`, `policies/analytics-agent.yaml` | The governance narrative and the policies it exercises. Coupled to each other through facet names, not through files |
| `presentation` | `theatre.py`, `capture.py`, `README.md`, `docs/images/` | A verified leaf (`theatre.py` imports only `rich`) plus the renderer and the prose. The field report's validated "README as a code-free issue" pattern, its issue #5 |
| integrator | everything unlisted | `policies/baseline.yaml`, `check_policy.py`, `OWNERS`, `requirements*.txt`, `.github/`, `scripts/` |

`demo.py` is not split by act. The six acts share module-global state through
`tools.reset()`, thread `safe_db` from `act_two` into `act_three` and `act_six`,
and are sequenced by one `main()`. Six agents on six acts is six agents on one
308-line file — the anti-pattern of AGENT_WORKFLOW section 4.

The third lane was cut. The measured repository is twelve tracked non-image files,
550 lines of Python (`capture.py` 106, `demo.py` 308, `theatre.py` 62, `tools.py`
74) and 80 of YAML. The field report's own three-lane fan-out was over a frontend,
a backend and 157 tests — roughly an order of magnitude more code — and one of its
three lanes touched no code at all. A third lane here would have been six
prohibitions and a fixture directory whose file format is a contract with a file
its owner may not edit. Folding those cases into the integrator-owned
`check_policy.py` deletes the lane and the contract together.

**An earlier draft claimed "662 tracked lines".** It matches nothing measurable:
`git ls-files` totals 4,850 lines, 803 excluding the PNG and SVG images, 630 for
Python and YAML alone. The number was invented. Corrected here rather than
quietly dropped, because a design that miscounts its own scale has miscounted the
thing that decides its fan-out.

---

## 9. What this explicitly does not prevent

- **It does not intercept a write.** Ring 1 judges the staged tree. An agent can
  clobber a peer's file on disk and the peer can lose uncommitted work before
  anything refuses anything. This is a deliberate trade: judging the consequence
  is harness-agnostic and survives a `sed -i` or a shell redirect, where a
  tool-call hook does not.
- **Every local control is one flag from irrelevance.** `git commit --no-verify`,
  `git push --no-verify`, `git -c core.hooksPath=/dev/null`, or simply
  overwriting the hook file. Only CI and branch protection hold. Saying otherwise
  would be the same overclaim as calling a prompt rule an enforcement.
- **Identity is not authenticated.** The local lane name comes from configuration
  the agent can read and change; the authoritative one comes from the pull
  request's head branch, which it cannot. Any design that claims non-forgeable
  local identity for a process with a shell is wrong. Note also that the field
  report allocates worktrees **per task**, not per agent — `ck-issue-3`,
  `ck-issue-4`, `ck-issue-5` — so any identity function keyed on a worktree name
  pattern would have resolved all of them to the same thing.
- **It does nothing about the shared checkout, which is where 4.1 happened.**
  Keeping agent sessions out of it is discipline, not mechanism.
- **No layer observes `pip install` or `npm install`.** "One owner for installs"
  is enforced here as "one owner may commit `requirements.txt`", which is a
  different and weaker statement.
- **`git worktree remove --force` against a peer's dirty worktree is
  unenforceable.** Git 2.50.1 exposes 24 hooks and none fires on worktree removal.
  Git's own refusal of a dirty tree without `--force` is the only guard, and it is
  git's, not this design's.
- **A local `git branch -D` of a peer branch, and a hostile
  `git update-ref refs/heads/<peer> HEAD`, are unguarded.** Pushing every lane
  branch to `origin` on its first commit is the mitigation, and it is a habit, not
  a control.
- **`git log` is an audit trail, not a tamper-evident one.**
- **The `.git/config.lock` class of freeze survives.** It is shared across
  worktrees and blocks `git worktree add`, which is the fan-out step.
- **iCloud conflict copies cannot be prevented at any layer.** They are made by a
  sync daemon with no tool call. That this repository sits outside `~/Documents` is
  luck, not architecture.

---

## 10. What was cut, and why

**A `reference-transaction` hook guarding branch deletion.** This was the
centrepiece of an earlier draft and it is wrong. `git pack-refs --all` emits a
prepared transaction of `old=<sha> new=0000000` for **every** branch in the
repository, including `main`, so a hook that refuses zero-valued updates aborts
`git gc` outright: `fatal: ref updates aborted by hook / fatal: failed to run
pack-refs`. Worse, there is no discriminator available: a real `git branch -D` of
an already-packed ref arrives as `old=0000000 new=0000000`, and
`GIT_REFLOG_ACTION` is empty in both cases. The hook cannot tell deletion from
housekeeping. It also guards the deletion that costs nothing — the local copy —
while `git push origin --delete` succeeds with exit 0. Replaced by the pre-push
zero-sha check, a GitHub ruleset on the lane branch namespace, and pushing lane
branches early. Set `gc.auto 0` in the lanes regardless.

**A broker daemon in front of every write.** It makes AGT the primary control
plane by funnelling three agents' file writes and git calls through one Python
process: a single point of failure, a serialisation point, and a concentration of
blast radius, since whoever writes the facet extractor writes the policy. Its one
genuinely irreplaceable part was a cross-process lease, and a lease acquired as a
side effect inside a facet extractor — with no release, no TTL and no deny-path
semantics — leaks permanently when an unrelated rule denies. That turns 4.1 from
"two agents rebuild `node_modules`" into "no agent can ever install again".

**A `PreToolUse` hook calling AGT's `PolicyEngine`.** Four reasons. It is
fail-open: `PreToolUse` blocks only on exit code 2, so a crash or unparseable
output proceeds, and this design's whole value is failing closed. It costs 0.34s
per governed call, essentially all of it `import agentmesh.governance`, on every
`Edit` and `Bash`. It covers one harness while the field report's fleet was three.
And for path ownership specifically, a declarative deny rule on a glob beats a
Python extractor outright.

**Per-agent exclusive file lists via managed settings.** On macOS managed settings
is a single machine-wide root-owned file, so agents on one Mac share it and its
rules cannot differ per agent. A deny rule cannot carry allowlist exceptions and
the glob syntax has no negation, so an exclusive list can only be an enumerated
complement — which cannot cover files that do not exist yet. (The managed-settings
file does not exist on this machine, so this rejection rests on documented
behaviour rather than a local test. Flagged rather than asserted.)

**AGT's audit chain as the forensic record.** `audit_file` is dead config;
`govern()` constructs `AuditLog()` with no sink (`govern.py:143`) and takes no
parameter to supply one, so the log dies with the process. And the tamper evidence
is partial: middle-entry deletion and content forgery are caught, but tail
truncation and a fully emptied log both verify as valid — so an agent deleting the
trailing lines that record its own violations is undetectable. `verify_integrity`
at `audit.py:604` has the docstring `"""Always valid."""`.

**`ExecutionRing`, `limit:` rate limiting, `MultiAgentPolicyEvaluator`,
`SessionState`, the kill switch.** None is usable. `ExecutionRing` classifies
actions into resource types by exact token match on the action-name string and
defaults to `TOOL_EXECUTION`, which is permitted at every ring — `read_file`,
`npm_install`, `send_email` and `export_dataset` all run unimpeded at Ring 3. It
is a naming convention enforcing a naming convention, which is the 5.6 failure
class. `limit:` is a silent no-op: `_increment_rate_limit` is called only from
`_apply_rule`, which has zero call sites in the package.
`MultiAgentPolicyEvaluator` is never called outside its own definition.
`govern()` never calls `SessionState.inject_context`. The kill switch is
referenced nowhere in `agentmesh` at all.

**`agt lint-policy` and `agt test` as CI gates.** Both fail on this repository's
own working policies, and the linter's failure is the dangerous direction: a
policy using `action: block` lints clean while the runtime cannot load it.

**Scaling past two lanes.** Covered in section 8.

---

## 11. What was checked, and how

Run against `agent-governance-toolkit==4.1.0`, Python 3.13.13, git 2.50.1.

| Claim | Method | Result |
|---|---|---|
| `govern()` intercepts only a Python call | read `govern.py:240` | `return self._fn(*args, **kwargs)` |
| No negation, glob, regex or `startswith` in conditions | read `policy.py:111-183`; live `PolicyRule.evaluate` | as in the table in 6.3 |
| A renamed facet silently flips a verdict | copy of the repo; `sed` `contains_pii`→`has_pii` in `baseline.yaml`; `python demo.py` | `[DENY] Personal data may not leave…` → `[ALLOW] exported`, exit 0 both times |
| A parenthesised condition evaluates False | live `PolicyRule.evaluate` | `(action.type == 'export_dataset')` → `False` |
| `audit_file` is dead config | `grep -rn audit_file` over the package | two hits, both declarations in `govern.py` |
| Facet extraction fails open | read `protocol_facets.py:47-58` | exceptions caught and logged |
| Facets are keyed by kwarg name | read `govern.py:364`, `protocol_facets.py:264` | only `sql` and `k8s` registered |
| `extends` matches on rule name only | read `policy.py:361-374` | `parent_deny_names = {r.name …}` |
| `deny_overrides` comes from `govern()`, not the engine | read `policy.py:491`, `govern.py:117` | engine default is `priority_first_match` |
| `agt lint-policy` passes a policy the runtime rejects | wrote a policy with `action: block` | lint `No issues found`; runtime `ValidationError` |
| cwd trap raises `AttributeError`, not `FileNotFoundError` | ran `demo.py` from the parent directory | `policy.py` `data.get("apiVersion")` on a `str` |
| A missing `core.hooksPath` directory fails open | throwaway repo, `-c core.hooksPath=/nonexistent` | commit landed, exit 0, no warning |
| A **relative** `core.hooksPath` does *not* fail open | throwaway repo, committed from a subdirectory | hook ran, `rc=1`. **An earlier claim to the contrary did not reproduce** |
| Losing `sqlglot` fails closed, not silently | forced an ImportError in the extractor, ran a plain `SELECT` through `govern()` | `DENIED reason='No matching rules, using default' matched_rule=None` |
| `pack-refs` is indistinguishable from deletion | instrumented `reference-transaction` hook | `old=<sha> new=0000000` for every branch; `branch -D` of a packed ref arrives `old=0 new=0` |

Two claims used to reject alternatives were **not** testable here and are marked
as such above: `PreToolUse` blocking only on exit code 2, and the absence of
negation in permission globs. They are consistent with documented behaviour, but
they were not verified in this environment and should not be read as if they were.

---

## 12. The operational footguns

Worth writing down because each costs a session.

- **Run `demo.py` and `check_policy.py` from the worktree root.** From anywhere
  else, `govern(policy="policies/…")` falls through to parsing the path *string* as
  YAML and dies with `AttributeError: 'str' object has no attribute 'get'` rather
  than `FileNotFoundError`. `check_policy.py` resolves its paths from `__file__`
  for this reason.
- **`sqlglot` must stay installed.** Without it `_extract_sql_facets` returns
  `verb="UNKNOWN"` with a `logger.warning`. Contrary to an earlier note, this does
  not fail silently in a dangerous direction: `UNKNOWN` matches no allow rule, so
  `default_action: deny` refuses the innocent `SELECT` too. Tested by forcing the
  ImportError: the verdict is `DENIED … matched_rule=None`. It fails loudly and
  closed. It is still a pinned dependency, because AGT's `[full]` extra does not
  pull it in — and the README's current wording, "Act 3 stops working", should be
  read as "Act 3 denies everything", which is the safe direction.
- **Do not put `agt lint-policy` or `agt test` in CI.** Section 4.6.
- **Set `gc.auto 0` in the lanes** and let the integrator run `git gc` in the
  shared checkout.
