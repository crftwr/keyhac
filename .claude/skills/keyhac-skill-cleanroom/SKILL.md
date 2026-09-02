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

1. **Build the bundle.** `make skill-bundle`, and note the versioned name:
   `dist/keyhac-action-authoring-<version>-skill.zip`. Test the artifact a user
   would download, not the folder in the tree — the folder has `evals/` in it
   and the bundle does not, and the generated references are copied in at
   bundle time.

2. **Unpack somewhere the checkout is not.**

   ```
   ROOM=$(mktemp -d /tmp/keyhac-cleanroom.XXXX)
   unzip -q dist/keyhac-action-authoring-<version>-skill.zip -d "$ROOM/skill"
   ```

3. **Choose the task.** One prompt from
   `keyhac/skills/keyhac-action-authoring/evals/cases.md`, or a real one the
   user wants. Copy **only the prompt text** — the "must" list underneath is the
   scoring key and must not enter the clean room.

4. **Write the clean room's rules** into `$ROOM/RULES.md` — the second half of
   this file, verbatim — and the prompt into `$ROOM/TASK.md`.

5. **Start a fresh session** whose working directory is `$ROOM`, and hand it:
   *"Read RULES.md, then do TASK.md."* Do not paste anything else. In
   particular do not answer its questions: a question you answer is a finding
   you have destroyed.

## In the repository: score

1. **Mechanical.** `python keyhac/skills/keyhac-action-authoring/evals/check.py
   <the action>` — exit status is the number of files with violations.
2. **Judgement.** Read the case's "must" list against what came out.
3. **The real result:** `$ROOM/QUESTIONS.md`. Each entry is a candidate skill
   edit.

**A "must" missed is a skill defect, not a model defect** (`evals/cases.md`).
Fix the skill, rebuild the bundle, and re-run — all ten cases if the change is
to a rule, because one new rule is how another gets broken.

Clean up: remove `$ROOM`, and remove the test action from
`~/.keyhac/extensions/` — `ActionsSource` imports every module in that folder,
so one left behind shows up in the chooser and can break its listing.

## In the clean room: the rules (copy verbatim into `$ROOM/RULES.md`)

> You are writing a Keyhac action using **only** the skill in `./skill`. Read
> `./skill/SKILL.md` first; its `references/` are the whole of what you know.
>
> **Allowed**
> - Everything under `./skill`.
> - Keyhac's MCP tools, if the endpoint is connected. That is the product's
>   public interface, not inside information — reading the screen with it is
>   what the skill tells you to do.
> - Writing your action to `~/.keyhac/extensions/`, running it, reading its
>   result and its logs.
>
> **Forbidden**
> - The Keyhac checkout, anywhere on this machine: its source, its `doc/`, its
>   `examples/`, its tests, its git history.
> - The installed `keyhac` package's source. Do not read the module you are
>   importing, and do not use `help()`, `inspect`, or the REPL to list a
>   signature the references do not give.
> - Other actions already in `~/.keyhac/extensions/`. They were written by
>   somebody who had the repository.
> - Asking the operator. They know the answer, which is exactly why asking
>   destroys the measurement.
>
> **When the skill does not tell you something, write it down and guess.**
> Append to `./QUESTIONS.md`:
>
> ```
> ## <what you needed to know>
> Where you looked in the skill:
> What you did instead:
> What it cost (wrong first attempt / extra run / gave up):
> ```
>
> This file is the point of the exercise. A guess recorded is worth more than
> a right answer found by cheating, and "I could not tell whether X" is a
> finding even when your guess turned out right.
>
> Finish by leaving three things in `$ROOM`: the action's source, a short note
> on whether it ran and what it produced, and `QUESTIONS.md`.
