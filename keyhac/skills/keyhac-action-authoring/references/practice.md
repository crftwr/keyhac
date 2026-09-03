# Driving another application, in practice

Everything an action needs hangs off `self.ui` inside a `ThreadedAction`
(`keymap.ui` elsewhere) and off the nodes it hands back.

**Signatures are in `action-api.md` beside this** — every argument and default,
generated from the docstrings. This file is the other half: which call to reach
for, what each one costs, and which of them fail without telling you. Nothing
here can be generated, which is why it is written by hand and why it is short.

The whole import list, and there is nothing else to reach for:

```python
from keyhac import (ThreadedAction, WaitTimeout, FillFailed, ActionCancelled,
                    StaleElement, UINode, getLogger)

logger = getLogger("MyAction")     # `logger` is NOT importable - make one
```

**`keymap` is not importable either.** It is the argument to `configure()`, so
any module-scope `keymap.…` raises `NameError` at import - reach it through
`self.keymap` inside the action instead. See "Where the file goes" in
`SKILL.md`.

## Getting a root

```python
ui = self.ui                                  # inside a ThreadedAction

node   = ui.focused()                         # the focused element
window = ui.window(app="Safari")              # a window, to search inside
windows= ui.windows(app="Terminal")           # all matching windows
node   = ui.at_point(x, y)                    # whatever is under a point
node   = ui.node(some_platform_element)       # wrap something you already have
```

`window()` / `windows()` match like `define_keytable`: case-insensitive
fnmatch, `|` alternation, `.exe` optional on Windows.

## Searching

```python
field = window.find(identifier="q")           # first match, or None
rows  = window.find_all(role="AXRow|DataItem")
tree  = window.reread(max_depth=12, max_nodes=800)   # fresh snapshot
print(window.dump())                          # indented text, for reading
for node in tree.walk(): ...
```

**Criteria** — `role`, `name`, `value`, `identifier`, `text`, `predicate`, plus
`max_depth` / `max_nodes` on `find`, `find_all`, `reread` and the waits. Role
patterns accept the macOS names with or without the `AX` prefix, but the prefix
is stripped from the *role*, not the pattern: `role="AXTable"` matches nothing
on Windows.

**A node is a snapshot.** `find`/`find_all` read the live UI each time; the
node they return does not update. Re-`find` after the screen changes.

| | Use for |
|---|---|
| `node.text` | its own label + content; keeps falsy values (`0`, `""`) |
| `node.all_text` | the subtree's text — what a **table cell** needs |
| `node.name` | anything with its own label: a heading, a button, a field |
| `node.identifier` | DOM id / AutomationId — but not macOS `_NS:*` nib numbers |
| `node.truncated` | the walk hit a bound here; not a leaf |
| `node.element` | the platform element, for anything this API does not wrap |

## Acting: the verbs

**Do not write a retry loop.** Every act goes through a verb that carries one,
because the platform lies: an accessibility press is *accepted* by
applications that then do nothing with it, so the return value is not evidence
and only a postcondition you state can be.

```python
SAVE  = ui.Locator(role="Button", name="Save", max_depth=7)
SHEET = ui.Locator(identifier="save-panel", max_depth=2)

sheet = ui.click(SAVE, within=dialog, until=ui.Appears(SHEET, within=dialog))
ui.click(OK, within=sheet, until=ui.Gone(SHEET, within=dialog))
print_window = ui.send_key("Cmd-P", given=ui.Front(app="Google Chrome"),
                           until=ui.Appears(app="Google Chrome", title="Print"))
ui.fill("REC-001", FIELD, within=form)
row = ui.scroll(node=table, until=ui.Appears(ui.Locator(text="REC-042"),
                                             within=table))
ui.activate(app="Google Chrome")
ui.menu("File", "Export", "As PDF…")            # macOS menu bar
```

Each returns what its postcondition was satisfied with — an `Appears` hands
back the node it found — so `sheet` above is the panel, not a boolean.

**`given=` and `until=` differ by who causes the thing.** `until` is what
*your act* produces, and is the only place repeating means anything. `given`
is the state of the world somebody else has to have arranged: the browser
being the front window before a Cmd-P, because after a save the application's
own download popup owns the front and swallows it. `given` is re-checked
before **every** attempt, which is why it is a parameter and not a `wait()`
before the call.

Getting them the wrong way round fails in two shapes. A `given` written as
`until` hammers a door that is not open — Cmd-P into the popup, again and
again. An `until` written as `given` waits for what nothing has caused yet.

