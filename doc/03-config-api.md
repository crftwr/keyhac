# 03 — User-facing config API

## Decision

Keyhac2's API is **keyhac-mac's API, extended** — snake_case, `define_keytable`,
`ThreadedAction`, `from keyhac import *`. Rationale:

- It is the newer, deliberately redesigned API (the author already modernized once).
- Existing keyhac-mac configs should run with few or no changes.
- keyhac-win's camelCase API (v1.x, 2011-era) is documented as *legacy*; we ship a
  migration table, not a compatibility shim. (Revisit only if user demand is loud.)

One `config.py` at `~/.keyhac/config.py` serves both OSes. Entry point:

```python
from keyhac import *

def configure(keymap):
    ...
```

`~/.keyhac/extensions/` is on `sys.path` (both predecessors have this).

## Portability model

- `keymap.platform` → `"windows"` / `"mac"` for explicit branching.
- Portable subset (works identically on both): key tables, key expressions with the
  portable modifier set, `app=`/`title=` focus conditions, key/tuple/callable/keytable
  assignment, one-shot & user modifiers, multi-stroke, `InputContext`, `ThreadedAction`,
  clipboard history/snippets/tools, chooser, record/playback, `LaunchApplication`,
  `ShellExecute`, `MoveWindow`, logging.
- Platform-only escape hatches (documented as such): `Focus.native` (Win32 window
  wrapper / AX `UIElement`), `class_name=` (Windows), `focus_path_pattern=` (macOS),
  `Cmd`/`Fn` (macOS), `Win`/`Apps` (Windows), mouse output (Windows first).

## Keymap definition

```python
# global (matches everything)
kt_global = keymap.define_keytable(focus_path_pattern="*")   # mac-compat spelling kept

# portable app matching — NEW in Keyhac2
kt_term  = keymap.define_keytable(app="WindowsTerminal|Terminal|iTerm2")
kt_edit  = keymap.define_keytable(app="Code", title="*myproject*")

# platform extras (each ignored-with-warning or error on the other OS — see Open questions)
kt_np    = keymap.define_keytable(app="notepad", class_name="Edit")        # Windows
kt_xcode = keymap.define_keytable(focus_path_pattern="*/AXTextArea()")     # macOS

# arbitrary logic
kt_x     = keymap.define_keytable(custom_condition_func=lambda focus: ...)

# detached table = multi-stroke second stroke
kt_ctrlx = keymap.define_keytable(name="Ctrl-X")
```

Matching semantics (superset of both predecessors):

- `app` — process/exe base name on Windows (`fnmatch`, `|` alternation), localized app
  name or bundle id on macOS.
- `title` — window text (Win32) / `AXWindow.AXTitle` (macOS), `fnmatch`.
- `class_name` — Win32 window class (Windows only).
- `focus_path_pattern` — AX focus path (macOS only; unchanged from keyhac-mac).
- `custom_condition_func(focus)` — receives the portable `Focus` object
  (`app_name`, `pid`, `window_title`, `path` (mac), `native`).
- Multiple matching tables merge **in definition order**, later wins per key
  (keyhac-win rule; keyhac-mac currently rebuilds similarly).

`replace_key`, `define_modifier`, one-shot (`O-`), down/up (`D-`/`U-`) — unchanged from
keyhac-mac, including semantics ported from keyhac-win where richer (one-shot canceled
by intervening keys and mouse clicks).

## Key expressions

`{O-|D-|U-}{Modifier-}...{Key}` — case-insensitive.

- **Modifiers (portable)**: `Alt`, `Ctrl`, `Shift`, `User0`, `User1`, each with `L`/`R`
  variants. Plus per-OS: `Cmd`, `Fn` (macOS); `Win` (Windows).
- **Short forms** (`A-`, `C-`, `S-`, `W-`, `U0-`, `U1-`, `LC-`, `RA-` …) are **accepted
  as aliases** — they exist in every keyhac-win config and cost little to keep.
  Canonical/documented form is the full name (keyhac-mac style).
- **Key names**: union of both predecessors' tables (letters, digits, symbols incl. JIS
  names like `Yen`/`Atmark`, `F1`–`F20`, nav cluster, numpad, `Kana`/`Eisu` (mac),
  `Apps`/`PrintScreen`/`ScrollLock`/`Pause` (win), modifier keys as primaries, raw
  `"(nnn)"`).
- Condition side is L/R-agnostic (`Ctrl-A` matches either Ctrl; `LCtrl-A` only left);
  output side resolves to left-hand physical keys — both inherited behaviors.
- **macOS Fn-arrow gotcha**: Apple keyboards translate `Fn-Left/Right/Up/Down` into
  `Home`/`End`/`PageUp`/`PageDown` in hardware (the `Fn` modifier itself still
  arrives), so a `Fn-…-Left` binding never fires — bind `Fn-…-Home` etc. instead.
  The shipped template's MoveWindow samples show the per-OS spelling.

### Portable "primary command" modifier — open proposal

