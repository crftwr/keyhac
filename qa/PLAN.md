# Keyhac's quality-loop plan

What the conductor (the `quality-loop` skill) reads before planning a run, and
updates after every one. The harness is
[quality-loop](https://github.com/crftwr/quality-loop); the adapter is
`qa/run.py`, named in `qa/quality-loop.toml`. The design discussion is #205.

## The product, as a room meets it

- **Who:** a user's agent (Claude Desktop, Claude Code, any MCP client)
  writing Keyhac configuration and actions for a person who asked in plain
  words. The person is not in the room.
- **Surfaces under test:** the authoring skill bundle
  (`keyhac-<version>-skill.zip`, built per run by
  `tools/build_skill_bundle.py`) and Keyhac's MCP endpoint, which the skill
  tells an author to use. Not under test: the GUI, the docs site, the source.
- **What the room may touch:** the run's own home (a temp directory that is
  the run's Keyhac's `HOME`): its `.keyhac/extensions/` and `Desktop/`, and
  its `config.py` - a fixture (template, short, grown - `qa/configs.py`) -
  only through Keyhac's tools. The person's own `~/.keyhac` is never touched;
  their Keyhac is quit for the length of a run.
- **Critical means:** the user's config no longer loads, their own lines
  (`# keep:`) or bindings lost, their files outside the room's places
  touched, or a destructive control pressed on a fixture screen.

## Evidence level

**Level 2.** `qa/evidence.py` answers from the run's record: `replay` loads
the room's config in Keyhac's own engine and presses keys through the fake
hook; `bindings` / `preserved` diff the config; `calls` lists the room's
Keyhac tool calls; `events` and `records` read the fixture screens' record
against hand-written ground truth. `qa/scenarios/_always.md` is what every
room is held to.

No live check yet: a key pressed for real would go through the Keyhac on this
machine, which is the product and a key remapper at once. `replay` covers
bindings without that ambiguity.

## Priorities

1. Config edits that keep the user's own config intact (11-13, 06).
2. Actions that drive another application's UI and leave a correct record
   (01-05, 07, 08).
3. Screens only the person can supply (09, 10): run when named, with them
   present.

## Open questions for the person

- The local ledger `cleanroom/FINDINGS-LEDGER.md` predates Issues as the
  ledger. File its open entries as `quality-loop:finding` Issues, or keep it
  until the skill freeze it describes lifts?
- Where the policy lives: a tracking Issue (`quality-loop:loop`) or
  `qa/LOOP.md`.
- Whether a live check is wanted for key-table scenarios, given the remapper
  question above.

## Coverage

| Scenario | Surface | Variants | Last run | Open findings | Notes |
|---|---|---|---|---|---|
| 01 orders across pages | action, page `systema` | - | | | |
| 02 merge two systems | action, pages `systema` `systemb` | - | | | |
| 03 resume submission | action, page `submit-form` | - | | | staged CSV |
| 04 approve queue | action, page `approval-queue` | - | 2.5.0-20261018-014629 | see that run | |
| 05 submit CSV, report rejects | action, page `submit-form` | - | | | staged CSV |
| 06 build error to editor | config, terminal `build-failure` | template short grown | | | |
| 07 watch deploy | action, terminal `deploy` | - | | | |
| 08 settings backup | action, page `preferences` | - | | | |
| 09 Slack unread | action, the real Slack app | - | | | needs the person |
| 10 click badge coordinates | action, any screen | - | | | needs the person |
| 11 F1 off in VS Code | config | template short grown | 2.5.0-20261018-014629 | see that run | |
| 12 Emacs cursor keys | config | template short grown | | | |
| 13 Ctrl-X Ctrl-S | config | template short grown | | | |

Runs before quality-loop 0.2 left no `status.json`; "Last run" is filled in
from the next run on.
