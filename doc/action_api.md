# Action API reference

The surface an action uses to drive another application: finding windows,
searching element trees, waiting for the screen to change, filling fields.
Reached through `keymap.ui` (or `self.ui` inside a `ThreadedAction`) and the
methods on the nodes it hands back — the three names below are the only ones a
config imports.

Generated from the docstrings. For how to *write* an action, the authoring
skill in `keyhac/skills/action-authoring/` is the procedural half, and
`examples/actions/` holds working ones.

> **Experimental.** This surface may change in ways a minor release normally
> would not, and an upgrade may require editing actions you have written. The
> unsettled part is `UINode` itself — how an element is identified, and how
> long a node you are holding stays valid — which is the shape everything
> below is built on. The rest of Keyhac's API is not affected; see
> [Authoring actions with an AI agent](mcp.md) for what this covers and what it
> would take to settle it.

**Cross-platform by shape, not by data.** Every method here exists and behaves
the same on Windows and macOS. What differs is the tree it reads: roles are
`AXTable` / `Table`, macOS keeps a control's state in one value where Windows
splits it across Value, ToggleState and IsSelected, and neither platform's
attribute names mean anything to the other. An action is written against a
screen that was inspected first, so it is not portable — the framework is.
`UI.enable_content_access()` is the one deliberately one-sided call, exposed so
an action can make it unconditionally.

**Contents:** [UI](#class-ui) · [UINode](#class-uinode) · [WaitTimeout](#class-waittimeout) · [FillFailed](#class-fillfailed)


## <kbd>class</kbd> `UI`
The action-facing view of the desktop.  Reached as `keymap.ui`. 




---

### <kbd>method</kbd> `UI.at_point`

```python
at_point(x: 'float', y: 'float') → UINode | None
```

The element under a screen point, in whichever application owns it. 

The cheap way into the text layer: the pointer is usually already over the line the user means (design document §6). 

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

### <kbd>method</kbd> `UI.focused`

```python
focused() → UINode | None
```

The element with keyboard focus, as a node. 

The cheapest root there is: a key binding already told you which application and which field the user meant (design document §3.2). 

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

### <kbd>method</kbd> `UI.wait`

```python
wait(
    condition: 'Callable[[], Any]',
    timeout: 'float' = 10.0,
    message: 'str | None' = None,
    interval: 'float | None' = None
) → Any
```

Block until `condition()` is truthy, and return what it returned. 

For a wait that is not "an element appeared" or "an element went away" 
- those are `node.wait_for()` and `node.wait_until_gone()`. Never `sleep`: a fixed delay passes on the machine it was written on, and on a faster one it fails *silently*, acting on a screen that has not arrived. 



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



**Attributes:**
 
 - <b>`role`</b>:  Control role - "AXTextField" (macOS) or "Edit" (Windows).  The  OS's own name; match it with `role=` patterns, which accept the  macOS names with or without their "AX" prefix. 
 - <b>`name`</b>:  The element's label, not its content ("Query" for a field  labelled Query). 
 - <b>`value`</b>:  The element's content (what is typed into the field, "0"/"1" for  a checkbox). 
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

This subtree as indented text - to read, and to hand to Claude. 

---

### <kbd>method</kbd> `UINode.find`

```python
find(**criteria) → 'UINode | None'
```

The first element below this one matching `criteria`, or None. 

Criteria are `role`, `name`, `value`, `identifier`, `text` and `predicate`; patterns are case-insensitive fnmatch with "|" alternation.  None rather than an exception, because only the caller knows whether a missing element is a failed precondition or an expected absence - `wait_for` is the one that insists. 

---

### <kbd>method</kbd> `UINode.find_all`

```python
find_all(**criteria) → list['UINode']
```

Every element below this one matching `criteria`, in tree order. 

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
    **criteria
) → 'UINode'
```

Wait until an element matching `criteria` exists below this one. 

---

### <kbd>method</kbd> `UINode.wait_until_gone`

```python
wait_until_gone(
    timeout: 'float' = 10.0,
    message: 'str | None' = None,
    **criteria
) → None
```

Wait until nothing below this one matches `criteria`. 

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

This node and every descendant, depth first. 

---


## <kbd>class</kbd> `WaitTimeout`
A wait gave up. 

Deliberately its own type, and deliberately an error rather than a False return: an action whose precondition never arrived must stop, not carry on against a screen that is not there (design document §3.7). 

---


## <kbd>class</kbd> `FillFailed`
A write did not take. 

Carries what was attempted, because "the field is still empty" and "the field has the wrong text" want different responses from the caller. 

---

Generated from the docstrings by `make api-reference`. Edit the
docstrings, not this file.
