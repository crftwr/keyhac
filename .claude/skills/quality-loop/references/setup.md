# Setting quality-loop up for a product, and planning with the person

This is how a product gets an adapter, a plan and its first scenarios: in
conversation with the person. It is also where the loop comes back to when
the plan needs widening.

`quality-loop init` has already run if this skill is installed: it wrote
`qa/quality-loop.toml` and gitignored the rooms directory. Everything below
is judgement, and yours.

## 1. Read first

README, the docs, the Makefile, `--help`, any skill or MCP definitions the
product ships. **Never ask what can be read.**

## 2. Ask, in a few small rounds

A handful of questions per round, not a form:

- Who uses it: people, a user's agent, both? Through which surfaces: a
  skill, an MCP server, a CLI, a terminal UI, a desktop GUI?
- How is it launched, and can its config and data location be redirected
  (an environment variable, an argument, a home directory)? This is the one
  requirement that matters for safety: a run must never touch the user's
  real data.
- Which of the user's files does it touch, and what must never happen?
  That is what `critical` will mean for this product.
- What matters most to them, and where are they uneasy?

## 3. Propose a plan, and agree on it

- The surfaces in scope.
- The users a room plays: "a user's agent with only the skill", "a
  first-time user with only the manual".
- Priorities.
- The evidence level available:
  - **0**: nothing more than launching it. Judged from the transcript, the
    stood-in files' diff and the screen (text or screenshot).
  - **1**: an event log. The product writes JSON lines to the path in
    `QUALITY_LOOP_EVENTS` at its API boundary - prompts shown, messages,
    exceptions, programs launched. A few lines of hooks; it turns "what the
    screenshot seems to show" into facts.
  - **2**: evidence tools of the product's own (Keyhac's `qa/evidence.py`).

## 4. Write the adapter skeleton and the directories

```
qa/
  quality-loop.toml  written by init; add adapter = "qa/run.py:Product"
  PLAN.md            the product, its users, surfaces, priorities, coverage table
  LOOP.md            the policy, when not kept in a tracking Issue
  run.py             the adapter
  RULES.md           what a room may read and use (often read by run.py)
  scenarios/
    _always.md       what every room is held to (optional)
    NN_name.md
  fixtures/          stand-in files, pages, terminal scripts, file trees
  inputs/            material from outside the repository, with source and date
```

**Do not copy the repository's own docs into `qa/`.** Refer to them by path:
a copied doc goes stale and tests last version's text. Only material from
outside the repository (a doc site snapshot) goes into `inputs/`, with where
it came from and when.

The adapter is a subclass of `quality_loop.adapter.Adapter`. quality-loop's
`docs/adapters.md` has every attribute; the essentials:

- `bundle()`: what the room is given - a skill zip, or a docs directory
  (with `bundle_place = "docs"`). Built for this run, never a stale file.
- `rules()`: RULES.md - what the room may read and use, and how to record a
  guess in QUESTIONS.md.
- `session(room)`: the room's tools and permission list.
- `stand_in(...)`: fixtures in place of the user's files, in a home the run
  owns.
- `apps`: the product's app for the framework to drive -
  `drive.TerminalApp([...], rows=40, cols=120)` for a terminal UI (needs
  `quality-loop[tui]`), `drive.DesktopApp([...], title="...")` for a GUI.
  `room_apps = ("name",)` when the UI itself is the surface under test.
- `restore()`: put back what a dead run set aside.

For a product with no MCP and no evidence tools, a few dozen lines are
enough: a launch command with a redirected home, a window title or a
terminal size, and a docs directory as the bundle.

## 5. Drafting scenarios

Three sources: the intents the person told you, **claims the docs make**
(a key, a menu name, a setting, the result of a step), and gaps in the
coverage table.

Follow quality-loop's `docs/scenarios.md`:

- **The Prompt names an intent, never a mechanism.** What a user would
  type. A prompt that names the API has done the bundle's work for it.
- **Each Prompt stands alone.**
- **Must lines are observable**, outcome first.

**Pair every Must line with how it can be checked** at this product's
evidence level: a file diff or the screen at level 0, the event log at level
1, the product's tools at level 2. Flag a Must line that cannot be checked
while it is still a draft.

A **Live check** section (written for the operator) asks for the real app to
be driven after the room is done: "Open the extension's command with
Ctrl-Shift-G and check the list shows the three trip folders."

**Give reasons with each draft:** why this scenario, and which concern each
Must line answers, so the person can judge quickly.

Must lines are the person's ground truth. Write a draft into `scenarios/`
only when they accept it. Then `quality-loop check`, and the product's own
test if it has one (Keyhac's `tests/test_qa.py`).

## 6. A living plan

`qa/PLAN.md`'s coverage table is updated after every run: scenario, variant,
last run, what it found. It is what you read to decide where to look next, so
planning is never finished at setup; the loop keeps coming back to it.

```markdown
| Scenario | Surface | Variants | Last run | Open findings | Notes |
|---|---|---|---|---|---|
```