Configs that mean "the OS's main shortcut modifier" (Cmd on mac, Ctrl on win) could use
a virtual alias resolved per platform:

```python
keymap.define_alias("Mod", mac="Cmd", windows="Ctrl")   # then "Mod-C", "Mod-V"
```

Cheap to implement in the expression parser; decide in M2 whether to ship it built-in
as `Mod-`.

## Assignments (unchanged from keyhac-mac)

```python
kt["Fn-J"] = "Left"                          # key → key
kt["Fn-N"] = "Cmd-1", "Cmd-2"                # key → sequence
kt["Fn-A"] = some_callable                   # key → function/action object
kt["Ctrl-X"] = kt_ctrlx                      # key → multi-stroke table
kt["O-RAlt"] = "Space"                       # one-shot
```

## Runtime APIs available to configs

Ported/unified — final reference generated from docstrings (lazydocs pipeline like
keyhac-mac's `make api-reference`):

| API | Source | Notes |
|---|---|---|
| `keymap.define_keytable / replace_key / define_modifier / define_alias` | mac (+new) | |
| `keymap.get_input_context(replay=False)` → `with … as ctx: ctx.send_key("Ctrl-C")` | mac | thread-safe via engine lock |
| `keymap.focus` → `Focus` | mac (`keymap.focus` was `UIElement`) | now portable object; `.native` for the old power |
| `keymap.get_active_window() / list_windows() / find_window(app=, title=, class_name=)` → `Window` | win `pyauto.Window` (+mac) | portable window ops; **UI-thread only** (see below) |
| `keymap.clipboard_history` | both | items/add/get_current/set_current, JSON persistence |
| `keymap.editor` (path or callable), `keymap.edit_config()`, `keymap.reload_config()` | win | tray/menu uses these too |
| `ThreadedAction` (starting/run/finished) | mac | the one background-work primitive |
| `keymap.call_later(seconds, func)` | win `delayedCall` | needs PuiKit/platform timer |
| `MoveWindow(direction=…, distance=…, …)` | mac | Windows impl via SetWindowPos; keeps mac's multi-monitor edge logic |
| `SnapWindow(position, ratio=0.5)` | new | left/right/top/bottom/full within the screen's work area |
| `LaunchApplication(name)` | mac | win: shell_execute |
| `ShellExecute(verb, file, param, dir, swmode)` | win | mac: degrade to `open` |
| `ActivateApplication / ActivateWindow(app=…, title=…)` | win `ActivateWindowCommand` | portable subset; returns native window or None |
| `InputText("…")` | win `InputTextCommand` | win: `SendInput` unicode; mac: CGEvent `keyboardSetUnicodeString` |
| Mouse output commands | win | Windows M4; macOS later (CGEvent mouse) |
| `ChooserAction`, `ShowClipboardHistory/Snippets/Tools` | mac | chooser UI now PuiKit |
| `Start/Stop/Toggle/PlaybackRecordedKeys` | mac (win macro semantics merged) | |
| `keymap.pop_balloon(name, text, timeout)` / `close_balloon` | win | PuiKit balloon window |
| `getLogger(name)`, `print()` → console | mac | |
| `Chooser`, `Clipboard` | both | lower-level, platform-flagged |
| `focus.element` → `UIElement` | mac AX / win UI Automation | same shape, per-OS attribute names — see below |

## Windows and elements

Two different things, split deliberately.

**Windows are portable.** `Window` (`keymap.get_active_window()`, `list_windows()`,
`find_window(...)`) exposes `title`, `app_name`, `pid`, `class_name` (Windows only),
`get_frame()` / `set_frame(x, y, w=None, h=None)`, `activate()`, `is_minimized()`,
`restore()`, `minimize()`, `native`. macOS backs it with AX window elements, Windows with
HWNDs. `find_window` matches exactly like `define_keytable(app=/title=/class_name=)`:
`fnmatch` wildcards, `|` alternation, case-insensitive, `.exe` optional.

Screen geometry lives on `keymap` too: `screen_frames()` (whole screens, primary
first), `screen_work_frames()` (the same minus menu bar / Dock / taskbar — what
`SnapWindow` tiles against), and `window_frames()` (all normal on-screen windows).

**Elements are not.** `focus.element` is the focused *semantic* element — an AX
`UIElement` on macOS, a UI Automation one on Windows. Both offer the same shape
(`get_attribute_names()`, `get_attribute_value(name)`, `get_action_names()`,
`perform_action(name)`, `parent()`), but each uses **its own OS's vocabulary**, because a
portable façade would have to invent a third one and misrepresent both:

| | macOS (AX) | Windows (UI Automation) |
|---|---|---|
| role | `AXRole` | `ControlType` (`"Edit"`, `"Window"`, `"Button"`, …) |
| label | `AXTitle` | `Name` |
| text value | `AXValue` | `Value` |
| selection | `AXSelectedText` | `SelectedText` |
| press | `perform_action("AXPress")` | `perform_action("Invoke")` |

So element-level config code branches on `keymap.platform`. An unsupported attribute
reads `None` and an unknown action logs and returns `False` — a macOS name reaching a
Windows element never raises on the key path. `get_attribute_names()` lists only what
*this* element actually supports.

`Focus.native` keeps its per-OS meaning: the same `UIElement` on macOS, an HWND wrapper
on Windows (the `pyauto.Window` analogue a migrating keyhac-win config expects).

### Thread contract

`Window` accessors and `focus.element` are **UI-thread only**. On macOS they are AX calls,
and AX into our own process off the main thread crashes with `SIGTRAP`; on Windows,
reading a window title is a blocking `SendMessage(WM_GETTEXT)` that deadlocks against a
UI thread which is not pumping. A `ThreadedAction` therefore reads windows in
`starting()`, computes in `run()`, and writes back in `finished()`. The only geometry
queries safe to call from `run()` are `screen_frames()` and `window_frames()`, which
use CoreGraphics on macOS and pure `GetWindowRect` on Windows. `MoveWindow` is built
exactly that way. `screen_work_frames()` is the exception among the geometry calls:
its macOS source is AppKit (`NSScreen.visibleFrame` — CoreGraphics knows nothing about
the Dock), so it is UI-thread only; `SnapWindow` is accordingly a plain main-thread
action (no edge scan to push off-thread, just arithmetic).

### Windows focus paths

The Windows focus path is the full UI Automation control hierarchy, the same granularity
as the macOS AX path:

```
/Application(Code)/Window(…)/Pane()/…/Document()/Group()/Edit(Message input)
```

UI Automation rather than the HWND tree, because a UWP/WinUI, Electron or Chrome window is
one HWND containing the entire UI — walking HWND parents adds no levels precisely in the
apps people work in. Use `*` to skip depth, as on macOS: `focus_path_pattern="*/Edit()"`.

## Compatibility matrix

### vs keyhac-mac configs — near-drop-in

Expected breaks (list kept current during development):

- `keymap.focus` returns `Focus`, not `UIElement` → use `keymap.focus.native`.
- `UIElement.set_attribute_value(name, type, value)` unchanged, but documented properly
  (upstream docs bug: generated signature omits `type`).
- Chooser/console visuals differ (PuiKit windows instead of SwiftUI).

### vs keyhac-win configs — migration table (excerpt; full table maintained here)

| keyhac-win | Keyhac2 |
|---|---|
| `keymap.defineWindowKeymap(exe_name="notepad.exe", class_name="Edit")` | `keymap.define_keytable(app="notepad", class_name="Edit")` |
| `keymap.defineWindowKeymap()` | `keymap.define_keytable(focus_path_pattern="*")` |
| `keymap.defineMultiStrokeKeymap(help)` | `keymap.define_keytable(name=…)` (+ balloon help restored) |
| `keymap.replaceKey / defineModifier` | `replace_key / define_modifier` |
| `keymap.InputKeyCommand("C-X")` | `"Ctrl-X"` (bare assignment) or `InputKey("Ctrl-X")` |
| `keymap.InputTextCommand(s)` | `InputText(s)` |
| `keymap.ShellExecuteCommand(...)` | `ShellExecute(...)` |
| `keymap.ActivateWindowCommand(exe_name=…)` | `ActivateWindow(app=…)` |
| `keymap.MouseMoveCommand(dx,dy)` etc. | `MouseMove(dx,dy)` etc. (M4) |
| `keymap.command_ClipboardList` | `ShowClipboardHistory()` |
| `keymap.command_Record*` | `*RecordingKeys()` actions |
| `JobQueue`/`JobItem` | `ThreadedAction` |
| `CronItem`/`CronTable` | `keymap.call_later` loop or ThreadedAction + timer (see Open) |
| `keymap.delayedCall(f, msec)` | `keymap.call_later(msec/1000, f)` |
| `keymap.popBalloon / closeBalloon` | `keymap.pop_balloon / close_balloon` |
| `keymap.popListWindow(listers)` (blocking) | `ChooserAction` subclass (callback-based; **no nested blocking loop** in Keyhac2) |
| `keymap.setFont/setTheme` | PuiKit theme/font settings via `keymap.ui` settings (M5) |
| `cblister_FixedPhrase` | `ShowClipboardSnippets` |
| `keymap.editor = "path"` | same name, kept |

Deliberate drops (documented): `send_input_on_tru` ini hack, `keymap.wnd`, profile mode
flag semantics, blocking `popListWindow` (its nested message loop is the single worst
source of reentrancy in keyhac-win).

## Open questions (to resolve in M1/M2)

1. Platform-foreign kwargs (`class_name=` on mac): warn-and-never-match vs raise at
   config load. Leaning **warn**, so one config file loads everywhere.
2. Ship `Mod-` built-in or as opt-in `define_alias`?
3. `focus_path_pattern="*"` vs a dedicated `keymap.global_keytable` property.
4. Cron-style periodic tasks: keep out (users can ThreadedAction+sleep loop) or provide
   `keymap.every(seconds, func)`.
