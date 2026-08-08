# API reference for actions

Everything an action needs hangs off `self.ui` inside a `ThreadedAction`
(`keymap.ui` elsewhere) and off the nodes it hands back. The generated
reference is `doc/action-api.md`; this is the working subset.

The whole import list, and there is nothing else to reach for:

```python
from keyhac import (ThreadedAction, WaitTimeout, FillFailed, ActionCancelled,
                    StaleElement, UINode, getLogger)

logger = getLogger("MyAction")     # `logger` is NOT importable - make one
```

**`keymap` is not importable either.** It is the argument to `configure()`; a
module-scope `keymap.register_action(...)` raises `NameError` at import. See
"Where the file goes" in `SKILL.md`.

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
`max_depth` / `max_nodes` on `find_all` and `reread`. Role patterns accept the
macOS names with or without the `AX` prefix, but the prefix is stripped from
the *role*, not the pattern: `role="AXTable"` matches nothing on Windows.

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

## Waiting

```python
node = window.wait_for(identifier="modal", timeout=5,
                       message="the sheet to open")
window.wait_until_gone(identifier="modal", message="the sheet to close")
window.wait_until_stable(quiet=0.3, max_nodes=300)
value = ui.wait(lambda: something(), timeout=10, message="the job to finish")
```

`ui.wait` returns what the condition returned. A timeout raises `WaitTimeout`
(a `TimeoutError`) — an error, not a `False`, because an action whose
precondition never arrived must stop.

The three-beat, for every menu, modal and sheet:

```python
opener.press()
dialog = window.wait_for(identifier="dialog-title")   # 1: appears
...                                                   # 2: act
window.wait_until_gone(identifier="dialog-title")     # 3: gone
```

Beat 3 is the one that breaks iteration when omitted.

## Reading text the tree cannot reach

```python
buffer = node.read_text()        # whole content, descending into child nodes
line   = node.line_at_caret()    # no selection, no pointer
sel    = node.selection()        # "" is a real answer
```

## Writing

```python
used = field.set_text("REC-001")     # paste → keys; returns which worked
box.set_checked(True)                # reads first; True if it pressed
button.press()                       # AXPress / Invoke / Toggle / Select
ok = field.focus()                   # verified against the system focus
with ui.preserve_clipboard(): ...
```

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
ui.enable_content_access(node)          # ...and False when done
```

macOS only, and safe to call anywhere: Chromium and Electron applications
expose no content until asked (59 nodes of browser chrome → 119). Windows needs
nothing equivalent and returns `False`, so call it unconditionally rather than
branching.
