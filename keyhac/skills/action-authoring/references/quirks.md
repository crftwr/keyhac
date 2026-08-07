# Measured quirks

Every entry here was found by running against a real application, and each one
produces code that looks correct and silently is not. Read this before
debugging something that "should work". Measured on macOS 15 / Safari 18 /
Chrome, 2026-08-06 and 07.

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
registered on the application element and on the `AXWebArea` alike. Notifications
also do not bubble, so "wait for an element to appear" cannot be registered
anywhere useful.

This is why `wait_for` polls (20 ms backing off to 250 ms) and an observer is
only an accelerator. Against a browser, polling is what finds the change, and
it is fast enough - a modal was seen 10-25 ms after the click.

## Terminals do answer whole-value reads

Terminal.app's `AXTextArea` returns the entire scrollback through `AXValue`,
and `get_line_at_caret()` returns the prompt line. So the cheap path - read
everything, take the last match - works, and neither a selection nor the
pointer is needed. iTerm2 untested.

## Native macOS: identifiers are serial numbers

`AXDOMIdentifier` in web content is a real name. AppKit's `AXIdentifier` is
usually `_NS:746` - a nib ordinal that changes when the window is edited and
means nothing to a reader. **"Prefer identifier" holds for DOM ids and
AutomationIds, and is actively wrong for `_NS:*`.** Address native controls by
name, and fall back to structure.

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

## And one that was not a platform quirk at all

`send_key("Cmd-V")` once emitted the V with no Cmd, consistently, in WebKit and
in a native text view - which read exactly like an OS bug and nearly became a
"fix" in the keyboard hook. The cause was the harness: `keymap.configure()`
populates the modifier map, the runner had not called it, and with an empty map
no modifier events are emitted at all.

**Before concluding a platform misbehaves, check that your harness is the one
the application actually uses.** Print the event sequence; do not infer it.
