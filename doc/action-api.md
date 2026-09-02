# Action API reference

The surface an action uses to drive another application: finding windows,
searching element trees, waiting for the screen to change, filling fields.
Reached through `keymap.ui` (or `self.ui` inside a `ThreadedAction`) and the
methods on the nodes it hands back — the three names below are the only ones a
config imports.

Generated from the docstrings. For how to *write* an action, the authoring
skill in `keyhac/skills/keyhac-action-authoring/` is the procedural half, and
`examples/actions/` holds working ones.

> **A `UINode` is a snapshot.** It records what an element was when it was read;
> the screen then moves on and the node does not notice. `find`, `find_all` and
> the waits read the live screen each call regardless of the node's age;
> `walk`, `dump`, `children` and the text properties show only what was
> captured. `reread()` refreshes one deliberately, and `StaleElement` is
> raised when a node you are still
> holding refers to something that no longer exists — which is the signal to
> re-find it, as distinct from `FillFailed`, which means the selector was
> wrong. Address elements by `identifier` where there is one, then by role plus
> name or text.

**Cross-platform by shape, not by data.** Every method here exists and behaves
the same on Windows and macOS. What differs is the tree it reads: roles are
`AXTable` / `Table`, macOS keeps a control's state in one value where Windows
splits it across Value, ToggleState and IsSelected, and neither platform's
attribute names mean anything to the other. An action is written against a
screen that was inspected first, so it is not portable — the framework is.
`UI.enable_content_access()` is the one deliberately one-sided call, exposed so
an action can make it unconditionally.

