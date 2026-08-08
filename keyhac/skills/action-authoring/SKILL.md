---
name: action-authoring
description: Write a Keyhac Action that drives another application's UI - reading tables, filling forms, walking pagination, handling dialogs. Use when the user asks for automation of a system that has no API, or says "make an action that…", "automate this screen", "extract these records", "fill this form from a CSV".
---

# Writing a Keyhac action

An action drives another application's accessibility tree. The body is a
pipeline; the UI is its I/O driver, not its subject:

```
enumerate targets → for each: navigate, wait, read or write, accumulate
                  → aggregate → transform → emit
```

**Write plain Python.** Locating an element is a tree search, branching is
`if`, extraction is a regex, iteration is a loop. Nothing here needs a model at
runtime, and a regex beats one on paths, line numbers, URLs and IDs - it is
*more accurate*, not merely cheaper. If you reach for inference at runtime,
state in a comment why the input space is not closed.

Read `references/api.md` for the API surface and `references/quirks.md` before
debugging anything that "should work". Both are short.

## Before you write anything

Look at the actual screen. Do not write selectors from memory or from what the
page probably contains:

```python
from keyhac import get_ui_tree, format_tree
print(format_tree(get_ui_tree(keymap.focus.element)))
```

If the state you need does not exist yet - a modal, a later wizard step - ask
the user to open it and look again. That costs one message and records nothing.
Ask in conversation for what a screen cannot show: how many items, which steps
are slow, what the branch rule is, what a failure should do.

**Look at the screen on the platform the action will run on, and write for that
one.** An action targets a screen you inspected; it is not expected to be
portable and should not pay for it. Do not add branches for the OS you are not
on — write the second action when there is a second screen, and let the two
files sit side by side.

Role names are not a shared vocabulary: an `AXTextField` is a Windows `Edit`,
and Windows has no `Cell` or `Row` role at all. Write role patterns **without**
the `AX` prefix - it is stripped from the role, not from your pattern, so
`role="Button"` matches both platforms while `role="AXButton"` quietly matches
only macOS. A selector carried over from the other platform's tree finds
nothing and reads like a page that failed to load. If the target is Windows,
read the **Windows:** entries in `references/quirks.md` first; three of the
four are cases where the macOS answer was confidently wrong.

## The seven hard rules

Each of these was a real failure, not a preference. Breaking one produces code
that looks correct and is not.

1. **Never `sleep`.** Wait for something specific: an element appearing, a
   value changing, a page label differing from the one you captured before
   clicking. `sleep` passes on your machine and fails on a slower one - and on
   a faster one it fails *silently*, acting on a screen that has not arrived.
2. **Never coordinates, and address by structure when names run out.**
   `identifier` first *when it is a real name* - a DOM id or an AutomationId.
   macOS `AXIdentifier` values like `_NS:746` are nib serial numbers: ignore
   them. Then name or label, then visible text, then position in a known
   parent ("the tab group's children", not "the radio buttons near the top").
   Code containing pixel positions is a failed generation - though using a
   rect to *associate* a label with an unnamed field is fine, and on native
   macOS panes it is the only thing that works. When the role itself is the
   wrong question, address by **capability**: the thing you can tick is the one
   with a `ToggleState`, whatever it calls itself - and scope that search to the
   panel, or you will find a toolbar button that also toggles.
3. **Read back after writing.** `set_text()` does this for you and raises
   `FillFailed`. Every write mechanism has a silent-failure mode; the read-back
   is what turns all of them into loud ones. `verify=False` is not a speed
   option: it removes the only signal that the write landed *and* the only
   signal that the clipboard is safe to restore. Use it for a password field or
   not at all.
4. **Read before toggling.** A checkbox press *toggles*. `set_checked(box,
   True)` twice would untick it, and a resumed run would undo its own work.
5. **Preconditions per step, before every press.** Not once at the top. The
   screen changes between item 2 and item 3, and a handler that trusts the
   dialog it saw last time will press the first button of a *different* dialog.
   Check what is actually on screen, and stop if it is not what you expect.
6. **Accumulate outside the thing that can fail.** A list built inside the
   function that raises is discarded with it - losing every page already read,
   which is exactly the failure this class of action exists to avoid.
7. **Bound every loop that follows a link.** A "Next" that links to itself
   otherwise runs until the operator gives up.

## Structure

Waiting belongs on a worker; reading elements belongs on the loop thread. That
is `ThreadedAction`, and it is not optional - a key press must return control
immediately:

```python
class ExtractThings(ThreadedAction):
    def starting(self):            # loop thread: capture origin, log intent
        logger.info("extracting…")

    def run(self):                 # worker: the whole pipeline, may block
        window = wait_for(lambda: front_window("Safari")[0], timeout=20)
        rows = []
        for target in targets:
            try:
                self._read_one(window, rows)      # appends as it goes
            except PreconditionFailed as error:   # the UI moved: stop
                self.stopped = str(error)
                break
        return rows

    def finished(self, result):    # loop thread: report, apply, notify
        logger.info(f"{len(result)} rows")
```

Element reads inside `run()` go through `evaluate_on_main_thread(...)`; the
`wait_*` helpers already do it. Calling `wait_for` *on* the loop thread raises
rather than freezing the keyboard.

## Failure, progress, resume

These actions run for tens of minutes over hundreds of items. A read that fails
leaves nothing behind; a write that fails halfway leaves a partial mutation the
remote system has already accepted - so rollback is usually not available and
**checkpointing matters more than undo**.

- Write the outcome **per item, as it happens**, not at the end. A run killed
  mid-way must leave a file that tells the truth about what got through.
- Make reruns safe: key output rows (`(system, id)`), or keep a status column
  and skip what is already done. Then a partial failure is resumed, not
  repeated.
- Report **what to redo**, not that something failed: name the systems, rows or
  items that need another run.
- Use distinct exception types for "the UI moved on me" (regenerate the action)
  and "my step failed" (retry it). They call for opposite responses.
- Capture the application's own validation message after a submit. Without it,
  "write the failure back to the row" cannot be implemented.

## When you are done

Check the generated action against this list, and fix rather than explain:

- [ ] No `sleep`, no coordinates, no bare `time` waits
- [ ] Every wait names what it is waiting for, in words an operator would read
- [ ] Every write is verified; every toggle is read first
- [ ] Preconditions before each press, not just at the start
- [ ] Results accumulate outside the failing scope, and are written per item
- [ ] A second run does not duplicate the first
- [ ] Loops are bounded
- [ ] It says what to redo when it stops

## What belongs in the user's prompt, not in your questions

Domain knowledge is theirs and irreducible: which system, output paths and
naming, column correspondences, which error formats their tools emit, what
should happen on failure. **API names are not.** If you find yourself needing
the user to say `get_ui_tree` or `set_value`, this skill has failed - fix the
skill instead.

Working examples of every pattern above: `examples/actions/` in the Keyhac
repository, split into `mac/` and `win/` because an action targets one
platform, with `README.md` recording what each one taught. Read the folder for
the platform you are on; read the other one for its shape, never for selectors
to copy across.
