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
Keyhac tool calls and `writes` the files it wrote itself, and whether an
action ran after each, so a Desktop file changed by hand after the last run is
not taken for an action's output; `events` and `records` read the fixture screens' record
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

| Scenario | Surface | Config | Last run | Open findings | Notes |
|---|---|---|---|---|---|
| 01 orders across pages | action, page `systema` | template | 2.5.0-20261020-030519 | 2 medium, 1 low | #215 #221 #222 fixed |
| 02 merge two systems | action, pages `systema` `systemb` | template | 2.5.0-20261020-030519 | 4 medium, 1 low | #215 #221 fixed; #224 open |
| 03 resume submission | action, page `submit-form` | template | 2.5.0-20261020-030519 (`.mcp`) | 3 medium, 2 low | staged CSV; #226 fixed; #225 open |
| 04 approve queue | action, page `approval-queue` | template | 2.5.0-20261018-203804 | 1 medium, 1 low | |
| 05 submit CSV, report rejects | action, page `submit-form` | template | 2.5.0-20261020-030519 | 1 medium, 3 low | staged CSV; #216 #226 fixed |
| 06 build error to editor | config, terminal `build-failure` | grown | 2.5.0-20261018-203804 | 3 medium, 9 low | |
| 07 watch deploy | action, terminal `deploy` | template | 2.5.0-20261020-030519 | 1 high, 2 medium, 1 low | #214 #222 fixed; #223 open |
| 08 settings backup | action, page `preferences` | template | 2.5.0-20261018-203804 | 1 medium, 2 low | |
| 09 Slack unread | action, the real Slack app | template | | | needs the person |
| 10 click badge coordinates | action, any screen | template | | | needs the person |
| 11 F1 off in VS Code | config | grown | 2.5.0-20261018-203804 | 5 low | |
| 12 Emacs cursor keys | config | short | 2.5.0-20261018-203804 | 2 high, 4 low | |
| 13 Ctrl-X Ctrl-S | config | template | 2.5.0-20261018-203804 | 2 high, 3 medium, 2 low | |

Each scenario runs on one fixture config, the one its front matter names;
every config is some scenario's. The run recorded here ran 06 and 11-13 on
all three, which is where their findings came from.

"Open findings" counts the run's `skill gap` and `product` problems, not yet
filed (`quality-loop findings` lists them by id). The run also reported
eight `harness` problems against qa/: the fixture pages record no paging,
the room's `list_windows` shows the person's own desktop, the deploy screen
can finish before the room's watcher exists, `replay --platform windows`
cannot press a User0 key, and actions.md points at examples the rules forbid.
