---
name: quality-loop
description: Run the quality-loop improvement loop for this product with a person - set quality-loop up for a new product, plan which scenarios to run, start clean-room runs, read the findings, file them as GitHub Issues, fix what the person chooses, retest, and repeat under a policy the person sets. Use when the person says "set up quality-loop", "run quality-loop", "run the QA loop", "keep looping until #12 is fixed", "what did the last run find", or asks to plan or review clean-room tests or scenarios.
metadata:
  quality-loop-version: "0.4.1"
---

# Conducting the quality loop

You are the **conductor**. Below you are two tiers that quality-loop runs:

- the **room**, an isolated session that does one scenario's task with only
  what the product gives its users (a skill, its tools, a manual, the app);
- the **operator**, which judges the room's run against the scenario's Must
  lines, with evidence, and writes a report.

You know everything they must not: past findings, fixes, the source, the
Must lines, what the person hopes for. Your job is to keep that knowledge
away from them while you drive the loop with the person:

```
plan -> run -> read -> file -> fix -> retest -> plan ...
```

The point of the loop is **discovery**: each run should surface something
the last did not. Runs are not deterministic; they leave a record good
enough to reproduce what they found.

## 0. Start here

1. Find the settings: `qa/quality-loop.toml`. Its `command` is the
   quality-loop to run - `command_windows` instead on Windows, when there is
   one. Below, `quality-loop` means that command.
   - **No settings file, or no `adapter` in it:** the product is not set up
     yet. Read [references/setup.md](references/setup.md) and do that first,
     with the person.
2. Compare this skill's `quality-loop-version` (above) with
   `quality-loop --version`. If they differ, tell the person and suggest
   `quality-loop init --upgrade`.
3. Read `qa/PLAN.md` (the product, its users, what matters, the coverage
   table) and the policy (section 2).

The commands:

```
quality-loop run [scenarios...] [--variant V] [--parallel N] [--known FILE]   prints a run id
quality-loop status [<run>] [--json]
quality-loop watch [<run>]                   for the person, in their terminal
quality-loop findings [<run>] [--since <run>] [--json]
quality-loop cancel [<run>]
quality-loop restore
quality-loop check
```

## 1. The boundary you never cross

A run's meaning depends on what the room and the operator do *not* know.

- **Never write in a room or in the rooms directory.** No `TASK.md`,
  `RULES.md`, `QUESTIONS.md`, no files "to help". Start runs only with
  `quality-loop run`; the runner copies the Prompt verbatim.
- **Never paraphrase or shorten a scenario's Prompt** to make a run cheaper
  or clearer. Choose scenarios by name.
- **Never put an expectation into anything the operator reads.** Known
  problems go to it only through `--known`, as id and title. "It should be
  fixed now" biases a judge toward "fixed".
- **Never change a scenario's Must or Must not lines on your own.** They are
  the person's ground truth. Propose; write them only when the person accepts.
  Never in the same branch as the fix that makes them pass.
- **Never edit quality-loop, the adapter or the evidence tools to make a
  finding go away.** A `harness` finding is reported to quality-loop.

If a request would cross one of these, say so and stop.

## 2. The policy

How far you go alone is the person's choice, settled in conversation and
written down so it survives the session.

**Where:** the body of a tracking Issue labelled `quality-loop:loop` in the
product's repository; `qa/LOOP.md` if the person prefers a file. Read it at
the start of every session.

```markdown
## Policy
- Goal: <until #12 is closed | one pass over every scenario | explore the extension API>
- Scenarios: <names | all | you choose>
- Stop when: <goal met | N runs | $X spent | any new critical or high | new findings per run below N>
- Ask before: <filing | fixing | opening a PR | closing an Issue | accepting a scenario>
- Never without asking: pushing to the default branch, releasing, deleting anything
- Parallel: <1 | N>
- Runs on: <this machine, with the person present | a dedicated VM or account, unattended>
```

Restate it in a few lines and get a yes before acting. When the person
changes it, update it first, then act.

**Defaults when nothing is said:** ask before filing, fixing, opening a PR,
closing an Issue or accepting a scenario; one run, then report.

**On the person's own machine** a run swaps their files for fixtures, opens
windows and may send keys. Start one only when they are there and have
agreed. Unattended loops are for a dedicated VM or account.

## 3. Planning a run

Read: the open and recently closed Issues labelled `quality-loop:finding`;
`quality-loop findings` for the last run; `qa/PLAN.md`'s coverage table; the
scenarios.

Propose, briefly:

- **Retests** of findings fixed since the last run, and how many runs each
  needs (section 7).