**Contents:** [UI](#class-ui) · [UINode](#class-uinode) · [WaitTimeout](#class-waittimeout) · [FillFailed](#class-fillfailed) · [ActionCancelled](#class-actioncancelled) · [StaleElement](#class-staleelement)


## <kbd>class</kbd> `UI`
The action-facing view of the desktop.  Reached as `keymap.ui`. 




---

### <kbd>method</kbd> `UI.activate`

```python
activate(
    app: 'str' = None,
    title: 'str' = None,
    timeout: 'float' = 10.0,
    retry_interval: 'float' = 2.0
)
```

Bring a window to the front, and wait until it really is. 

```python
ui.activate(app="Google Chrome")
``` 

An act with a postcondition, which is what makes it a verb rather than a wrapper: asking a window to activate is not the same as it being in front, and the difference is where a keystroke goes. It was also the last thing an action had to reach around this API to do - `keymap.find_window(...).activate()` on the loop thread, by hand. 



**Args:**
 
 - <b>`app`</b>:  Application pattern, as `window()` takes it. 
 - <b>`title`</b>:  Window title pattern. 
 - <b>`timeout`</b>:  Seconds before giving up. 
 - <b>`retry_interval`</b>:  Seconds to watch before asking again - an  application starting up can take more than one ask. 



**Returns:**
 The front window's node. 



**Raises:**
 
 - <b>`WaitTimeout`</b>:  It never came to the front. 

---

### <kbd>method</kbd> `UI.at_point`

```python
at_point(x: 'float', y: 'float') → UINode | None
```

The element under a screen point, in whichever application owns it. 

The cheap way into the text layer: the pointer is usually already over the line the user means (design document §6). 

---

### <kbd>method</kbd> `UI.choose`

```python
choose(
    *path: 'str',
    given: 'Condition | Callable[[], Any]' = None,
    until: 'Condition | Callable[[], Any]' = None,
    timeout: 'float' = 10.0,
    retry_interval: 'float' = 2.0
)
```

Pick a command out of the menu bar by its path. 

```python
ui.choose("File", "Export", "As PDF…")
``` 

**macOS only, and that is a fact about the platform rather than a gap here.** There the menu bar is an OS-level part, one per application, readable in full *while it is closed* - so this finds the leaf in the closed tree and presses that, opening nothing on the way. Windows has no menu bar in this sense (`doc/dev/design-notes.md`), and this says so rather than pretending. 



**Args:**
 
 - <b>`*path`</b>:  Menu names from the bar down to the command. 
 - <b>`given`</b>:  What must hold before the command is pressed. 
 - <b>`until`</b>:  What makes it true - a dialog appearing, usually. 
 - <b>`timeout`</b>:  Seconds before giving up, in total. 
 - <b>`retry_interval`</b>:  Seconds to watch `until` before pressing again. 



**Returns:**
 Whatever `until` was satisfied with, or the menu item. 



**Raises:**
 
 - <b>`WaitTimeout`</b>:  The path was not there. 
 - <b>`ValueError`</b>:  This platform has no menu bar. 

---

### <kbd>method</kbd> `UI.click`

```python
click(
    node=None,
    within=None,
    given: 'Condition | Callable[[], Any]' = None,
    until: 'Condition | Callable[[], Any]' = None,
    timeout: 'float' = 10.0,
    retry_interval: 'float' = 2.0,
    **locator
)
```

Find one control and press it, and say what "it worked" means. 

```python
ui.click(role="Button", name="Save", within=dialog,
          until=Appears(identifier="save-panel"))
``` 

**The platform's answer is not evidence.** An accessibility press is accepted by applications that then do nothing with it - measured, an `AXPress` on a control drawn by a Chromium application returns success and moves nothing unless that application has been told an assistive client is present. So `until` is how a caller says what to look for, and the press is repeated every `retry_interval` until it holds. 

**Without `until` it presses once.** A blind retry double-acts - double-save, double-submit - so the retry is the caller's to ask for, and code that does not ask is visibly the weaker code rather than silently the unlucky code. 

**The act is the whole ladder** (`keyhac.core.act`): a click where the screen can prove the control is at the point about to be clicked, the platform's press behind it, the focus last. An action never writes the fallback itself, for the same reason `set_text` owns paste-then-type rather than leaving it to every caller. 



**Args:**
 
 - <b>`node`</b>:  A node already in hand, instead of a locator - the third row  of a list an earlier step enumerated is a thing no locator  says well. 
 - <b>`within`</b>:  Where to look; the focused window by default. 
 - <b>`given`</b>:  What must hold before each attempt - state of the world  somebody else has to have arranged, which this waits for and  never causes. It is re-checked before *every* attempt, and 
 - <b>`that is the whole reason it is a parameter`</b>:  with no `until` it is only sugar for `wait()` then the call, but with one, a hoisted `wait()` guards the first attempt and nothing after it. It also fails distinctly - a precondition that never held and an act that did not take are different diagnoses. 
 - <b>`until`</b>:  What makes it true - what *this act* produces, which is  the definition of it having landed, and a separate clause only  because the platform lies about success. Waiting here for  something the act does not cause fires it again and again into  a door that is not open. None presses once and returns. 
 - <b>`timeout`</b>:  Seconds before giving up, in total. 
 - <b>`retry_interval`</b>:  Seconds to watch the postcondition before pressing  *again* - the only rate here, because how often to *look* is  `wait_for`'s backing-off default and cannot be got expensively 
 - <b>`wrong, while pressing again can`</b>:  too short, and a dialog that takes three seconds to open gets pressed three times. 
 - <b>`**locator`</b>:  `find_elements` keywords - role, name, value,  identifier, text. 



**Returns:**
 Whatever `until` was satisfied with (an `Appears` hands back the node it found), or the node that was pressed when there is no `until`. 



**Raises:**
 
 - <b>`WaitTimeout`</b>:  The target never appeared, or the postcondition never  held. 
 - <b>`StaleElement`</b>:  The target was there and had gone by the time it was  pressed. 

---

### <kbd>method</kbd> `UI.content_access`

```python
content_access(target: 'UINode | None' = None)
```

Turn content access on for the block, and hand it back afterwards. 

```python
with self.ui.content_access():
     ...
``` 

`enable_content_access()` on its own is the one call that changes another application and leaves it changed: nothing turns it off, so the flag outlives the action, the key press and the session. That is not tidiness - it decides behaviour. A press into a Chromium application's *content* is live only while the flag is set, so an action that leaves it on makes the next unrelated press work for reasons nobody chose, and one that never set it makes the same press do nothing at all while reporting success. 

**It does not wait for the application to act on it.** Measured on VS Code: the write is accepted at once and the tree is readable at once, but a *press* only starts working about two seconds later. Waiting here would put that stall in front of every action, to buy what a verified retry gets for nothing - act, check the postcondition, act again (discussion #98). Reading, which is what an action does first, needs no wait at all. 

Nested blocks are counted, so an inner one does not hand back what an outer one still needs. Two different applications at once is not something this counts - an action works in one at a time. 



**Args:**
 
 - <b>`target`</b>:  A node in the application, or None for the focused one. 



**Yields:**
 Whether the platform did anything (False on Windows, which needs nothing equivalent). 

---

### <kbd>method</kbd> `UI.enable_content_access`

```python
enable_content_access(
    target: 'UINode | None' = None,
    enable: 'bool' = True
) → bool
```

Ask a Chromium or Electron application to expose its content. 

**macOS only, and safe to call anywhere.** Chrome, Edge, VS Code and Slack build no accessibility tree until an assistive client asks: a loaded page measured 59 nodes of browser chrome with no document in it, and 119 with every field addressable once asked. Windows needs nothing equivalent - Chromium enables its renderer tree when a UIA client attaches - so this returns False there, and an action calls it either way rather than branching. 



**Args:**
 
 - <b>`target`</b>:  A node in the application, or None for the focused one.  Any node will do; the request goes to its application. 
 - <b>`enable`</b>:  False to give it back, which is polite and measurably  works - Chrome returned to 59 nodes. 



**Returns:**
 True when the platform did something. 

---

### <kbd>method</kbd> `UI.fill`

```python
fill(
    text: 'str',
    node=None,
    within=None,
    given: 'Condition | Callable[[], Any]' = None,
    until: 'Condition | Callable[[], Any]' = None,
    timeout: 'float' = 10.0,
    retry_interval: 'float' = 2.0,
    **locator
)
```

Find one field and write `text` into it. 

```python
ui.fill("REC-001", identifier="record-id", within=form)
``` 

`set_text` already focuses, verifies the focus landed, writes, and reads the value back, raising `FillFailed` naming what each mechanism did - so this verb rarely needs an `until`. What it adds is the locator, the precondition and the one shape every step has. 

**A `FillFailed` is not retried**, and that is deliberate: it means the write happened and the read-back disagreed, so doing it again is the double-act hazard. A field that is not ready yet is a `given=`. 



**Args:**
 
 - <b>`text`</b>:  What to write. 
 - <b>`node`</b>:  A field already in hand, instead of a locator. 
 - <b>`within`</b>:  Where to look; the focused window by default. 
 - <b>`given`</b>:  What must hold before the write. 
 - <b>`until`</b>:  What makes it true, when the read-back is not the whole  story - a form that only enables Save once the field is  valid. 
 - <b>`timeout`</b>:  Seconds before giving up, in total. 
 - <b>`retry_interval`</b>:  Seconds to watch `until` before writing again. 
 - <b>`**locator`</b>:  `find_elements` keywords. 



**Returns:**
 Whatever `until` was satisfied with, or the field. 



**Raises:**
 
 - <b>`FillFailed`</b>:  Every mechanism was tried and the value did not stick. 
 - <b>`WaitTimeout`</b>:  The field never appeared, or `until` never held. 

---

### <kbd>method</kbd> `UI.focused`

```python
focused() → UINode | None
```

The element with keyboard focus right now, as a node. 

The cheapest root there is: a key binding already told you which application and which field the user meant (design document §3.2). 

**Asked each time, not remembered.** This used to hand back `keymap.focus`, which is a snapshot taken while a key was being dispatched - so an action that closed a window and waited for focus to land somewhere else never saw it move, and kept being handed the destroyed element, or the application that no longer had a window. Polling did not help, because polling produces no keystrokes and only a keystroke refreshed it (issue #44). 

`keymap.focus` stays what it was, on purpose. Deciding which key table applies to a keystroke needs the focus *that keystroke* was aimed at, and re-reading it there would race the key it is dispatching. The two are different questions; this is the one an action is asking. 



**Returns:**
  The focused element, or None when nothing has focus or the  platform could not say. None is an answer - a stale element that  fails every attribute read is not. 

---

### <kbd>method</kbd> `UI.node`

```python
node(element) → UINode | None
```

Wrap a platform element as a node, reading nothing below it. 

The escape hatch for an element obtained some other way - through `keymap.focus.element`, or a platform call this API does not cover. 

---

### <kbd>method</kbd> `UI.on_main_thread`

```python
on_main_thread(func: 'Callable[[], Any]') → Any
```

Run `func` on the event-loop thread and return its result. 

Every method here already does this, so an action needs it only to make several reads atomic with respect to a UI that is moving underneath - or to call a platform element method this API does not wrap. 

---

### <kbd>method</kbd> `UI.preserve_clipboard`

```python
preserve_clipboard()
```

Put the clipboard back the way it was afterwards. 

`node.set_text()` already does this around its own paste; this is for an action that uses the clipboard for something else. 

---

### <kbd>method</kbd> `UI.scroll`

```python
scroll(
    within=None,
    by: 'str' = 'down',
    amount: 'float' = 3.0,
    given: 'Condition | Callable[[], Any]' = None,
    until: 'Condition | Callable[[], Any]' = None,
    timeout: 'float' = 10.0,
    retry_interval: 'float' = 0.4,
    **locator
)
```

Turn the wheel over a view until something shows up in it. 

```python
row = ui.scroll(within=table, until=ui.Appears(text="REC-042"))
``` 

**For the rows that are not there until you scroll.** A virtualised list has no element for a row it has not drawn, so no amount of looking finds it and no bound on the walk helps - the only way to read the fortieth row is to bring it into view. That is what this is for, and it is why it is a verb of its own rather than something `click` does on the way (which it also does, for a control it is about to press). 

Scrolling past the target is the hazard, so `retry_interval` is short and `amount` modest: the postcondition is looked at between turns, not after a page of them. 



**Args:**
 
 - <b>`within`</b>:  The view to scroll, or a locator for it below. 
 - <b>`by`</b>:  `"down"` or `"up"`. 
 - <b>`amount`</b>:  Wheel notches per turn. 
 - <b>`given`</b>:  What must hold before each turn. 
 - <b>`until`</b>:  What makes it true. None turns the wheel once. 
 - <b>`timeout`</b>:  Seconds before giving up, in total. 
 - <b>`retry_interval`</b>:  Seconds to watch before turning again. 
 - <b>`**locator`</b>:  `find_elements` keywords, when `within` is not the view  itself. 



**Returns:**
 Whatever `until` was satisfied with, or the view. 



**Raises:**
 
 - <b>`WaitTimeout`</b>:  It never showed up. 

---

### <kbd>method</kbd> `UI.send_key`

```python
send_key(
    keys: 'str',
    given: 'Condition | Callable[[], Any]' = None,
    until: 'Condition | Callable[[], Any]' = None,
    timeout: 'float' = 10.0,
    retry_interval: 'float' = 2.0
)
```

Send a key expression, and say what "it arrived" means. 

```python
ui.send_key("Cmd-P", until=Appears(title="Print"))
``` 

Nothing can confirm a keystroke arrived - the application may be starting, may have a window of its own in front, may be busy - which is why every action that sends one grows a retry loop of its own. This is that loop, once. 



**Args:**
 
 - <b>`keys`</b>:  A key expression, as `InputContext.send_key` takes it. 
 - <b>`given`</b>:  What must hold before each attempt - `Front` is the one  this verb is usually given, because a keystroke goes to  whatever is in front rather than to whatever you meant, and  what was in front when the first attempt went out need not  be in front for the second. 
 - <b>`until`</b>:  What makes it true; None sends it once. 
 - <b>`timeout`</b>:  Seconds before giving up, in total. 
 - <b>`retry_interval`</b>:  Seconds to watch the postcondition before sending  *again*; how often to look is not a parameter, for the reason  `click` gives. 



**Returns:**
 Whatever `until` was satisfied with, or None. 



**Raises:**
 
 - <b>`WaitTimeout`</b>:  The postcondition never held. 

---

### <kbd>method</kbd> `UI.wait`

```python
wait(
    condition: 'Condition | Callable[[], Any]',
    timeout: 'float' = 10.0,
    message: 'str | None' = None,
    interval: 'float | None' = None
) → Any
```

Block until `condition()` is truthy, and return what it returned. 

For a wait that is not "an element appeared" or "an element went away" 
- those are `node.wait_for()` and `node.wait_until_gone()`. Never `sleep`: a fixed delay passes on the machine it was written on, and on a faster one it fails *silently*, acting on a screen that has not arrived. 

`condition` may also be an `Appears` / `Front` / `Gone` / `Reads` / `Stable` rather than a callable - the same question without the lambda, and without the predicate helper an action grows to hold the lambda. 

**This is the wait for what something else causes** - a file appearing, a job finishing, a window someone else opens - where waiting is the whole strategy because nothing you could do would help. What your own act causes is a verb's `until=`; what has to be true before your act goes out is its `given=`. 



**Raises:**
 
 - <b>`WaitTimeout`</b>:  The condition never became true. 

---

### <kbd>method</kbd> `UI.window`

```python
window(
    app: 'str' = None,
    title: 'str' = None,
    class_name: 'str' = None
) → UINode | None
```

A top-level window, as a node to search inside. 

Matches exactly like `keymap.find_window` and `define_keytable`: case-insensitive fnmatch, "|" alternation, ".exe" optional on Windows. 



**Args:**
 
 - <b>`app`</b>:  Application name pattern. 
 - <b>`title`</b>:  Window title pattern. 
 - <b>`class_name`</b>:  Win32 class name pattern (Windows only). 



**Returns:**
 The window's element as a node, or None when nothing matched. 

---

### <kbd>method</kbd> `UI.windows`

```python
windows(app: 'str' = None, title: 'str' = None) → list[UINode]
```

Every matching top-level window, as nodes. 

For the cases where "the window" is ambiguous - a browser with several windows open, or an application whose settings live in a second one. 

---


## <kbd>class</kbd> `UINode`
One element, projected onto the facts both platforms agree on. 

Every member is one of two kinds.  `find`, `find_all`, `reread`, the waits and the text layer read the live UI each time they are called, dispatching to the event-loop thread themselves; `text`, `all_text`, `children`, `walk` and `dump` are free reads of this snapshot, showing the screen as it was when the node was read. 



**Attributes:**
 
 - <b>`role`</b>:  Control role - "AXTextField" (macOS) or "Edit" (Windows).  The  OS's own name; match it with `role=` patterns, which accept the  macOS names with or without their "AX" prefix. 
 - <b>`name`</b>:  The element's label, not its content ("Query" for a field  labelled Query). 
 - <b>`value`</b>:  The element's content (what is typed into the field, "0"/"1" for  a checkbox). 
 - <b>`name_source`</b>:  Which attribute `name` came from - "label",  "description", "help", or None when the element has no name.  An icon-only button typically has no label and answers one of the  other two; nothing at all means it can be addressed only by role  and position. 
 - <b>`identifier`</b>:  A stable identifier where the platform has one - the DOM id  in web content, AXIdentifier in native macOS UI, AutomationId on  Windows.  The best thing to address an element by when present,  since it survives relabelling and localisation. 
 - <b>`rect`</b>:  (x, y, w, h) in screen coordinates, or None. 
 - <b>`depth`</b>:  Distance below the root the walk started from. 
 - <b>`element`</b>:  The platform UIElement, for anything outside this projection. 
 - <b>`children`</b>:  Child nodes, in the platform's own order. 
 - <b>`truncated`</b>:  True when this node's children were cut off by max_depth or  max_nodes - so a caller can tell "leaf" from "gave up here". 


---

#### <kbd>property</kbd> UINode.all_text

The text of this element and everything under it. 

What a table cell needs: web content puts the visible string in a child AXStaticText, so the cell's own `text` is empty and reading a results table off `.text` silently yields blank columns. 

Two kinds of repetition are dropped, both of them WebKit's doing: a child that merely restates its parent (a label's AXStaticText carrying the label again, a heading's child carrying the heading again), and an immediate repeat of the piece just emitted.  Repeats that are *not* adjacent survive on purpose - two cells of a row legitimately holding "37" are data, not noise. 

---

#### <kbd>property</kbd> UINode.text

This element's own label and content, as one string. 

Note `is not None`, not truthiness: an unchecked checkbox's value is 0 and an empty field's is "", and both are facts an action needs.  This is the same trap "read before toggling" exists to avoid. 



---

### <kbd>method</kbd> `UINode.dump`

```python
dump(max_value: 'int' = 60) → str
```

This subtree as indented text - to read, and to hand to an AI agent. 

Prints the snapshot as held: a node from `ui.window()` or `ui.node()` has read nothing below itself yet, so `reread()` first. 

---

### <kbd>method</kbd> `UINode.find`

```python
find(
    max_depth: 'int' = 14,
    max_nodes: 'int' = 1000,
    **criteria
) → 'UINode | None'
```

The first element below this one matching `criteria`, or None. 

Reads the live UI at call time - this node's captured `children` play no part, so an old window node finds what is on screen *now*. None rather than an exception, because only the caller knows whether a missing element is a failed precondition or an expected absence - `wait_for` is the one that insists. 



**Args:**
 
 - <b>`max_depth`</b>:  Depth bound for the underlying walk.  Web content can  nest controls deeper than the default; raise this before  concluding an element is not there. 
 - <b>`max_nodes`</b>:  Node budget for the underlying walk. 
 - <b>`**criteria`</b>:  `role`, `name`, `value`, `identifier`, `text` and  `predicate`; patterns are case-insensitive fnmatch with "|"  alternation. 

---

### <kbd>method</kbd> `UINode.find_all`

```python
find_all(
    max_depth: 'int' = 14,
    max_nodes: 'int' = 1000,
    **criteria
) → list['UINode']
```

Every element below this one matching `criteria`, in tree order. 

The same live read as `find` - the snapshot is not consulted. 



**Args:**
 
 - <b>`max_depth`</b>:  Depth bound for the underlying walk. 
 - <b>`max_nodes`</b>:  Node budget for the underlying walk. 
 - <b>`**criteria`</b>:  As `find`. 

---

### <kbd>method</kbd> `UINode.focus`

```python
focus() → bool
```

Give this element keyboard focus; True when it actually landed. 

---

### <kbd>method</kbd> `UINode.line_at_caret`

```python
line_at_caret() → str | None
```

The line the caret is on - no selection, no pointer. 

---

### <kbd>method</kbd> `UINode.press`

```python
press() → None
```

Press this element, by whichever action name the platform uses. 

---

### <kbd>method</kbd> `UINode.read_text`

```python
read_text() → str | None
```

The whole text content, descending into child text nodes. 

Distinct from the `text` / `all_text` properties, which are free reads of the snapshot: this asks the application, and is what a terminal buffer or a document body needs. 

---

### <kbd>method</kbd> `UINode.reread`

```python
reread(
    max_depth: 'int' = 14,
    max_nodes: 'int' = 1000,
    roles: 'str | None' = None,
    prune=None
) → 'UINode'
```

Read this subtree again, returning a fresh node. 

A UINode is a snapshot: the screen moves on, and nothing here notices. 

---

### <kbd>method</kbd> `UINode.selection`

```python
selection() → str | None
```

The selected text ("" is a real answer, meaning a bare caret). 

---

### <kbd>method</kbd> `UINode.set_checked`

```python
set_checked(checked: 'bool') → bool
```

Set a checkbox, reading it first. True when it pressed. 

---

### <kbd>method</kbd> `UINode.set_text`

```python
set_text(text: 'str', **options) → str
```

Write `text` into this field and prove it arrived. 

Returns the mechanism that worked; raises `FillFailed` when none did. Takes the same options as `keyhac.core.fill.set_text`. 

---

### <kbd>method</kbd> `UINode.wait_for`

```python
wait_for(
    timeout: 'float' = 10.0,
    message: 'str | None' = None,
    max_depth: 'int' = 14,
    max_nodes: 'int' = 1000,
    **criteria
) → 'UINode'
```

Wait until an element matching `criteria` exists below this one. 



**Args:**
 
 - <b>`timeout`</b>:  Seconds before giving up. 
 - <b>`message`</b>:  What was being waited for, for the timeout error. 
 - <b>`max_depth`</b>:  Depth bound for the walk.  Every poll walks the tree  again, so this is a cost bound as much as a reach bound. 
 - <b>`max_nodes`</b>:  Node budget for the walk. 
 - <b>`**criteria`</b>:  As `find`. 

---

### <kbd>method</kbd> `UINode.wait_until_gone`

```python
wait_until_gone(
    timeout: 'float' = 10.0,
    message: 'str | None' = None,
    max_depth: 'int' = 14,
    max_nodes: 'int' = 1000,
    **criteria
) → None
```

Wait until nothing below this one matches `criteria`. 

A bound makes "gone" mean "not found within the bounds": an element deeper than `max_depth` counts as gone. 



**Args:**
 
 - <b>`timeout`</b>:  Seconds before giving up. 
 - <b>`message`</b>:  What was being waited for, for the timeout error. 
 - <b>`max_depth`</b>:  Depth bound for the walk. 
 - <b>`max_nodes`</b>:  Node budget for the walk. 
 - <b>`**criteria`</b>:  As `find`. 

---

### <kbd>method</kbd> `UINode.wait_until_stable`

```python
wait_until_stable(
    quiet: 'float' = 0.3,
    timeout: 'float' = 10.0,
    **bounds
) → None
```

Wait until this subtree stops changing. 

---

### <kbd>method</kbd> `UINode.walk`

```python
walk() → Iterator['UINode']
```

This node and every descendant in the snapshot, depth first. 

A walk over what was captured, not what is on screen: it yields the nodes already held, asking the OS nothing.  On a node read with `max_depth=0` - which is what `ui.window()` and `ui.node()` return - that is this node alone.  `find_all()` is the one that searches the live tree; `reread().walk()` traverses a fresh capture. 

---


## <kbd>class</kbd> `WaitTimeout`
A wait gave up. 

Deliberately its own type, and deliberately an error rather than a False return: an action whose precondition never arrived must stop, not carry on against a screen that is not there (design document §3.7). 

---


## <kbd>class</kbd> `FillFailed`
A write did not take. 

Carries what was attempted, because "the field is still empty" and "the field has the wrong text" want different responses from the caller. 

---


## <kbd>class</kbd> `ActionCancelled`
Raised inside a running action when the user cancels it with Esc. 

**Derived from BaseException rather than Exception, and that is the point.** An action of the kind this framework exists for catches `Exception` around each item, because partial failure is the thing it is built to survive: 

```python
for system in self.systems:
     try:
         self._read_system(system, rows)
     except Exception as error:          # <- would swallow a cancellation
         self.failed.append((system["name"], str(error)))
``` 

Were this an ordinary Exception, pressing Esc there would be recorded as "SystemA failed" and the run would carry on to SystemB - the one thing cancelling must not do. As a BaseException it passes through every such handler while still unwinding the action's `finally` blocks, so progress already written stays written. 

Cancellation is KeyboardInterrupt's cousin, not an error. An action never needs to know this class exists: `wait_for` raises it, and long actions spend most of their time waiting. 

---


## <kbd>class</kbd> `StaleElement`
The element this node was read from no longer exists. 

**A `UINode` is a snapshot.** It records what an element was when the tree was walked; the screen moves on and the node does not notice. That is the contract on purpose - a node that quietly re-read itself would hide exactly the change an action's preconditions exist to catch. 

So the node has to say when it has gone stale, and say it in a way an action can act on. The distinction this exists for is the one §3.7 turns on: 


- `StaleElement` - *the screen moved*. Re-find the element and carry on, or  stop and hand back to a human. The action is not wrong. 
- `FillFailed` / an empty search - *the selector is wrong*. The action was  written against a screen that is not this one, and running it again will  fail the same way. 

Before this existed both arrived as "element supports no press action", because a dead element reports no actions - true, and the least useful true thing to say. 

---

Generated from the docstrings by `make api-reference`. Edit the
docstrings, not this file.
