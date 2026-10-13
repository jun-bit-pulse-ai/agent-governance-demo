# What this is, in plain English

No jargon. If you only read one page, read this one.

## The problem

AI assistants have stopped being chatbots. They now *do* things: run database
queries, send email, export files. To do that, someone hands them real access to
real systems.

The usual safety measure is to tell the assistant to be careful — "don't delete
anything", "don't email customers without checking". That's a written note on
the wall. It's not a lock. An assistant that misreads its instructions, or is
tricked by something it reads, will still pull the lever.

The question this demo answers is: **can you put an actual lock there?**

## The idea

Put a gate in front of the tool.

Every instruction stops at the gate first. The gate checks a written rulebook
and either opens or stays shut. If it stays shut, the tool never runs — not
"runs and gets logged", not "runs and gets reversed". It never runs.

```
instruction  →  GATE  →  the tool runs
                  ↓
                 shut  →  nothing happens
```

The important part: the tool itself is unchanged. Nobody edits the database
code to make it safer. The gate sits outside, at the door.

## How the demo proves it

The hard thing about showing a blocked action is that **nothing happens** —
and nothing is hard to photograph. So the demo runs the same instruction twice,
side by side: once with no gate, once through the gate. Then it counts what
changed.

Here's the actual result when the instruction is "delete the customer table":

| | no gate | with gate |
|---|---|---|
| customer records | **0** — all 1,284 destroyed | 1,284 — untouched |
| what ran | the deletion | nothing |

That second column is the whole point. The proof is a number that *didn't move*.

## The six things it demonstrates

1. **Without a gate**, the assistant deletes a table and exports 12,000 private
   customer records. Nothing objects.
2. **With a gate**, both are refused — but a legitimate, properly anonymised
   export still goes through. The gate isn't just "block everything".
3. **The gate reads the instruction properly**, rather than scanning for scary
   words. A query that merely *mentions* deleting is allowed; a deletion
   *disguised* with odd capitals and comments is caught.
4. **Some things wait for a person.** A 40,000-recipient email isn't allowed or
   blocked — it's held until someone signs off.
5. **Who is asking matters.** The same instruction, from a different assistant,
   gets a different answer.
6. **Every decision is written down** in a logbook you can't quietly edit. The
   demo deliberately forges an entry so you can watch the tampering get caught.

## What it does *not* do

Being straight about the limits, because a security demo that oversells itself
is worse than none:

- **It can't stop an assistant that isn't using the gate.** The gate guards the
  doors it's attached to. Hand the assistant a different door and it walks
  through.
- **The "who is asking" check is on the honour system.** The assistant states
  its own name, and nothing verifies it. Useful for keeping jobs separate; not a
  security boundary.
- **The logbook detects tampering, it doesn't prevent it.** Anyone who can
  rewrite the whole logbook consistently gets away with it. It's evidence
  against accidents and against a reader, not against a determined writer.
- **The person who approves things in the demo isn't real.** The *decision to
  ask* is genuine; the "yes" comes from the demo, not a human.

## Is any of this fake?

No. Every yes, no, and wait you see comes from Microsoft's real policy software
doing a real evaluation on your own machine. Nothing is pre-recorded. Change a
rule and the demo's answers change with it.

Building it also turned up **eight genuine defects** in that software — things
like a rule that silently stops working if you misspell one word in it. Those
are written up in the main README for people who want that detail.

## Words you'll see

| Word | What it means here |
|---|---|
| **policy** | the rulebook — a plain text file listing what's allowed |
| **rule** | one line of the rulebook, e.g. "never delete a table" |
| **verdict** | the gate's answer: allow, deny, or wait for a human |
| **the estate** | the pretend company data the demo can damage — customer records, sent mail, exported files |
| **governed / ungoverned** | with the gate / without it |
| **fail closed** | when something breaks, refuse by default (the safe direction) |
| **fail open** | when something breaks, allow by default (the dangerous one) |
| **audit log** | the logbook of every decision |

## Try it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python demo.py
```

That prints the whole story in your terminal, about a screen per act.

For a clickable version where you press a button and watch the two sides
diverge:

```bash
.venv/bin/python ui/server.py      # then open http://127.0.0.1:8765/
```
