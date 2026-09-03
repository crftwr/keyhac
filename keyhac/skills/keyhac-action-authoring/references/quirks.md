# Measured quirks

Every entry here was found by running against a real application, and each one
produces code that looks correct and silently is not. Read this before
debugging something that "should work". Measured on macOS 15 / Safari 18 /
Chrome, 2026-08-06 to 08, and on Windows 11 Home 10.0.26200 / Notepad,
2026-08-07.

Entries prefixed **Windows:** or **Native macOS:** apply to that platform only.
The rest were measured on macOS against web content, which is where most of
them will also be true on Windows - but that is an expectation, not a
measurement, and the Windows entries below are there because three of the four
were expectations that turned out wrong.

## The tree is a DAG, not a tree

A table cell is a child of its **row and its column** - the same element,
reached twice. `get_ui_tree` dedupes on element identity, so this is handled;
it matters if you walk `AXChildren` yourself, where every cell and therefore
every extracted table comes out doubled.

## Web content puts text one level below where you ask

A `<pre>`'s or a cell's own `AXValue` is empty; the string lives in a child
`AXStaticText`. So:

- `cell.text` → `""`, and a results table reads as blank columns
- `cell.all_text` → `"REC-001"`

Use `element.get_text()` for the whole content of a container - it descends to
leaves for exactly this reason.

## A web table's header row is not marked as one

Measured against Safari: a `<thead>`'s `<th>` cells project as plain `AXCell`,
the same role as the `<td>`s below them, and a search for `ColumnHeader|Header`
across the whole table returns nothing. There is no structural test, so take
the first row as the header - and expect it again on every page, because a
paginated table re-emits its `<thead>` and a loop that does not skip it writes
the column names into the data once per page.

## A heading's value is its level

WebKit reports `AXValue` `"2"` for an `<h2>`. Reading a dialog title with
`all_text` therefore gave `"Approve this item? 2 Approve this item?"` - level
in the middle, and the child restating the heading. The projection drops it
now, but the general rule stands: **use `.name` for anything that has its own
label**, and keep `all_text` for containers.

## DOM ids do not reach plain spans

`AXDOMIdentifier` is exposed for controls, tables and landmarks. A
`<span id="page">page 1 of 3</span>` collapses into bare `AXStaticText` with no
identifier at all, so `find_element(identifier="page")` returns `None` on every
page - and an action waiting on it is blind, not broken.

Address such things by their visible text (`find_element(role="AXStaticText",
value="page*of*")`), or use the document title, which survives as the web
area's `name` and changes per page - a good thing to wait on after clicking
Next.

## Chromium and Electron expose nothing until asked

Chrome, Edge, VS Code, Slack: a loaded page was **59 nodes of browser chrome
with no document in it**. After `set_manual_accessibility(True)` on the
application element, 119 nodes with every field addressable. Reversible.

Chrome ignores the targeted `AXManualAccessibility` and only answers to
`AXEnhancedUserInterface`, which is the blunt "an assistive client is present"
signal - VS Code reacts to it by switching to screen-reader rendering. So this
is always an explicit call, never something a walk does implicitly.

## A file panel's OK button reads disabled while it works

Measured on Safari's open panel: `AXEnabled` on the confirming button is
`False` both before and after a row is selected, so the obvious postcondition
for "the file is picked" never holds. What does move is `AXSelectedRows` on the
outline, reached through `node.element.get_attribute_value(...)` - the same
escape the `ToggleState` entry above uses. And one panel is not one shape: the
sheet an application raises inside its window and the panel Cmd-O opens are an
`AXSheet` and a top-level `AXWindow` respectively, so a wait scoped `within=`
the window can never see the second.

## Focus is a precondition for writing

An unfocused `AXValue` write is silently ignored - which is how an earlier
measurement concluded that `set_value` never works. Unfocused *keystrokes* are
worse: they go to whatever does have focus, i.e. the window behind. `set_text`
verifies focus against the system-wide focused element and refuses to write
when it did not land.

## The clipboard cannot go back until the target has read it

Restoring right after posting the paste keystroke races the application, and
loses: the field ends up holding whatever was on the clipboard *before*, which
looks exactly like a successful paste of the wrong value. Verify inside the
swap, which is what `set_text` does.

## AX notifications do not arrive for web content

Native Cocoa applications post generously (`AXWindowCreated`, `AXValueChanged`,
`AXUIElementDestroyed`). A Safari `<dialog>` opening posted **nothing at all**,
registered on the application element and on the `AXWebArea` alike — and
Chrome, measured separately with its tree exposed and a driven page change,
posted nothing either. So this is Chromium as well as WebKit, not a WebKit
quirk. Notifications also do not bubble, so "wait for an element to appear"
cannot be registered anywhere useful.

Measured against Safari; an Electron app's notifications are untested, and
Windows has no observer at all. Neither changes what you should write — the
answer everywhere is to poll, which `wait_for` already does.

