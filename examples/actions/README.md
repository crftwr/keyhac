# Hand-written actions

Step 4 of [`doc/dev/ai-integration.md`](../../doc/dev/ai-integration.md) §10 —
the step that says *do not skip*. These are written by hand, against real
applications, so that the generalisation heuristics in the authoring skill come
from real failures instead of first principles.

Each action targets one OS, and says so in its docstring. Five are macOS; the
sixth is `win/snapshot_settings.py`, the same task as `mac/snapshot_settings.py`
written against Windows. **They are two files rather than one with branches in
it on purpose** — an action is generated for one screen, and carrying selectors
for a tree it will never meet buys nothing. The framework underneath is
portable; the selectors are not, and pretending otherwise would teach the wrong
thing to anyone reading these as a template.

These files are actions and nothing else — no `__main__` block, no `sys.path`
preamble — because that is what a registered action is, and an example that
carried launcher scaffolding would be teaching it. [`tools/run_action_file.py`](../../tools/run_action_file.py)
supplies the launching, from the repository root:

```bash
RUN="python tools/run_action_file.py"

$RUN examples/actions/mac/extract_records.py output_path=~/records.csv  # Safari
$RUN examples/actions/mac/handle_queue.py                               # Safari
$RUN examples/actions/mac/jump_to_error.py dry_run=true                 # Terminal
$RUN examples/actions/mac/submit_from_csv.py                            # Safari
$RUN examples/actions/mac/snapshot_settings.py             # Terminal > Settings
$RUN examples/actions/win/snapshot_settings.py             # control main.cpl
```

Arguments are `key=value` and become constructor keyword arguments, because that
is where a real action takes them: `keymap.register_action("snapshot",
SnapshotSettings(output_path=…))` in `configure()` is the call site being stood
in for.

The runner starts an event loop on the main thread and runs the action on a
worker — the real thread architecture, because that is what makes waiting legal
from a worker at all. It is a development tool and deliberately not a shipped
entry point: running under a bare interpreter cannot use the Accessibility
permission granted to Keyhac.app, so it borrows the one held by your terminal
instead. That file's docstring has the full reasoning; an agent should be
reaching the daemon over MCP, where the permission already lives.

`mac/submit_from_csv.py` types, so running it installs a keyboard tap for as long
as it runs — that is what puts the modifier flags on injected keystrokes.

| Action | Exercises | Verified |
|---|---|---|
| [`mac/extract_records.py`](mac/extract_records.py) | pagination, cross-system normalisation, partial failure, CSV output, idempotent re-run | 17 rows from 5 pages across 2 systems; re-running leaves 17 |
| [`mac/handle_queue.py`](mac/handle_queue.py) | per-item branching, the three-beat modal cycle, per-step preconditions | approves 3 items, then refuses the "Delete all records?" dialog and stops |
| [`mac/jump_to_error.py`](mac/jump_to_error.py) | the text layer, and §6's cheapest-rung-first ladder | finds `path:line` in Terminal via the whole-buffer read |
| [`mac/submit_from_csv.py`](mac/submit_from_csv.py) | the write side: form filling, validation read-back, per-row checkpointing | 3 accepted, 1 rejected with the form's own error written into the row; rerunning submits only the failure |
| [`mac/snapshot_settings.py`](mac/snapshot_settings.py) | tab navigation on a *native* pane, label association, leaving the UI as found | 57 values from Terminal's six settings tabs, into JSON |
| [`win/snapshot_settings.py`](win/snapshot_settings.py) | the same task on Windows: `SelectionItem` for tabs, state read from three patterns | 15 values from Mouse Properties' five tabs, into JSON |

Not written yet: **print every browser tab to PDF**, which §2 calls the densest
single case. It needs print dialogs and writes files, so it wants a deliberate
session rather than being squeezed in beside the others.

---

## What writing them changed

Thirteen findings. Several were bugs in the framework these actions are written
against, and one was a bug in the measurement itself - which is the argument for
hand-writing them before writing the skill.

### In the API

1. **`wait_for_element(..., message=…)` raised `TypeError`.** `message`
   collided with the `**criteria` forwarded to `find_element`. A caller wants
   to say *what step* was waiting ("SystemA to load a result table"), not have
   the selector echoed back. Fixed by making it an explicit parameter.
