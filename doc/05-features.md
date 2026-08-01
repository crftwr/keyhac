# 05 — Feature parity plan

Target: the **union** of keyhac-win 1.83 and keyhac-mac 1.68 features, phased by
milestone (M-numbers from [07-roadmap.md](07-roadmap.md)).

## Parity matrix

| Feature | win 1.83 | mac 1.68 | Keyhac2 | Milestone |
|---|---|---|---|---|
| Key→key / key→sequence / key→callable | ✔ | ✔ | ✔ shared engine | M1 |
| App/window-specific keymaps | exe/class/title | AX focus path | portable `app`/`title` + per-OS extras | M2 |
| Multi-stroke keymaps | ✔ (+balloon help) | ✔ (help missing, FIXME in source) | ✔ with balloon help on both | M2/M4 |
| replace_key / user modifiers (User0-1(-3)) / one-shot | ✔ (User0–3) | ✔ (User0–1) | ✔ User0–3, engine parity tests | M1 |
| L/R modifier distinction | ✔ | ✔ | ✔ | M1 |
| Keyboard macro record/playback | ✔ | ✔ (replay source) | ✔ mac's replay-source design on both | M4 |
| Clipboard history (persistent) | ini, text only | JSON, text | JSON; keep per-OS rich payloads (RTF/HTML) as fidelity extension | M3 |
| Snippets / clipboard tools | ✔ (`cblister_FixedPhrase`) | ✔ (actions) | ✔ mac actions API | M3 |
| Chooser / list window | ListWindow (blocking API, migemo, one-key search) | Chooser (async, substring AND) | PuiKit chooser, async API; incremental filter; **migemo deferred** (optional pure-Python port later) | M3 |
| Console window | ✔ (ckit) | ✔ (SwiftTerm) | PuiKit LogView + toggle/level/inspector | M2 |
| Balloon/tooltip | ✔ | ✖ | ✔ PuiKit frameless window (E1/E2) | M4 |
| Tray icon / menu-bar extra | ✔ tray | ✔ MenuBarExtra | ✔ PuiKit E4 | M4 |
| Mouse output commands | ✔ | ✖ | ✔ both (SendInput mouse / CGEvent mouse: `MouseMove/MouseButton*/MouseWheel*`; mac adds drag typing while a button is held, click-state escalation for synthetic double-clicks, 3 lines per notch) | M4 |
| One-shot cancel on mouse | ✔ (mouse LL hook) | ✖ | ✔ both (WH_MOUSE_LL / mouse types in the tap mask; observation-only, own output ignored via dwExtraInfo / event source) | M4 |
| InputText (literal string typing) | ✔ | ✖ | ✔ both (SendInput unicode / CGEvent unicode string) | M3 |
| ActivateWindow / window enumeration | ✔ | ✖ | ✔ portable subset (`app`/`title`); native power via `Focus.native` | M3 |
| MoveWindow (multi-monitor edges) | ✔ (monitor edge cmd) | ✔ (rich screen-edge logic) | ✔ mac logic + win SetWindowPos backend | M3 |
| ShellExecute / LaunchApplication | ✔ / – | – / ✔ | ✔ both | M3 |
| UIElement AX automation | – | ✔ | ✔ macOS (PyObjC port) | M3 |
| ThreadedAction | JobQueue/JobItem | ✔ | ✔ ThreadedAction (+`call_later`) | M2 |
| Cron / periodic | ✔ CronTable | ✖ | TBD (open question, 03-config-api) | — |
| Config reload / edit from tray+console | ✔ | ✔ | ✔ | M2 |
| stdout/stderr → console, getLogger levels | ✔ / – | ✔ / ✔ | ✔ mac design | M2 |
| Themes (black/white) / fonts | ✔ ini+PNG | ✖ (system) | PuiKit `Theme` (derive_theme); light+dark presets; `setFont` equivalent | M5 |
| i18n en/ja | ✔ (ckit.strings) | ✖ (en only) | ✔ small string table module, en/ja | M5 |
| Portable mode (config next to exe) | ✔ | ✖ | ✔ Windows keep; macOS n/a | M5 |
| Settings persistence | keyhac.ini | scattered | `~/.keyhac/settings.json` | M2 |
| Single-instance guard | via hook ValueError | app bundle | explicit lockfile/mutex per OS | M2 |
| Help/docs generation | doxygen+rst | lazydocs markdown | lazydocs pipeline (mac's `make api-reference`) | M5 |
| Internet update check | ✖ (none, verified) | ✖ | ✖ (keep none) | — |

## Per-feature design notes

### Clipboard history
- Model from keyhac-mac (`ClipboardHistory`, `max_items=1000`, label truncation,
  size quotas), persistence `~/.keyhac/clipboard.json`.
- Fix upstream FIXME: don't rewrite the whole JSON on every copy — append-journal or
  debounced save; flush on quit and on session-end signal (`WM_ENDSESSION` /
  `NSApplicationWillTerminate`).
- Monitoring: event-driven on Windows (`AddClipboardFormatListener`), changeCount poll
  on macOS (~1 s).
- Paste flow (from keyhac-win, the battle-tested one): set clipboard → refocus target →
  serialized `Ctrl-V`/`Cmd-V` injection (`hook_call` on win; keyhac-mac's AXFrontmost +
  paste on mac). Shift-select = copy-only, Ctrl(-mac: Cmd)-select = quote-paste with
  `keymap.quote_mark`.

### Chooser
- Async, callback-based (`ChooserAction.list_items/on_chosen`) — keyhac-win's blocking
  `popListWindow` is not carried over.
- Filtering: multi-word AND substring (mac behavior) first; then optional match-mode
  escalation like keyhac-win's isearch (strict→partial→inaccurate). Migemo: deferred —
  needs a C/Migemo binding or pure-Python reimplementation + dict distribution; keep the
  hook point (`match_func` injectable).
- Placement: centered on focused window (mac behavior) via E5 screen frames; remember
  per-chooser size.

### Console
- LogView ring buffer 1000 lines, per-level colors (mac ANSI scheme → PuiKit styles),
  log-level dropdown, hook on/off toggle (with AX permission recheck on mac), last-key +
  focus-path inspector fields with copy buttons (mac console features), geometry saved in
  settings.json (win behavior).
- `print()` and `getLogger` both land here; stderr force-shows the window (win behavior).

### Balloon
- Frameless topmost no-activate PuiKit window near the caret (`ScreenInfo.caret_rect`,
  fallback: focused-window corner). Used for multi-stroke help (restores the mac FIXME)
  and macro record state, with timeout via `call_later`.

### Tray / menu-bar extra
- Same `Menu` model both OSes: Open console, Edit config, Reload config, Hook on/off,
  Record start/stop, Clear console, Help, Quit (union of both predecessors' menus).

### Macro record/playback
- keyhac-mac's design (dedicated replay event source, replayed keys re-enter the keymap)
  on both OSes; keyhac-win's normalization rules (drop unmatched downs, 1000-event cap,
  release-modifiers-before-play) ported into `core/replay.py`.

### Window actions
- `MoveWindow`: keep keyhac-mac's direction/edge/multi-monitor logic in core, backed by
  `WindowControl.set_rect` (SetWindowPos / AXPosition+AXSize).
- `ActivateWindow(app=, title=)`: Windows implementation from keyhac-win (enum, skip
  invisible/minimized, `getLastActivePopup().setForeground`); macOS via NSRunningApplication
  activate + AX window raise.

### Security/privacy notes (document for users)
- macOS: Accessibility permission required; input monitoring implications.
- Clipboard history stores clipboard contents on disk — document location, size caps,
  and how to disable persistence (`keymap.clipboard_history.persist = False`).
- Windows: cannot see input of elevated windows unless Keyhac runs elevated.