**Retry is opt-in.** No `until` presses once, because a blind retry
double-acts: double-save, double-submit. Say what "it worked" means and you
get the retry; say nothing and you get one attempt.

## Waiting for what you do not cause

```python
value = ui.wait(lambda: os.path.exists(path), timeout=120)
ui.wait(ui.Stable(within=table, quiet=0.3, max_nodes=300))
node = window.wait_for(identifier="modal", timeout=5)      # a node's own
```

`ui.wait` is for what *something else* causes — a file appearing, a job
finishing — where waiting is the whole strategy. It takes a condition value as
readily as a callable, and returns what the condition returned. A timeout
raises `WaitTimeout` (a `TimeoutError`) — an error, not a `False`, because an
action whose precondition never arrived must stop.

**Wait for the state you expect, not for the old state to change.** "It
differs from what I captured" and "the result arrived" coincide only when the
new value happens to differ: a transform can be the identity — a translation
whose output equals its input — and a wait on *difference* then never returns,
with the screen already correct the whole time. So the conditions name states:

| | Satisfied when |
|---|---|
| `Appears(locator, within=…)` | something matching exists — hands it back |
| `Appears(app=…, title=…)` | that window exists |
| `Gone(locator_or_node, within=…)` | it is not there any more |
| `Reads(node, value="True")` | that node reads as that — the read-back rule |
| `Front(app=…)` | that window is the front one |
| `Stable(within=…, quiet=…)` | the subtree stopped changing |

`Stable` is the escape for what cannot be named, and an escape is easy to
reach for: prefer `Reads` or `Appears` wherever you can say the state. It is a
**precondition only** — quiet is the *absence* of change, so it is satisfied
by the calm before your act's effect starts as readily as the calm after, and
the API refuses it as an `until`. "Click, then let the field re-render" is
`ui.wait(Stable(...))` before the read, not a postcondition on the click.

## Locators are values

Write the selector once and pass it around:

```python
SHEET = ui.Locator(identifier="save-panel", max_depth=2)
```

The same selector appears in the act, its postcondition, the dismissal and a
read — four places anything repairing your action has to find. As a value it
is one, and its `repr` is a Python literal. The keywords are `find`'s own, and
the walk bounds ride along, because a locator that needs a deeper walk needs it
everywhere it is used.

`within` is **not** in the locator: scope is a live node, and a value holding a
handle cannot be written down or compared. `node=` is for a target no locator
describes — "the third tab, from the list the previous step enumerated" — and
it excludes `locator` and `within` rather than quietly outranking them.

**`find` and `find_all` take the keywords, not the value.** A locator reaches
them through `criteria()`, which is what keeps one selector serving a verb and
a plain search both:

```python
ROW  = ui.Locator(role="Row")
rows = table.find_all(**ROW.criteria())
```

Build them where `self.ui` exists — the top of `run()`, not the class body.
`Locator` is reached through `ui`, so there is no name for it at class scope;
they are cheap values and rebuilding them per run costs nothing.

## Reading text the tree cannot reach

```python
buffer = node.read_text()        # whole content, descending into child nodes
line   = node.line_at_caret()    # no selection, no pointer
sel    = node.selection()        # "" is a real answer
```

## Writing, at the node level

The verbs above are what an action normally uses. These are the same acts
without the locator, the precondition or the retry — reach for them when you
already hold the node and there is nothing to verify.

```python
used = field.set_text("REC-001")     # paste → keys; returns which worked
box.set_checked(True)                # reads first; True if it pressed
button.press()                       # AXPress / Invoke / Toggle / Select
ok = field.focus()                   # verified against the system focus
with ui.preserve_clipboard(): ...
```

**`press()` cannot tell an accepted press from an effective one** — that is
the whole reason `ui.click()` exists and takes an `until`. Use the verb
wherever the outcome matters.

| mechanism | cost (macOS / Windows) | caveat |
|---|---|---|
| `paste` (default) | ~105 / 48–95 ms | costs the clipboard; some fields refuse it |
| `keys` (fallback) | ~70 / 114–272 ms | goes through the IME |
| `set_value` (opt-in) | ~5 / 15–33 ms | React/Vue frequently do not observe it |

`set_text` focuses first, verifies focus landed, writes, and reads the value
back; it raises `FillFailed` naming what each mechanism did. `verify=False`
removes the only signal that the write landed *and* that the clipboard is safe
to restore — password fields or nothing.

## Threads

```python
value = ui.on_main_thread(lambda: ...)     # rarely needed
```