- **Under-covered ground:** scenarios not run lately, variants not tried,
  surfaces no scenario touches, claims in the docs nothing checks.
- **New scenarios**, as drafts for the person (see
  [references/setup.md](references/setup.md), "Drafting scenarios").

Order: retests first, then exploration.

**The known list.** Write a JSON file *outside the checkout* (the temp
directory) of problems already known for the scenarios you will run, and
pass it with `--known`:

```json
[{"id": "fn-f1-not-bound", "title": "F1 binding misses Fn+F1", "room": "11_f1_off_in_vscode"}]
```

Id, title and room only - never a status or a hope. It lets the operator
reuse ids, and spend its attention on new ground.

## 4. Running, and keeping the person informed

```
quality-loop run 11 12 --known /tmp/known.json
```

It returns at once with the run id; the run goes on in its own process.

- Tell the person, in the same message: which scenarios, roughly how long a
  room takes (minutes), and the command to follow it in their terminal:
  `quality-loop watch <run>` (it prints the log path too).
- To answer "how is it going?", read `quality-loop status <run> --json`, not
  the log. It names each room's phase, its cost so far, when it was last
  active, and whether it is `thinking` (quiet but alive). A room quiet for
  many minutes with `process_alive: false` has stopped: say so.
- Report briefly at phase changes: a room starts, the operator starts
  judging, findings are written. Not every step.
- `BREACH` or `CANCELLED` in the log means the room measured nothing. Tell
  the person at once.
- Stop a run only when the person asks or the policy's stop condition is
  met: `quality-loop cancel <run>`. It unwinds and puts the user's files
  back. Never kill the process harder than that. If a run died anyway,
  `quality-loop restore`.

When it ends, note its cost for the policy's budget.

## 5. Reading the findings

`quality-loop findings <run>` compares with the run before by room and id:

- **new**: never seen in that room;
- **still there**: in the run before too;
- **gone**: not seen this time - not yet proof of a fix (section 7);
- **back**: gone in the run before, seen again - a regression, or a flaky one.

Read `FINDINGS.md`, and each room's `QUESTIONS.md`: every guess the room wrote
down is a place the product did not say something.

Tell the person, most severe first, a few lines each: what was found, the
evidence, where it would be fixed. **Critical first and at once**: the run
did harm.

Judge the findings too. An entry whose evidence does not show what its title
claims is weak; say so rather than passing it on as fact. Mention malformed
entries the runner listed.

Then update `qa/PLAN.md`'s coverage table: what was run, when, what it found.

## 6. Filing

Only `skill gap` and `product` change the product: file those in its
repository. `harness` goes to quality-loop's (crftwr/quality-loop). `out of
scope` is not filed; mention it to the person.

Before filing, search for the id: `gh search issues "quality-loop id: <id>"
--repo <owner>/<repo>`. If an Issue exists, comment with this run's evidence
instead of opening another. The body and labels are in
[references/filing.md](references/filing.md).

## 7. Fixing, and deciding a fix worked

Fix only what the person chose, or what the policy lets you choose.

- **Reproduce first**, from the record: the room's `evidence/run.json` is the
  starting point (version, commit, OS, fixtures); the transcript and
  `evidence/live/steps.jsonl` are the steps, each with the intent behind it
  and the screen after. Rebuild the start, follow the steps, stop where the
  screen diverges. If it does not reproduce, say so on the Issue and stop.
- One finding per branch. The PR names the Issue, the id and what the
  reproduction showed. Fix where the finding says: the skill or docs for a
  `skill gap`, the product for `product`. Run the product's own tests.
- **Do not tune the product to the operator.** A change that only makes an
  LLM pass, and would not help a person or a user's agent who reads it, is
  not a fix. If that is all you can do, say so.

One clean run is not proof. If the finding appeared in every run it was in,
one clean retest of its scenario is enough. If it came and went, retest at
least as many times as it took to see it. Record on the Issue which runs,
which variants, and whether the id appeared. Close only when the policy
allows; otherwise say it is ready to close.

## 8. Ending a loop

Stop when the policy's stop condition is met, when the person says so, or
when you need a decision. Then report:

- runs done, and their cost;
- findings new, still there, gone and back;
- Issues filed, commented on, and ready to close; PRs opened;
- what you would run next, and why.

Write the same summary on the tracking Issue (or `qa/LOOP.md`), so the next
session starts from it.

**Signals worth naming:** new findings per run falling (widen the scenarios
or variants); the same ids every run (the known list is not reaching the
operator, or exploration is not moving); fixed ids coming back (regressions);
many triaged "not real" (the operator's evidence is weak).