This is why `wait_for` polls (20 ms backing off to 250 ms) and why there is no
subscription API to reach for: one existed and was removed once measured.
Polling is what finds the change, and it is fast enough - a modal was seen
10-25 ms after the click.

## Terminals and editors do answer whole-value reads

Terminal.app's `AXTextArea` returns the entire scrollback through `AXValue`,
and `get_line_at_caret()` returns the prompt line. So the cheap path - read
everything, take the last match - works, and neither a selection nor the
pointer is needed. iTerm2 untested.

Measured on Windows too (`tools/text_pattern_survey.py` in the Keyhac
repository, 2026-08-07): **Windows
Terminal** exposes the buffer as a `Text` element - 366 characters of
scrollback, with `get_line_at_caret()` returning one line of it - and **VS
Code** exposes the editor as an `Edit` named for the open file. Both answer the
cheap path, so §6's ladder holds on both platforms. Find them by capability
(`"SelectedText" in get_attribute_names()` is true only where TextPattern is
supported) rather than by role: the terminal's buffer is a `Text`, Notepad's is
a `Document` and VS Code's is an `Edit`.

## Windows: an Electron app's text is not there on the first read

VS Code's window was probed twice, minutes apart, by the same code. The first
time it offered 12 Text-pattern elements and **none of them held the buffer** -
the `Document #RootWebArea` was present and empty of content. The second time
it offered 26, two of which held it.

Chromium turns renderer accessibility on in response to a UIA client attaching,
and it does not finish doing so before that client's first read returns. (The
exact mechanism was not isolated - the process was the operator's own running
VS Code and could not be restarted to test cold - but the observation was
repeatable and the consequence does not depend on the cause.)

So on Windows an Electron app needs **no** equivalent of macOS's
`set_manual_accessibility()`; it needs a *retry*. Never conclude "this window
has no text" from one read - `wait_for` the element you expect, which is what
rule 1 already tells you to do, and is one more reason not to write the read as
a single shot.

## Native macOS: identifiers are serial numbers

`AXDOMIdentifier` in web content is usually a real name - though not always;
see the next entry. AppKit's `AXIdentifier` is usually `_NS:746` - a nib
ordinal that changes when the window is edited and means nothing to a reader.
**"Prefer identifier" holds for DOM ids and AutomationIds that name something,
and is actively wrong for `_NS:*`.** Address native controls by name, and fall
back to structure.

## A DOM id can be a serial number too

Google Translate's language chips carry ids `#i14`-`#i21`, and the id→language
mapping is reassigned on every page load, ordered by recently used languages:

```
load 1:  #i15='英語'    #i16='日本語'
load 2:  #i15='日本語'  #i16='英語'
```

So "DOM ids are real names" is a habit of well-built pages, not a property of
the platform - and a generated id is not marked as generated; `i15` looks no
more synthetic than `q` looks hand-written. Before an action relies on what an
id *means*, read the same screen twice - reload between reads - and confirm
the id→content mapping survived. If it did not, address the element by name or
visible text and treat the id as noise. Measured twice, 2026-08-08.

## Native macOS: a field's label is its sibling

A text field on a settings pane has **no name at all** - the Columns field is
`AXTextField = "120"`, and the string "Columns:" is a separate `AXStaticText`
beside it, with nothing in the tree linking them. A snapshot keyed on names
silently drops every text field on the screen.

Pair them by geometry: the label is the static text immediately to the left
whose vertical centre matches. That is *association*, not addressing - the
field is still found by role - and it is the one legitimate use of `rect`.

## A tab is defined by its parent, not by its role

macOS tabs are `AXRadioButton`s inside an `AXTabGroup`, and so are ordinary
radio groups inside the panels. "Every AXRadioButton near the top of the
window" therefore collects real tabs *plus* whatever radio group the current
panel happens to show - a walk that then tries to "select" the scrollback
option as if it were a tab. Enumerate the tab group's own children.

While walking panels, exclude that navigation from what you record: the tab
buttons have values too, so a naive snapshot writes the whole tab bar's
selection state into every panel and a config diff lights up whenever the
window was left on a different tab.

## A settings window's title is its selected tab

Terminal's is "Profiles", then "Window", then "Keyboard" as the walk proceeds.
Find such a window by shape - `AXDialog` containing an `AXTabGroup` - and never
by a fixed title.

## Windows: the role vocabulary is not the macOS one

Two separate things bite here.

**The `AX` prefix is stripped from the role, not from your pattern.** So
`role="Button"` matches a macOS `AXButton` and a Windows `Button` alike, while
`role="AXButton"` matches **only** macOS - `AXTable` will not find a Windows
`Table` even though that role exists. Write patterns *without* the prefix and
they work on both. Every example under the repository's `examples/actions/mac/`
breaks this, having been written on macOS - which is also why they live in a
`mac/` folder rather than looking like the general case.

