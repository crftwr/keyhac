# Migrating a keyhac-mac config.py to Keyhac 2

Most keyhac-mac configs run with small edits. Known differences:

| keyhac-mac | Keyhac 2 |
|---|---|
| `keymap.focus` -> `UIElement` | `keymap.focus` -> portable `Focus`; the UIElement is `keymap.focus.native` |
| `custom_condition_func(elm)` receives `UIElement` | receives `Focus`; unknown attributes forward to `.native`, so existing AX-based conditions run unchanged |
| `Hook`, `Console`, `Chooser`, `Clipboard` core objects | not exposed; use actions / `keymap.pop_balloon` / `getLogger` |
| `MoveWindow(...)` | full port - same signature incl. window_edge/screen_edge, deprecated x/y |
| `keymap.replay_buffer` (undocumented) | same name, documented |
| `ThreadedAction`, `ShowClipboard*`, `LaunchApplication`, `define_keytable`, `replace_key`, `define_modifier`, key expressions incl. `Fn-`/`Cmd-` | unchanged |
| — | new: `app=`/`title=` focus conditions, keyhac-win short forms (`C-`, `A-`...), `InputText`, `ActivateWindow`, `keymap.call_later` (planned), User2/User3 |

The same config.py also runs on Windows: branch with `keymap.platform`.
