# Hand-written actions

Step 4 of [`doc/dev/ai-integration.md`](../../doc/dev/ai-integration.md) §10 —
the step that says *do not skip*. These are written by hand, against real
applications, so that the generalisation heuristics in the authoring skill come
from real failures instead of first principles.

They run on macOS today. Each is self-contained:

```bash
python examples/actions/extract_records.py ~/Desktop/records.csv   # Safari
python examples/actions/handle_queue.py                            # Safari
python examples/actions/jump_to_error.py --dry-run                 # Terminal
```

`_runner.py` starts an event loop on the main thread and runs the action on a
worker — the real thread architecture, because that is what makes waiting legal
from a worker at all.

| Action | Exercises | Verified |
|---|---|---|
| [`extract_records.py`](extract_records.py) | pagination, cross-system normalisation, partial failure, CSV output, idempotent re-run | 17 rows from 5 pages across 2 systems; re-running leaves 17 |
| [`handle_queue.py`](handle_queue.py) | per-item branching, the three-beat modal cycle, per-step preconditions | approves 3 items, then refuses the "Delete all records?" dialog and stops |
| [`jump_to_error.py`](jump_to_error.py) | the text layer, and §6's cheapest-rung-first ladder | finds `path:line` in Terminal via the whole-buffer read |

Not written yet: **print every browser tab to PDF**, which §2 calls the densest
single case. It needs print dialogs and writes files, so it wants a deliberate
session rather than being squeezed in beside the others.

---

## What writing them changed

Nine findings. Four were bugs in the framework these actions are written
against, which is the argument for hand-writing them before writing the skill.

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
  step. Writing `AXValue` to a plain text field on macOS did nothing at all,
  silently ([design doc §7.3](../../doc/dev/ai-integration.md)).
- **Key your output rows** so a rerun after a partial failure merges instead of
  duplicating. `(system, id)` here.
- **Report what to redo**, not that something failed. Both actions log the
  systems or the item they stopped on.
- Bound every loop that follows a link. A "Next" that links to itself is
  otherwise a run that ends when the operator gives up.
- Distinguish **"the UI moved on me"** from **"my step failed"** with separate
  exception types: the first means regenerate the action, the second means
  retry it.

## Still missing

**There is no supported way to type into a field.** `AXValue` writes fail
silently, and `InputContext.send_text` types into whatever has focus rather
than into a named element. So of §2's cases, *bulk form submission from CSV*
cannot be written today — the read side is complete and the write side is one
mechanism short. §7.3's paste-by-default with `preserve_clipboard()` is the
design; none of it exists yet. That is the next gap worth closing.
