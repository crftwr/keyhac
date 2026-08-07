# API reference for actions

Everything here is importable from `keyhac`. Fuller prose in
`doc/configuration.md`; this is the working subset for writing an action.

## Reading the tree

```python
from keyhac import get_ui_tree, find_element, find_elements, format_tree

tree  = get_ui_tree(root, max_depth=14, max_nodes=1000, roles=None, prune=None)
node  = find_element(root, identifier="q")          # None when absent
nodes = find_elements(root, role="AXRow|DataItem")
print(format_tree(tree))                            # indented text, for reading
```

`root` is a platform element (`keymap.focus.element`, an application element, a
window) or a `UINode`.

**Criteria** — `role`, `name`, `value`, `identifier`, `text`, `predicate`.
Patterns are case-insensitive fnmatch with `|` alternation, the same matching
`define_keytable` uses. Role patterns accept macOS names with or without the
`AX` prefix (`role="Button"` matches `AXButton`).

**UINode** carries `role`, `name` (the label), `value` (the content),
`identifier` (DOM id / `AXIdentifier` / `AutomationId`), `rect`, `depth`,
`children`, `truncated`, and `element` - the platform element, for anything
outside this projection.

| | Use for |
|---|---|
| `node.text` | this element's own label + content; keeps falsy values (`0`, `""`) |
| `node.all_text` | the subtree's text - what a table **cell** needs, since web content puts the string in a child |
| `node.name` | anything that has its own label: a heading, a button, a field |

`truncated` is set where the walk hit `max_depth` or `max_nodes`. Check it
before concluding a screen is small.

## Waiting

```python
from keyhac import wait_for, wait_for_element, wait_until_gone, wait_for_stable, WaitTimeout

value  = wait_for(lambda: condition(), timeout=10, message="the job to finish")
node   = wait_for_element(window, identifier="modal", timeout=5, message="the sheet to open")
wait_until_gone(window, identifier="modal", message="the sheet to close")
wait_for_stable(window, quiet=0.3, max_nodes=300)
```

`wait_for` returns whatever the condition returned, so
`node = wait_for(lambda: find_element(...))` is one step. A timeout raises
`WaitTimeout` (a `TimeoutError`) - an error, not a `False`, because an action
whose precondition never arrived must stop.

The three-beat, for every menu, modal and sheet:

```python
press(opener)
node = wait_for_element(window, identifier="dialog-title")   # 1: appears
...                                                          # 2: act
wait_until_gone(window, identifier="dialog-title")           # 3: gone
```

Beat 3 is the one that breaks iteration when omitted: the next cycle starts
while the previous dialog is still up, and presses land in it.

## Writing

```python
from keyhac import set_text, set_checked, press, focus, preserve_clipboard, FillFailed

used = set_text(field, "REC-001")      # paste → keys; returns the method used
set_checked(box, True)                 # reads first; returns whether it pressed
press(button)                          # AXPress / Invoke / Toggle
```

| mechanism | cost | caveat |
|---|---|---|
| `paste` (default) | ~105 ms | costs the clipboard; some fields refuse paste |
| `keys` (fallback) | ~70 ms | goes through the IME |
| `set_value` (opt-in) | ~5 ms | React/Vue frequently do not observe it |

`set_text(field, text, methods=("paste", "keys"), clear=True, verify=True)`
focuses first, verifies focus landed, writes, and reads the value back; it
raises `FillFailed` naming what each mechanism did. Pass
`methods=("set_value",)` deliberately, never for speed.

`preserve_clipboard()` restores the clipboard around a block. `set_text`
already does this internally, and holds the swap until the value has arrived -
restoring earlier races the application.

## Threading

```python
from keyhac import ThreadedAction
from keyhac.core.wait import evaluate_on_main_thread
```

- `starting()` / `finished(result)` — loop thread. UI, windows, elements OK.
  Keep light; they hold the lock the keyboard hook needs.
- `run()` — worker. May block. Wrap element reads in
  `evaluate_on_main_thread(lambda: ...)`; the `wait_*` and `fill` helpers do it
  for you.
- The pool is **one worker shared by every threaded action**, so a long run
  delays the others.

## Platform elements

Reached with `node.element` when the projection is not enough.

```python
element.get_text()             # whole content, descending into child text nodes
element.get_line_at_caret()    # the caret's line - no selection, no pointer
element.get_selection()        # "" is a real answer
element.set_focus()            # verified against the system-wide focused element
UIElement.element_at_point(x, y)
element.get_attribute_value("AXVisibleCharacterRange")   # anything else
```

Attribute *names* are the OS's own (`AXRole` vs `ControlType`); branch on
`keymap.platform` if you need them. The tree API above is portable and usually
makes that unnecessary.

macOS only, for Chromium and Electron targets:

```python
app_element.set_manual_accessibility(True)    # …and False when done
```