Every method above dispatches to the event-loop thread itself. `on_main_thread`
is for making several reads atomic against a moving UI, or for calling a
platform element method this API does not wrap. `starting()` and `finished()`
already run there; `run()` does not, and must not block it — waiting there
raises rather than freezing the keyboard.

## Saying what happened

**Starting an action and collecting it are two calls.** `start_action` returns
at once - these drive real applications and can take minutes - and
`get_action_result` waits for the end and hands back everything the action
logged or printed, with the traceback if it raised. That is how you read your
own failure instead of asking the operator to copy it out of a console window.
`logger.info(...)`, `print(...)` and a logger made with the standard library
all arrive. `cancel_action` stops one, the same as the operator's Esc.

**Shell out with `capture_output=True`.** It is the one thing that does *not*
arrive on its own — a child process writes to a real file descriptor, so
nothing here can see it, and the only place its stderr survives is on the
exception:

```python
subprocess.run(cmd, check=True, capture_output=True, text=True)
```

Without it a failure reads "returned 1" with no reason, and the run-read-fix
loop stops there. `get_action_result` says so explicitly when it happens, so
if you see that note, add the argument.

Log what a rerun would need, not that something broke: which selector, what was
found instead, which item of how many.

## A node is a snapshot

`ui.window(...)`, `find`, `find_all` and a walked tree all hand back nodes that
record what an element **was**. The screen moves on; the node does not notice.
It is deliberate — a node that quietly re-read itself would hide the very
change your preconditions exist to catch.

So do not keep nodes across a state change. Re-find after anything that could
have redrawn:

```python
row = window.find(identifier="row-3")
next_page.press()
row.press()                        # WRONG - that row belonged to page 2
```

**The root you search *from* is not what goes stale.** `find` and `find_all`
read the live UI at call time and ignore the node's captured `children`, so a
window node held across three page loads keeps finding what is on screen now —
which is what makes a pagination loop possible at all, since the title you
would re-find that window by is itself what changes. What must not be kept is
what they hand *back*: the row, cell or button belonging to the page you have
just left.

`node.reread()` refreshes a subtree when you genuinely want the same place
again. Acting on a node whose element has gone raises `StaleElement`, and the
distinction is the one that decides what to do next:

| | means | what to do |
|---|---|---|
| `StaleElement` | the screen moved | re-find it, or stop and report — your action is not wrong |
| `FillFailed`, an empty `find` | the selector is wrong | the action was written against a different screen; regenerating is the fix |

## Cancellation

The user can stop a running action by pressing **Esc**. You get this for free
and should write nothing for it: `ui.wait` and every wait built on it raise
`ActionCancelled` at the next poll, and a long action spends nearly all its
time waiting.

Two things to know, and only two:

```python
except Exception:          # fine - ActionCancelled passes straight through
except BaseException:      # DON'T - this catches it and the action won't stop
```

`ActionCancelled` derives from `BaseException` precisely so the `except
Exception` you write around each item — to survive one bad row without losing
the rest — does not turn a cancellation into "item 7 failed, carrying on".
Your `finally` blocks still run, so progress already written stays written.

If a stretch of work has no wait in it at all (a long parse, a big file
write), call `self.check_cancelled()` in the loop. Otherwise the cancellation
is not noticed until the next wait.

## The one platform-specific call

```python
with ui.content_access():               # handed back on every way out
    ...
```

macOS only, and safe to call anywhere: a freshly started Chromium or Electron
application exposes 13 nodes and no page at all until something asks. Windows
needs nothing equivalent, and neither does Safari — WebKit hands over its
document unasked. That is still not a reason to branch: the call costs a no-op
where it is not needed, and "which engine is this application" is the question
that gets answered wrong. Use it unconditionally.

**Use the context manager, not the bare `enable_content_access`.** Nothing
turns the flag off by itself, so a bare call leaves another application changed
for the rest of its life — and the flag decides whether a press into that
application's *content* works at all, which means the next unrelated action
starts working for reasons nobody chose. An action raises far more often than
it reaches its last line, and the `with` covers every way out.

**It does not wait**, and does not need to: the tree is readable at once, and
a press only starts working about two seconds later, which a verb's `until=`
absorbs by retrying. Do not sleep after it.

Most actions need it less than they think. A control drawn by the application
itself — Chrome's own toolbar and tabs — answers a press unconditionally; it is
the *page* that is conditional. And `ui.click()` lands a real click where the
screen can prove the control is there, which works either way.