**And where the names have no pair, no rule saves you.** Windows has
`Table`, `DataGrid`, `DataItem`, `Header` and `HeaderItem` - and no `Cell` or
`Row` at all, so an `AXRow` / `AXCell` walk has no direct translation and the
extraction has to be rewritten around what the platform does expose. macOS
`AXTextField` and `AXTextArea` are Windows `Edit` and `Document`.

Measured on Notepad: the editor is a `Document`, the Find field an `Edit`,
"Match case" a `MenuItem`, toolbar controls `Button`. Use alternation when an
action is meant to be portable (`role="Document|Edit|TextArea"`), and look at
the tree on both platforms rather than trusting the mapping.

## Windows: WinUI text controls drop and reorder injected input

In Windows 11's Notepad - a XAML editor - `"hello-keys"` typed with the `keys`
mechanism arrived as `"helloke-ys"`; a `Ctrl-V` came through as a bare `v`; and
an injected `Ctrl-V` is dropped outright often enough to need retrying. The
same strings down the same code path land intact **30/30** in a plain Win32
control, so this is XAML's input handling, not `SendInput` ordering.

Two consequences. Prefer `paste` over `keys` on Windows - which is already the
default, now for a second and independent reason. And **this is what the
read-back is for**: every one of those corruptions arrived as a loud
`FillFailed` naming the text actually found, rather than as a document that was
quietly wrong. An action that writes with `verify=False` on this platform is
choosing not to be told.

## Windows: a toggle is found by its ToggleState, not by its role

Waiting for a `CheckBox` in Notepad's Find panel waits forever: it has none.
"Match case" is a `MenuItem`, and it is one press deeper than the panel, behind
a button called "More options" - so both the role and the depth were wrong
guesses. An element with a **`ToggleState`** is one `set_checked` can drive
whatever it calls itself; find it with
`find_element(panel, predicate=lambda n: n.element.get_attribute_value("ToggleState") is not None)`.

Scope that search to the panel, not the window. Run against the whole Notepad
window it returns the formatting toolbar's **Bold** button - which also has a
`ToggleState`, and has nothing to do with Find. Capability answers "can I drive
this"; only the subtree answers "is this part of what just opened".

This is the Windows form of "a tab is defined by its parent, not by its role"
above. The general rule both are instances of: **address a control by what it
can do and where it sits, not by what it calls itself.**

## Windows: a control's state lives in one of three places

macOS puts it all in `AXValue` - a checkbox, a radio button and a text field
are all read the same way. Windows splits it across three patterns, and a
reader that knows about only one silently records a fraction of the screen:

| Control | Where the state is |
|---|---|
| Edit, ComboBox | `value` |
| CheckBox | `ToggleState` (0 off, 1 on, **2 indeterminate**) |
| RadioButton, ListItem, TabItem, TreeItem | `IsSelected` |

Measured on Internet Properties: reading only `value` and `ToggleState` found
**1** control on the Security panel; adding `IsSelected` found **5**. So:

```python
def state_of(node):
    if node.value is not None:
        return node.value
    for attribute in ("ToggleState", "IsSelected"):
        state = node.element.get_attribute_value(attribute)
        if state is not None:
            return state
    return None
```

`IsSelected` is also the **only** way to work a tab. A Win32 `TabItem` supports
no `Invoke`, no `Toggle` and no `Expand` - `get_action_names()` returns `[]`
for one - and its selected-ness is not a value, so `.value` reads `None`
however the tab is set. Selecting it is `press()` (which reaches
`SelectionItem::Select`), and reading which tab is current is `IsSelected`.
An action that finds the current tab the macOS way, with
`str(tab.value) == "1"`, matches nothing on Windows and then waits for a tab
that never reports itself selected.

## Windows: launching an application gets the operator's session

Windows 11's Notepad is tabbed and single-instance, so starting it merely
activates the window that is already open, on whatever document the user was
editing - and `FindWindowW("Notepad", None)` then names *that* window. An
action that "opens Notepad and types" will type into their work.

Open a named file of your own, find the window by *its* title, confirm the
element you are about to write to by content only you would have written, and
require it to hold keyboard focus first. Assume this of every modern Windows
application, not only Notepad.

## Windows: the UIA Text pattern answers, at least here

Notepad's editor returns `get_text()` over the whole buffer,
`get_line_at_caret()` for the caret's line (not the document), and
`get_selection()` - the same three-rung ladder that works on macOS. **Windows
Terminal, VS Code and the editors people actually run are unmeasured**, and
Notepad is a weak proxy for them: check before an action depends on it.

## And one that was not a platform quirk at all

`send_key("Cmd-V")` once emitted the V with no Cmd, consistently, in WebKit and
in a native text view - which read exactly like an OS bug and nearly became a
"fix" in the keyboard hook. The cause was the harness: `keymap.configure()`
populates the modifier map, the runner had not called it, and with an empty map
no modifier events are emitted at all.

**Before concluding a platform misbehaves, check that your harness is the one
the application actually uses.** Print the event sequence; do not infer it.
