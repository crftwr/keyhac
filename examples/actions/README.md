# Hand-written actions

Written by hand, against real applications, so that the generalisation
heuristics in the authoring skill come from real failures instead of first
principles. Doing this before attempting any generation was deliberate, and the
reasoning is in
[`doc/dev/ai-integration.md`](../../doc/dev/ai-integration.md).

Five tasks, each written twice: once for macOS in `mac/`, once for Windows in
`win/`, against the same fixture pages where the task is a web page. **They
are two files rather than one with branches in it on purpose** — an action is
generated for one screen, and carrying selectors for a tree it will never meet
buys nothing. The framework underneath is portable; the selectors are not, and
pretending otherwise would teach the wrong thing to anyone reading these as a
template. Read a pair side by side for what a change of platform costs: the
shape survives line for line, and the selectors do not.

All ten are written on the verb layer — `ui.click` / `ui.fill` /
`ui.activate` / `ui.menu` with `until=` and `given=`, selectors as
`ui.Locator` values, postconditions as `ui.Appears` / `ui.Gone` / `ui.Reads` /
`ui.Front` — which is what the authoring skill teaches. The first six were
written before that layer existed, and were rewritten on it (issue #63); what
the rewrite turned up is at the end of this file.

These files are actions and nothing else — no `__main__` block, no `sys.path`
preamble — because that is what a registered action is, and an example that
carried launcher scaffolding would be teaching it. [`tools/run_action_file.py`](../../tools/run_action_file.py)
supplies the launching, from the repository root:

```bash
RUN="python tools/run_action_file.py"

# macOS
$RUN examples/actions/mac/extract_records.py output_path=~/records.csv  # Safari
$RUN examples/actions/mac/handle_queue.py                               # Safari
$RUN examples/actions/mac/submit_from_csv.py                            # Safari
$RUN examples/actions/mac/jump_to_error.py dry_run=true    # Terminal, after fixtures/build_failure.sh
$RUN examples/actions/mac/snapshot_settings.py             # Terminal > Settings

# Windows
$RUN examples/actions/win/extract_records.py                            # Edge
$RUN examples/actions/win/handle_queue.py                               # Edge
$RUN examples/actions/win/submit_from_csv.py                            # Edge
$RUN examples/actions/win/jump_to_error.py dry_run=true    # Windows Terminal, after fixtures/build_failure.ps1
$RUN examples/actions/win/snapshot_settings.py             # Mouse Properties
```

Arguments are `key=value` and become constructor keyword arguments, because that
is where a real action takes them: `kt["Fn-S"] =
SnapshotSettings(output_path=…)` in `configure()` is the call site being stood
in for.

The runner starts an event loop on the main thread and runs the action on a
worker — the real thread architecture, because that is what makes waiting legal
from a worker at all. It is a development tool and deliberately not a shipped
entry point: running under a bare interpreter cannot use the Accessibility
permission granted to Keyhac.app, so it borrows the one held by your terminal
instead. That file's docstring has the full reasoning; an agent should be
reaching the daemon over MCP, where the permission already lives.

`submit_from_csv.py` types, so running it installs a keyboard hook for as long
as it runs — that is what puts the modifier flags on injected keystrokes. It
updates the CSV it reads in place, so by default it works on
`~/Desktop/to_submit.csv`, seeded from the fixture the first time, rather than
on the repository's copy.

| Task | Exercises | macOS | Windows |
|---|---|---|---|
| `extract_records.py` ([mac](mac/extract_records.py), [win](win/extract_records.py)) | pagination with a postcondition naming the next page, cross-system normalisation, partial failure, idempotent CSV | 17 rows from 5 pages across 2 systems; re-running leaves 17 | Edge: the same 17 rows, and a rerun leaves 17 |
| `handle_queue.py` ([mac](mac/handle_queue.py), [win](win/handle_queue.py)) | the three beats of a modal as `click(until=Appears)` / `click(given=Reads, until=Gone)`, per-step preconditions | approves 3 items, then refuses the "Delete all records?" dialog and stops | Edge: the same, after findings 26–28 |
| `submit_from_csv.py` ([mac](mac/submit_from_csv.py), [win](win/submit_from_csv.py)) | the write side: `ui.fill`, read-before-toggle, a submit that is never retried, an answer that must name its row, per-row checkpointing | 3 accepted, 1 rejected with the form's own reason written into the row; rerunning submits only the failure | Edge: the same, after findings 28–29 |
| `jump_to_error.py` ([mac](mac/jump_to_error.py), [win](win/jump_to_error.py)) | the text layer and its cheapest-rung-first ladder; finding the buffer by capability on Windows | finds `parser.py:42` in Terminal via the whole-buffer read | Windows Terminal: finds `…\acme\src\parser.py:42` via the whole-buffer read |
| `snapshot_settings.py` ([mac](mac/snapshot_settings.py), [win](win/snapshot_settings.py)) | tab navigation on a native pane, label association, opening and closing only what it opened | 59 values from Terminal's six settings tabs, into JSON; opened from the menu bar and closed when it was not already open | Mouse Properties: 14 values from five tabs, open or closed; closing what it opened is finding 31 |

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

---

## The rewrite on the verb layer (issue #63)

The first six examples predated `ui.click(until=…)`, `ui.Locator` and the
condition values, so they waited and pressed by hand, wrote `AX`-prefixed role
patterns that cannot match on Windows, and dispatched reads to the main thread
that no longer need it - in other words, they broke rules the skill now states,
while the skill pointed readers at them. Rewriting the macOS five, and writing
four new Windows ones beside the existing port, found six more things. Two of
them were the harness again, which makes finding 10 the most-repeated lesson in
this file.

20. **The runner took the front from the application it was driving.** A bare
    interpreter is a regular macOS application, and AppKit activates it a
    moment after launch - so `ui.activate(node=window)` succeeded and the very
    next `given=ui.Front(node=window)` failed, on the first system only.
    Keyhac.app is `LSUIElement` and never does this; the runner now sets the
    same accessory policy.
21. **The runner had a fake focus provider.** `ui.focused()` answered None, and
    `ui.menu()`, which reaches the menu bar through the focused element,
    reported that the platform had no menu bar. The old examples never asked
    for the focus, so nothing had noticed.
22. **`find()` hands back a subtree cut at its own `max_depth`.** A locator
    bounded to `max_depth=1` found the dialog's detail paragraph and read it as
    `""`, because the text is in a child one level further down. Bound a
    locator for what you will *read* off the node, not only for where it is.
23. **A postcondition can only name a state the screen can show.** The form
    fixture answered "error: Amount must be a number" without saying for which
    record, so two rows refused for the same reason were indistinguishable and
    "wait for this row's answer" was unwritable - a clean-room run had already
    hit it and fallen back to `Stable`. Every answer now names its record. When
    an action cannot say what it is waiting for, look at whether the
    application ever says it.
24. **A run can start in the middle of an item.** A dialog left open by an
    earlier run makes the page behind it inert, so "press Review next item"
    times out. `handle_queue` reads a dialog that is already up exactly as it
    reads one it opened, which is what "start from the screen you are given"
    means for a modal.
25. **`shutil.move` is not an atomic replace on Windows.** It cannot rename
    over an existing file there and quietly falls back to copy-then-delete -
    the half-written checkpoint the temp file existed to prevent. Both
    `submit_from_csv` files use `os.replace`.

The Windows four (`extract_records`, `handle_queue`, `submit_from_csv`,
`jump_to_error`) were written from the quirks and Chromium's documented UI
Automation mapping, not against a tree anyone had looked at, and the table above
says so. Each file names the assumption it rests on - rows and cells both
reported as `DataItem`, an open `<dialog>` as a `Pane`, the terminal buffer
found by its Text pattern - so a first run that fails says which guess was
wrong.

### What the first Windows run corrected

The Windows column was filled in by a run on Windows 11 (10.0.26200, Edge, 200 %
display). Two of the four new actions worked as written; for the other three
the run found the wrong assumption, and every one of them was a confident
guess about Chromium's UI Automation tree that nobody had looked at -
which is the skill's first instruction, broken by the examples that illustrate
it.

26. **An open `<dialog>` is a `Window`, not a `Pane`.** The same control type
    as a top-level window - so the search for it must be scoped to the page's
    `Document`, or the browser itself matches.
27. **A heading is a `Text`.** UI Automation has no heading control type, so
    `role="Heading"` finds nothing on Windows however the page is built. The
    dialog's title is read as its first text.
28. **Chromium passes DOM ids through for controls and headings, not for a
    `<p>`.** `find(identifier="result")` and `identifier="confirm-detail"` are
    None on Edge and answered on WebKit. The result line is found by what it
    says, the detail by its position in the dialog.
29. **`set_checked()` read every Edge checkbox as unchecked.** A Chromium
    checkbox implements Value as well as Toggle, and Value is always `""`;
    `keyhac/core/fill.py` fell back to `ToggleState` only for a *missing*
    value, so the press worked and the read-back said it had not. A library
    bug affecting every Chromium checkbox, found by an example - fixed, with a
    test.
30. **`control main.cpl` exits 1 even when the dialog opens**, and the close
    that followed the walk lost the front to something nobody identified -
    which threw away a read that had succeeded, because the JSON was written
    after the cleanup. Both `snapshot_settings` files now write first and close
    second, and the Windows one ignores the exit status, takes the front again
    before Cancel, and lets the wait for the window be the check.

The old README said the Windows port read **15** values from Mouse Properties;
the old file and the new one both read **14** on this machine, tab for tab. The
count depends on the machine's mouse and driver, not on the code.
31. **`Gone(node)` cannot see a Windows dialog close.** The re-run's Cancel
    closed Mouse Properties at 4.1 s - a foreground log showed it go - and
    `until=ui.Gone(window)` watched the held node for 10 s anyway and reported
    the Cancel as a failure. UI Automation keeps answering for a destroyed
    top-level window, degrading it from `Window` to `Pane` rather than
    failing, which `WinUIElement.is_stale()` documents. The described form,
    `Gone(Appears(title=…))`, goes through window enumeration and sees it;
    `Gone`'s own docstring already says to prefer it, and the example did
    not.
