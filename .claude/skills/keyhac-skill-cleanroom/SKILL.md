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

## Run them

```
make cleanroom                              # every case that needs no screen of yours, in order
make cleanroom CASE=3                       # one case from cases/
make cleanroom TASK="Save every tab ..."    # a job of your own, intent only
make cleanroom ARGS=--list                  # what there is, and what each needs
```

It rebuilds the bundle, starts Keyhac if it is not running, and for each
case: unpacks *this version's* bundle into a fresh room under
`cleanroom/<version>-<time>/<case>/` (gitignored), stands a fixture in for
your `config.py`, opens the case's screen, drives the room to completion,
collects what it made, puts your files back, audits the transcript, runs the
case's outcome check and reads its friction. It ends with one line per case —
verdict, friction, questions, room — writes `FINDINGS.md` beside the rooms,
quits the Keyhac it started, and exits non-zero on a FAIL or a disqualified
run.

**The bundle goes in under `skills/`**, the same single skill a user installs
(two were merged into one in #164). An action task often ends by putting the
action on a key, and the key-table half of the skill is what says how.

**Keyhac is started for you.** With no Keyhac answering, the run starts this
checkout's (`scripts/keyhac_daemon.py`: `python -m keyhac`, as `make run`
does) with the MCP endpoint on and **no timeout**, and quits it at the end —
gracefully, by closing its stdin, and killed only if it has not gone within 15
seconds. Its log is `keyhac.log` beside the rooms. Keyhac itself never opens
the endpoint at start-up, on purpose (`main.py` says why); a clean-room run is
the one start-up that *is* somebody arming it, so the switch is thrown in the
development tool and the product keeps its rule.

A Keyhac that is already answering is used as it is and **left running** —
it is yours. One that is running with the switch off makes the run refuse,
because two Keyhacs cannot both hold the keyboard: tick **AI Integration >
MCP Server** in it, or quit it and let the run start its own. Without an
endpoint the room could write an action and never run it, which fails the
skill's own first checklist line and looks like a skill defect.

**What a run does to your machine, and undoes.** For the length of each room:

- **Your `config.py` is set aside** in `~/.keyhac/cleanroom-stand-in/` and the
  fixture — the shipped template with a few user edits on it, each marked
  `# keep:` — takes its place and is reloaded, so the room reads and edits a
  known file through Keyhac's tools. Copies of your `config.py.bak-*` wait
  there too, because `write_config` prunes old backups. Yours come back, and
  are reloaded, on the way out of every room — an exception, Ctrl-C and
  SIGTERM all unwind through the same `finally`. If the stand-in directory is
  still there at the next start, the run refuses and points at
  `ARGS=--restore`.
- **A case's input files** (`to_submit.csv`) are put on the Desktop. A file of
  yours with the same name refuses the run rather than being overwritten.
- **Everything new** in `extensions/`, on the Desktop and beside `config.py` —
  not only `*.py`: a state file, a helper package, a backup — moves into the
  room's `evidence/` on the way out. `ActionsSource` imports every module in
  `extensions/`, so a leftover would show up in the chooser; `--keep` leaves
  a copy there.

**Screens open on macOS only.** The fixture pages open in Safari and the build
and deploy in Terminal; nothing opens them on Windows yet, so there a run of
everything is the key-table cases, and a screen case named with `CASE=` is
refused rather than run against whatever is in front. `--list` says which.
Cases that need a screen only you can put up — Slack, a mail app — are left
out of a run of everything and run when named.

### The cases

`cases/NN_name.py`, one module per case, run in file order:

| Name | What it holds |
|---|---|
| `PROMPT` | what a user would actually type — the only part that reaches the room |
| `MUST` | the observable outcomes first, then what to read for; the scoring key |
| `SCREEN` | `{"pages": [...]}` or `{"terminal": (script, title)}` from `examples/actions/fixtures/` |
| `STAGED` | `(fixture, name)` files put on the Desktop |
| `NEEDS` | a screen the operator must put up; keeps it out of a run of everything |
| `EDITS_CONFIG` | whether the task asks for `config.py` to change |
| `check(o)` | what went wrong, from an `Outcome` — `[]` passes |
| `reference(o)` | what a correct run would leave, for the test below |

Each prompt stands on its own: a room gets one case and nothing else, so "same
as above" sends it looking for the half it was not given. Each names a real
goal and, where the screen is a fixture, the thing as the screen names it.

**`check()` judges what the run produced, not what the room said.**
`scripts/outcome.py` reads the evidence the way Keyhac would: a `config.py` is
loaded by Keyhac's own `Keymap` on the fake platform and keys are *pressed*
through the fake hook ("with VS Code focused, F1 sends nothing"); a fixture
page reports what was done on it to the runner's server ("REF-201 was never
submitted again"); what Keyhac's tools answered is read from the transcript.
Every case also gets the shared checks — the config still loads, the `# keep:`
lines survive, nothing was rewritten, an edited config was reloaded, and an
action task left `config.py` alone.

**`tests/test_skill_cleanroom.py` keeps the checks honest without a run.** On
both platforms, every check must fail on the untouched starting state and pass
on its `reference`, so a check that can never fail — or never pass — is caught
for free. Change a case, run that test.

`scripts/check.py` is the other half: the rules every action shares, read off
the source (no `sleep`, no coordinates, bounded loops). It is advisory in a
room's score and calibrated by `tests/test_action_authoring_evals.py` against
`examples/actions/` and `fixtures/violates_every_rule.py`.