2. **The main-thread guard was on the wrong function.** It sat on
   `evaluate_on_main_thread`, so a helper that reads an element — called both
   from a worker and from inside a condition already running on the loop —
   raised the moment it was used the second way. Blocking is the sin, not
   reading: the guard moved to `wait_for` / `wait_for_stable`, and nested
   evaluation now runs inline.
3. **A heading's `AXValue` is its level, not its text.** WebKit reports `2` for
   an `<h2>`, so a dialog title read back as `"Approve this item? 2 Approve
   this item?"`. The projection now drops it for `AXHeading`.
4. **`all_text` doubled any child that restates its parent.** Same root cause
   as above, and it also affects labels. Non-adjacent repeats are kept on
   purpose — two cells of a row both saying `37` are data.

### In how you address things

5. **`AXDOMIdentifier` reaches controls, tables and landmarks — not plain
   spans.** `<span id="page">page 1 of 3</span>` collapses into bare
   `AXStaticText` with no identifier, so `find_element(identifier="page")`
   returns `None` on every page. The extraction action was blind from page one
   and reported "stuck". Address such things by the text they show, or use the
   document title, which survives as the web area's name.
6. **Reach for `.name` when the element has its own label**; `all_text` is for
   containers whose text lives in children, like a table cell. Reflexively
   using `all_text` is what surfaced findings 3 and 4.
7. **Error formats are domain knowledge, and there are two.** The first
   `jump_to_error` knew only `path:line:col` and found nothing in a terminal
   showing an ordinary Python traceback (`File "…", line 42`). Which formats
   matter is exactly the kind of thing that belongs in a prompt (§8.5), not
   something to infer.

### In the actions themselves

8. **Accumulate results outside the thing that can fail.** `_read_system`
   collected rows in a local and raised on a bad page, discarding every page it
   had already read — the precise failure this class of action exists to avoid,
   in a file whose docstring claimed otherwise. Rows now accumulate in the
   caller's list.
9. **Preconditions per step, checked before every press.** The queue fixture
   swaps in a *different* dialog with the same shape whose first button
   destroys everything. "Press the first button" and "trust the dialog you saw
   last iteration" both delete the records and report success.

---

## Patterns worth keeping

Candidates for the authoring skill, each earned above rather than assumed:

- Wait for something **specific** and never for time. After a navigation, wait
  for a value that must differ — a page label, a document title — not for the
  tree to look busy.
- **Read the value back** after writing it, and treat a mismatch as a failed
  step. Every write mechanism has a silent-failure mode, and reading back is
  what turns all of them into loud ones
  ([design doc §7.3](../../doc/dev/ai-integration.md)).
- **Focus before writing, and check that it landed.** An unfocused write fails
  silently; unfocused keystrokes go to whatever does have focus.
- **Key your output rows** so a rerun after a partial failure merges instead of
  duplicating. `(system, id)` here.
- **Report what to redo**, not that something failed. Both actions log the
  systems or the item they stopped on.
- Bound every loop that follows a link. A "Next" that links to itself is
  otherwise a run that ends when the operator gives up.
- Distinguish **"the UI moved on me"** from **"my step failed"** with separate
  exception types: the first means regenerate the action, the second means
  retry it.

## The write side, and what it cost to get right

`keyhac.core.fill` closed the gap this file used to end on. Four findings, in
descending order of how much time they wasted:

10. **A broken harness looks exactly like a broken platform.** `send_key("Cmd-V")`
    emitted the V with no Cmd at all, in WebKit *and* in a native text view, so
    convincingly that the shipped clipboard chooser looked broken and a "fix"
    went into the macOS hook. It was neither: `keymap.configure()` is what fills
    the modifier map, the example runner never called it, and with an empty map
    `send_modifier_keys` emits nothing. The hook change was reverted. **Before
    concluding that an OS behaves badly, check that the harness is the same one
    the application uses.**
11. **`set_value` works — if the element is focused first.** An earlier session
    recorded it as doing nothing silently; that run had not focused the field.
    Focused, all three mechanisms work on macOS, at ~5 ms / ~70 ms / ~105 ms.
12. **The clipboard cannot be restored until the target has read it.** Restoring
    right after posting the keystroke races the application and loses: the field
    ends up holding the *previous* clipboard content, which looks exactly like a
    successful paste of the wrong value. Verification now runs inside the swap.
