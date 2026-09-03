---
name: keyhac-skill-cleanroom
description: Run a clean-room test of a packaged Keyhac skill - write a real action from the bundle alone, with no access to this repository, and record every question the bundle could not answer. Use when the user says "clean-room test the skill", "test the skill bundle", or asks whether the authoring skill is self-contained.
---

# Clean-room testing a skill bundle

**The failures are the product.** This is not a test of whether a model can
write an action; it is a test of whether the *bundle* can teach one. Every
moment the clean room has to guess is a line the skill should have carried, and
the list of those moments is what this produces. A run that goes perfectly and
records nothing has told you nothing.

The uploaded skill is a snapshot with no repository behind it. This repository
is full of the answers — `doc/`, `examples/actions/`, the source itself — and a
session that has read any of it cannot unsee it. So the room is a directory
holding the bundle and nothing else, driven by a session that has never seen
this file.

## Run one

```
make cleanroom CASE=1                       # a case from evals/cases.md
make cleanroom TASK="Save every tab ..."    # a job of your own, intent only
```

That is the whole procedure. It rebuilds the bundles, unpacks *this version's*
into a fresh room under `cleanroom/` (gitignored), opens the case's fixture,
drives the room to completion, audits the transcript, scores what came out,
takes the test action back out of `~/.keyhac/extensions/` and prints
`QUESTIONS.md`.

**Both shipped skills go in**, under `skills/`, because that is the machine a
user actually has - both installed, and the model picking between them. It also
makes the last step answerable: an action task ends by handing the operator a
`configure()` block to bind a key, and what a key expression may say is the
key-table skill's subject. It is the same room to test that skill in when the
task calls for it.

**One precondition it cannot supply for itself:** Keyhac's MCP server switch
(**AI Integration > MCP Server**) must be on, or the room can write an action
and never run it — which fails the skill's own first checklist line and looks
like a skill defect. The run checks the endpoint before it opens a room and
refuses by name if it is off. It is off by default and turns itself off 60
minutes after being ticked, so this is usually the switch.

For a `TASK` of your own, put the screen it talks about up first. `CASE=1`–`5`
have fixtures and open them; 6–10 are about somebody's real build output,
deploy or Slack, and the run does not pretend otherwise.

### Why the room is driven non-interactively

The rule that kept breaking was the operator's. "Answer nothing it asks" is a
discipline, and a discipline fails at the first plausible-sounding question —
at which point the finding it would have produced is gone, and nobody can tell
from the output that it ever existed. A `-p` session has nobody to ask. The
only questions it can raise are the ones it writes into `QUESTIONS.md`, which
is the artefact the exercise exists to produce.

Three things follow, all of them in `scripts/run_room.py`:

- It is granted a **scoped permission list**, not `bypassPermissions`. A
  non-interactive session with no grant has every tool call denied, and the
  first run of this reported, accurately and uselessly, that it could not write
  a file.
- It runs `--restricted` with one explicit `--mcp-config`, so the settings
  files are ignored wholesale. That matters beyond this repository: a released
  copy of the authoring skill installed in `~/.claude/skills` has the same name
  as the bundle under test, and a room that loads it reports last version's
  gaps as this version's — the failure the stale-bundle check exists to
  prevent, arriving by another door.
- The transcript is kept, and `audit()` reads it for the rules RULES.md sets
  and no flag can enforce — chiefly reading another author's action in the
  extensions folder the room must be able to write to. A breach
  **disqualifies the run** rather than being assumed away.

### Driving it by hand

```
make cleanroom CASE=1 ARGS=--dry-run
```

builds the room and prints the command that opens it. **Paste that command.**
Every flag in it is load-bearing, and a plain `cd <room> && claude` is not a
smaller version of it — see below. Then say only *"Read RULES.md, then do
TASK.md."* and **answer nothing it asks**; this is the variant where that is
back on you. Afterwards,
`python .claude/skills/keyhac-skill-cleanroom/scripts/score_room.py <room>`,
and remove the action from `~/.keyhac/extensions/` yourself.

### The room is inside the checkout, and that is a bargain

It was refused there until 2026-09-02, for a reason that has now been paid for
rather than avoided: Claude Code reads `CLAUDE.md` from every parent of the
working directory, so a room here arrives with the source layout, the design
notes and the `UINode` contract already in context. Measured rather than
argued — a session in a room under `cleanroom/`, asked what project it is in
and allowed to read nothing:

| how it was started | answer |
|---|---|
| plain `claude` | "Keyhac2 — the unified Windows/macOS Python keyboard tool" |
| `--restricted` | `UNKNOWN` |

`--restricted` also hides the checkout's own skills — `keyhac-skill-cleanroom`
itself is one, and it names the scoring key — and removes Bash, which is what
makes the file tools' confinement to the working directories true rather than
nominal: a shell can `cat` its way out of any confinement. So the room writes
its action through `--add-dir ~/.keyhac/extensions` and reads its output
through `--add-dir ~/Desktop`, and has no other way out.

Three things guard the bargain, because a guarantee that rests on a flag is
worth exactly what checking it costs:

- **The room's own `CLAUDE.md` is a canary.** Under `--restricted` nothing
  loads it. A session that *can* read it was started without the flag, and the
  file tells it to stop and say so. The contamination is otherwise silent, and
  silent is the whole problem — the operator did start the session in the room.
- **`_isolation_held()` reads the session's init event back.** Bash present
  means restricted mode did not take effect; a `keyhac` slash command means the
  checkout's skills leaked; a server that is not `connected` means the room
  never drove Keyhac and its report is about the endpoint, not the skill.
- **`audit()` no longer trusts "mentions the checkout".** Every honest read of
  the bundle names that path now, so what disqualifies a run is a path under
  the checkout and *outside* the room.

One thing the arrangement cannot hide: the room knows its own working
directory, so it can see it sits under a `keyhac` project. It cannot read a
byte of it, and RULES.md forbids trying, but that hint did not exist when rooms
lived in `~/`.

A room elsewhere in the checkout is refused rather than documented — under
`cleanroom/` or outside the checkout entirely, both of which something actually
protects.

## Score it

The mechanical half prints itself. `QUESTIONS.md` is the actual result, and
**zero questions is not a pass** — a run with nothing to guess at either
measured nothing or broke a rule.

What no script can do is the judgement half: read the case's "must" list in
`keyhac/skills/keyhac-action-authoring/evals/cases.md` against what came out.
**A "must" missed is a skill defect, not a model defect.** Fix the skill,
rebuild the bundle, and re-run — all ten cases if the change was to a rule,
because one new rule is how another gets broken.

`CASE=n` lifts only the case's block quote. The "must" list underneath it is
the scoring key, and typing the task by hand is exactly where it leaks: a
copy-paste one line too long hands the room the answers and produces a run that
looks excellent.

## The room's rules

`room/RULES.md`, copied in by the setup script rather than quoted here. The
rules the room is under have to be *in* the room, and a second copy in this
file is a second thing to drift.

Their shape, so a reader of this file knows what is being measured: only the
bundle may be read; **the MCP endpoint is allowed and the checkout is not** —
reading the screen through Keyhac's own tools is what the skill tells an author
to do, while the package source, or another action written by somebody who had
the repository, is inside information; and anything the skill fails to answer
is written into `QUESTIONS.md` and then *guessed at*, never looked up.
