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
session that has read any of it cannot unsee it. So the test runs in a **fresh
session started outside the checkout**, and this file's second half is written
into the scratch directory for that session to read, because it cannot read
this one.

## In the repository: set up

**One command, because the step that used to be prose is the one a
transcription error ruins quietly.**

```
make skill-bundle
python .claude/skills/keyhac-skill-cleanroom/scripts/open_room.py \
    --task "<the prompt, intent only>"
```

It unpacks *this version's* bundle - and refuses if it is not built, rather
than falling back to an older one, since an older bundle reports an older
skill's gaps as though they were this one's. It copies `room/RULES.md` in,
writes `TASK.md`, opens an empty `QUESTIONS.md`, and prints the `cd … && claude`
that opens the room.

**Rooms live in `~/keyhac-cleanroom/<version>-<timestamp>/`, and never under
the checkout.** Two failures shaped that. A room in a temp directory was
unfindable - the session sent to look for it reported that it did not exist,
which is a fair conclusion from `/var/folders/9k/kmx0…/T/`. And a room *under*
the checkout is contaminated before it begins, because Claude Code reads
`CLAUDE.md` from every parent of the working directory: the source layout, the
design notes and the `UINode` contract arrive in context uninvited. That is the
worse of the two, because the operator did start the session in the room and
has every reason to believe the run was clean. The script refuses such a path
rather than documenting the hazard.

The task is the prompt from one case in
`keyhac/skills/keyhac-action-authoring/evals/cases.md`, or a real job the user
wants. **Only the prompt.** The "must" list under each case is the scoring key
and must not enter the room.

Then run the printed `cd <room> && claude` and say only *"Read RULES.md, then
do TASK.md."* **Answer nothing it asks.** The operator knows the answers, which
is exactly why answering destroys the measurement.

Two preconditions the room cannot supply for itself: Keyhac's **MCP server
switch must be on** (AI Integration > MCP Server) or the room can write an
action and never run it, which fails the skill's own first checklist line; and
whatever screen the task is about has to be **on screen already**.

## In the repository: score

```
python .claude/skills/keyhac-skill-cleanroom/scripts/score_room.py <room>
```

Mechanical rules come out of `evals/check.py`; `QUESTIONS.md` is printed
because it is the actual result. Zero questions is not a pass - a run with
nothing to guess at either measured nothing or broke a rule.

What the script cannot do is the judgement half: read the case's "must" list
against what came out. **A "must" missed is a skill defect, not a model
defect** (`evals/cases.md`). Fix the skill, rebuild the bundle, and re-run - all
ten cases if the change was to a rule, because one new rule is how another gets
broken.

Clean up: remove the room, and remove the test action from
`~/.keyhac/extensions/` - `ActionsSource` imports every module in that folder,
so one left behind shows up in the chooser and can break its listing.

## The room's rules

`room/RULES.md`, copied in by the setup script rather than quoted here. The
rules the room is under have to be *in* the room, and a second copy in this
file is a second thing to drift.

Their shape, so a reader of this file knows what is being measured: only the
bundle may be read; **the MCP endpoint is allowed and the checkout is not** -
reading the screen through Keyhac's own tools is what the skill tells an author
to do, while the package source, or another action written by somebody who had
the repository, is inside information; and anything the skill fails to answer
is written into `QUESTIONS.md` and then *guessed at*, never looked up.