13. **Report why each mechanism failed, not that it did.** Swallowing the
    exceptions is what let finding 10 masquerade as "paste does not work here".


## Step 6: what the skill did not warn about

`snapshot_settings.py` was written as eval case 8 against a screen nobody had
inspected first - Terminal's settings window - specifically to find the skill's
gaps. It found four, now in `references/quirks.md`:

14. **A tab is defined by its parent, not by its role.** macOS tabs are
    `AXRadioButton`s in an `AXTabGroup`, and so are ordinary radio groups
    inside the panels. "Every radio button near the top" collected the
    scrollback option as a seventh tab, and the action failed trying to select
    it. This one was a live failure, not a review catch.
15. **AppKit identifiers are serial numbers.** `_NS:746` is a nib ordinal. The
    skill's "prefer identifier" is right for DOM ids and AutomationIds and
    actively wrong here.
16. **A native field's label is its sibling.** The Columns field has no name at
    all; "Columns:" is a separate `AXStaticText` next to it. Keying on names
    drops every text field on the pane. Geometry pairs them - association, not
    addressing.
17. **Exclude the navigation from what you record.** Tab buttons have values,
    so the first snapshot wrote the whole tab bar's selection state into all
    six panels, and a config diff would light up whenever the window was left
    on a different tab.

Findings 15 and 16 mean the skill's addressing rule now reads "identifier
*when it is a real name*, then name, then text, then position in a known
parent" - which is a genuinely different instruction from what it said before
this action was written.

---

## The port: what survived crossing to Windows

`mac/snapshot_settings.py` was carried to Windows to find out how much of an action
is portable in practice. The answer is encouraging about structure and blunt
about everything else — and the shape of the answer is why the result is a
second file, `win/snapshot_settings.py`, rather than branches in the first.

The port was written as one cross-platform file to begin with. That was the
right *instrument* — running both halves through the same code is how the
differences got measured — and the wrong artefact to keep: these examples are
read as templates, and a template full of `if MAC` teaches an author to write
conditionals for a platform their action will never run on. The two files now
say the same thing more plainly by sitting next to each other.

**The shape survived unchanged.** Find the window by what it contains rather
than by its title; enumerate the tab strip's *own children*; select a tab; wait
for the selection to be reported rather than sleeping; read the panel; put the
original tab back. Every one of those decisions was right on both platforms,
including the two that came out of live failures on macOS — a tab is defined by
its parent, and the navigation must be excluded from what you record. The second
matters *more* on Windows: a `TabItem` has no value, but it does have
`IsSelected`, so the tab strip reappears in the output the moment the reader
learns where Windows keeps state.

**Every selector and every state read was rewritten.** Role names are not a
shared vocabulary, and the prefix-stripping in `match_role` works on the role
rather than on your pattern, so `role="AXTabGroup"` matches nothing on Windows
rather than falling back to something sensible.

**One thing could not be written at all.** A Win32 `TabItem` supports no press
action — `get_action_names()` returned `[]` — and has no value, so neither
selecting a tab nor asking which tab was current was expressible. That is a
platform gap rather than an action's problem, and it was fixed by adding the
`SelectionItem` pattern to `keyhac/platform/win/uielement.py`, pinned against
`TCM_GETCURSEL` in `tests/test_win_focus.py`. **Porting an action is a good way
to find holes in the element API**, which is an argument for doing it early
rather than once.

18. **Windows splits control state three ways.** macOS puts everything in
    `AXValue`. Windows uses `value` for an Edit or ComboBox, `ToggleState` for
    a CheckBox and `IsSelected` for a RadioButton, ListItem or TabItem — and no
    control implements more than one. Reading only the first two found 1 control
    on a panel that had 5.
19. **Geometric label association ported for free.** The rect-pairing written
    for macOS's unnamed `AXTextField`s is what recovers Manufacturer, Location
    and Device status from the Hardware tab, where the Edits are equally
    nameless. It is the one macOS-specific *technique* that turned out not to be
    macOS-specific at all.

Known imperfection, recorded rather than hidden: the Wheel tab reports three
values where four are on screen. The unnamed lines-per-notch Edit has no static
text within the pairing window, so it is dropped — the same failure mode finding
16 describes, at a distance the heuristic does not cover.
