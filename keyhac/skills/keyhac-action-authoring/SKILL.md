---
name: keyhac-action-authoring
description: Write a Keyhac Action that drives another application's UI - reading tables, filling forms, walking pagination, handling dialogs. Use when the user asks for automation of a system that has no API, or says "make an action that…", "automate this screen", "extract these records", "fill this form from a CSV".
license: MIT. Complete terms in LICENSE.txt
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
print(keymap.ui.focused().reread().dump())        # or ui.window(app="Safari")
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

## Working from a recorded demonstration

If the operator recorded the task (Claude Desktop's "Record a skill" captures
screen, clicks, typing and **voice**, and turns it into a skill), that skill is
your intent source — not your selector source. The split is strict:

- **Take from the recording**: what they were trying to do, which values vary
  and which are constants, where the iteration boundary is, what they checked
  before moving on, which steps were slow. The narration carries the
  transformation that happened in their head; the clicks do not.
- **Never take selectors from it.** A recording has pixels and screenshots. An
  action addressed by pixels is a failed action (rule 2). Re-derive every
  selector from the live tree.

And ask before generating. A recording shows that they set a filter to
"active"; it does not show whether that is a constant or an argument. It shows
one run, not the branch they would have taken on a different item. Turn those
into questions first — demonstration → clarifying questions → generation, never
demonstration → generation.

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
3. **Read back after writing.** `node.set_text()` does this for you and raises
   `FillFailed`. Every write mechanism has a silent-failure mode; the read-back
   is what turns all of them into loud ones. `verify=False` is not a speed
   option: it removes the only signal that the write landed *and* the only
   signal that the clipboard is safe to restore. Use it for a password field or
   not at all.
4. **Read before toggling.** A checkbox press *toggles*. `box.set_checked(True)`
   twice would untick it, and a resumed run would undo its own work.
5. **Preconditions per step, before every press.** Not once at the top. The
   screen changes between item 2 and item 3, and a handler that trusts the
   dialog it saw last time will press the first button of a *different* dialog.
   Check what is actually on screen, and stop if it is not what you expect.
6. **Accumulate outside the thing that can fail.** A list built inside the
   function that raises is discarded with it - losing every page already read,
   which is exactly the failure this class of action exists to avoid.
7. **Bound every loop that follows a link.** A "Next" that links to itself
   otherwise runs until the operator gives up.

## Where the file goes

An action is a module in `~/.keyhac/extensions/`, which is on `sys.path`. The
header is fixed, and the two names that look like they should exist do not:

```python
"""One line: what this drives, on which platform, tree inspected when."""

from keyhac import ThreadedAction, WaitTimeout, FillFailed, UINode, getLogger

logger = getLogger("OpenIssues")     # there is no importable `logger`
```

Emit **no registration at module scope.** `keymap` is not a global - it is the
argument to `configure()` - so `keymap.register_action(...)` beside the class
raises `NameError` the moment the module is imported. Hand the user this to
paste into `~/.keyhac/config.py` instead:

```python
def configure(keymap):
    import open_issues                              # from extensions/
    action = open_issues.OpenIssues()
    keymap.register_action("open-issues", action)   # list_actions / run_action
    kt["Fn-I"] = action                             # optional: bind a key
```

`register_action` is what makes the action visible to `list_actions` and
runnable by `run_action`; **a key binding alone leaves it invisible to both**,
which costs you the run-read-fix loop. Reloading re-imports the module, so an
edit is picked up without restarting Keyhac.

## Structure

Waiting belongs on a worker; reading elements belongs on the loop thread. That
is `ThreadedAction`, and it is not optional - a key press must return control
immediately:

```python
class ExtractThings(ThreadedAction):
    def starting(self):            # loop thread: capture origin, log intent
        logger.info("extracting…")

    def run(self):                 # worker: the whole pipeline, may block
        window = self.ui.window(app="Safari")
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

`self.ui` and every node method dispatch to the loop thread themselves, so an
action's body has no thread ceremony in it. Calling a *wait* on the loop
thread raises rather than freezing the keyboard.

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
the user to say `find` or `set_text`, this skill has failed - fix the
skill instead.

Working examples of every pattern above live in the Keyhac repository - not in
this skill, so they are a fetch away rather than a path away. They are split
into `mac/` and `win/` because an action targets one platform, and `README.md`
records what each one taught. Read the folder for the platform you are on; read
the other one for its shape, never for selectors to copy across.

    https://github.com/crftwr/keyhac/tree/v{VERSION}/examples/actions

If you can fetch a URL, a single file comes back as source from

    https://raw.githubusercontent.com/crftwr/keyhac/v{VERSION}/examples/actions/mac/extract_records.py

and the same path shape reaches any of the others. The tag is pinned to the
Keyhac this skill describes, so what you read matches the API documented here -
follow a link to `main` instead and you may be reading a different version's
code against this version's rules.

If you cannot fetch, nothing above depends on them: every pattern in this
document is stated in full here, and the examples only show them at length.