### Why the room is driven non-interactively

The rule that kept breaking was the operator's. "Answer nothing it asks" is a
discipline, and a discipline fails at the first plausible-sounding question —
at which point the finding it would have produced is gone, and nobody can tell
from the output that it ever existed. A `-p` session has nobody to ask. The
only questions it can raise are the ones it writes into `QUESTIONS.md`, which
is the artefact the exercise exists to produce.

Four things follow, all of them in `scripts/run_room.py`:

- It is granted a **scoped permission list** under `--permission-mode
  dontAsk`, not `bypassPermissions`: what is listed runs, and everything else
  is refused rather than left to `-p`'s default. With no grant at all, the
  first run of this reported, accurately and uselessly, that it could not
  write a file.
- It runs `--restricted` with one explicit `--mcp-config`, so the settings
  files are ignored wholesale. That matters beyond this repository: a released
  copy of the skill installed in `~/.claude/skills` has the same name as the
  bundle under test, and a room that loads it reports last version's gaps as
  this version's — the failure the stale-bundle check exists to prevent,
  arriving by another door.
- It passes `--no-session-persistence`: the transcript is kept in the room,
  and a stored copy only fills the session list with rooms.
- The transcript is kept, and `audit()` reads it for the rules RULES.md sets
  and no flag can enforce — chiefly reading another author's action in the
  extensions folder the room must be able to write to, or the stand-in that
  holds your own config. A breach **disqualifies the run** rather than being
  assumed away.

### Driving it by hand

```
make cleanroom CASE=1 ARGS=--dry-run
```

builds the room and prints the command that opens it. **Paste that command.**
Every flag in it is load-bearing, and a plain `cd <room> && claude` is not a
smaller version of it — see below. Then say only *"Read RULES.md, then do
TASK.md."* and **answer nothing it asks**; this is the variant where that is
back on you. A dry run swaps nothing, opens nothing and collects nothing: a
key-table case driven this way edits your real `config.py`, so use it for
action cases. Afterwards,
`python .claude/skills/keyhac-skill-cleanroom/scripts/score_room.py <room> <case>`,
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
itself is one, and its `cases/` are the scoring key — and removes Bash, which
is what makes the file tools' confinement to the working directories true
rather than nominal: a shell can `cat` its way out of any confinement. So the
room writes its action through `--add-dir ~/.keyhac/extensions` and reads its
output through `--add-dir ~/Desktop`, and reaches `config.py` only through
Keyhac's own tools.

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
- **`audit()` does not trust "mentions the checkout".** Every honest read of
  the bundle names that path, so what disqualifies a run is a path under the
  checkout and *outside* the room — in either spelling, `/Users/...` or
  `C:\Users\...`.

One thing the arrangement cannot hide: the room knows its own working
directory, so it can see it sits under a `keyhac` project. It cannot read a
byte of it, and RULES.md forbids trying, but that hint did not exist when rooms
lived in `~/`.

A room elsewhere in the checkout is refused rather than documented — under
`cleanroom/` or outside the checkout entirely, both of which something actually
protects.

## Score it

The checks print themselves: `FAIL:` lines from the case's `check()`, and
`scripts/check.py`'s advisory findings on each action. The improvement points
are three lists, and **`FINDINGS.md`**, beside the rooms, gathers all of them
for every case in one place:

- **outcome-check failures** — what the run did not get done;
- **friction** (`FRICTION.md` in each room, from `scripts/friction.py`) —
  where the record shows the room stumbling, *whether or not it noticed*: a
  tool call that failed or was refused, an answer from Keyhac that was an
  error or a blank ("focus now: unknown"), a search of the skill that came
  back empty, a file written or an action run more than once, a session that
  ran out of turns, and the sentences where the room said it was unsure or
  had it wrong. Read off the transcript, not the room's account;
- **questions** (`QUESTIONS.md`) — what the room noticed it had to guess.

**Friction with no matching question is the most valuable kind**: it is the
gap the room worked around without seeing it. The first key-table runs are the
example — all three met `describe_keymap` answering "focus now: unknown", and
it took the friction list to show that it was every run rather than one
room's aside. And **zero questions is not a pass** — a run with nothing to
guess at either measured nothing or broke a rule.

**Not every item is a skill gap.** Sort each one before acting on it:

- **skill gap** — the skill should have said it, and a user's agent would hit
  it too. Fix the skill.
- **out of scope** — true of the task or the application, not of Keyhac (how
  this particular site paginates, what the user meant by "free key"). Leave
  it; the skill cannot carry every application.
- **tooling** — about the room's own tools or harness (a file tool that would
  not reach a path, the runner's screen). Fix the runner if it is the
  runner's; otherwise note it and move on.

Only the first changes the skill. Adding the other two only grows it.

What no script can do is the judgement half: the case's MUST list, printed
after its questions, read against the transcript and the reply. **A MUST missed
is a skill defect, not a model defect.** Fix the skill, rebuild the bundle,
and re-run — every case if the change was to a rule, because one new rule is
how another gets broken. `make cleanroom` with no `CASE` is that re-run.

Only a case's `PROMPT` reaches the room. Its `MUST` list sits beside it in the
same module, and typing a task by hand is exactly where it leaks: a copy-paste
one line too long hands the room the answers and produces a run that looks
excellent.

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
