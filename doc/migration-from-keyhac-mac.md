# Migrating a keyhac-mac config.py to Keyhac 2

## Installing over 1.x

Keyhac 2 ships under its own application identity (`crftwr.Keyhac2`), so macOS treats it
as a new app: **grant the Accessibility permission again** on first launch, in System
Settings → Privacy & Security → Accessibility. The *Keyhac* entry already listed there
belongs to 1.x and does nothing for Keyhac 2 — remove it once 1.x is gone.

Uninstall 1.x rather than keeping both. They share `~/.keyhac/config.py` and
`~/.keyhac/clipboard.json`, and each installs its own keyboard hook, so running them
together puts two hooks on every keystroke. Sharing the config file is deliberate: your
existing `config.py` is picked up as it stands, subject to the differences below.

## Config differences

Most keyhac-mac configs run with small edits. Known differences:

| keyhac-mac | Keyhac 2 |
|---|---|
| `keymap.focus` -> `UIElement` | `keymap.focus` -> portable `Focus`; the UIElement is `keymap.focus.native` |
| `custom_condition_func(elm)` receives `UIElement` | receives `Focus`; unknown attributes forward to `.native`, so existing AX-based conditions run unchanged |
| `Hook`, `Console`, `Chooser`, `Clipboard` core objects | not exposed; use actions / `keymap.pop_balloon` / `getLogger` |
| `MoveWindow(...)` | full port - same signature incl. window_edge/screen_edge, deprecated x/y |
| `keymap.replay_buffer` (undocumented) | same name, documented |
| `ThreadedAction`, `ShowClipboard*`, `LaunchApplication`, `define_keytable`, `replace_key`, `define_modifier`, key expressions incl. `Fn-`/`Cmd-` | unchanged |
| — | new: `app=`/`title=` focus conditions, keyhac-win short forms (`C-`, `A-`...), `InputText`, `ActivateWindow`, mouse output, balloons, `SnapWindow`, portable `Window` objects, User2/User3 |

The same config.py also runs on Windows: branch with `keymap.platform`. Three things in a
macOS config do not carry over, and Keyhac 2 reports each at load time:

- **`Cmd`/`Fn` keys** (`"O-RCmd"`, `keymap.define_modifier("RCmd", ...)`) — no such key on
  Windows: `Invalid key expression: O-RCmd (… that key exists only on macOS …)`.
- **`Cmd-`/`Fn-` modifiers** — these *parse* on Windows (modifier names are OS
  independent) but no key sets the bits, so the assignment silently never fires. After
  loading, Keyhac warns once: `No key produces the Cmd, Fn modifiers on windows …`.
- **AX calls on the focus** (`focus.get_attribute_value("AXWindow")`, `get_attribute_names`,
  `perform_action`) — `Focus.native` is a Win32 window wrapper on Windows, so these raise
  `AttributeError`. Inside a `custom_condition_func` the traceback is logged once per
  configuration load and the condition evaluates to False.

The Windows counterparts are `Win`/`Apps` keys, `app=`/`title=`/`class_name=` focus
conditions, and — for element work — `focus.element` with UI Automation's vocabulary
(`ControlType`/`Name`/`Value`/`SelectedText`, `perform_action("Invoke")`) instead of AX's.
See [configuration.md](configuration.md#the-focus-object) for the mapping table.
Window operations (`keymap.get_active_window()`, `find_window()`, `MoveWindow`,
`ActivateWindow`) are portable and need no branch.
