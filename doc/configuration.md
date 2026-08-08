# Configuration

Keyhac is configured with one Python file: `~/.keyhac/config.py` (or, in Windows
[portable mode](installation.md#portable-mode-windows), the `config.py` sitting next
to `Keyhac.exe`). On first run it is
created from a fully commented template — the template
([keyhac/_config.py](../keyhac/_config.py) in the source tree) is a working example of
everything on this page, and a good file to keep open while reading.

This page introduces the APIs in the order you meet them. For the exact arguments
of any one of them, see the [API reference](api_reference.md).

## The config file

```python
from keyhac import *

def configure(keymap):
    ...
```

Keyhac calls `configure(keymap)` at startup and on every reload ("Reload Config" in
the tray / menu-bar menu). Errors are contained:

- A config that fails to load keeps the **previous** keymap active; the error appears
  in the console window.
- An exception inside one of your bound functions is logged with a traceback, and the
  key is passed through unmodified — typing keeps working even when the config is
  broken.

`~/.keyhac/extensions/` is on `sys.path`, so you can split a large config into modules
and `import` them.

"Edit Config" in the tray menu opens the file in an editor. Pick yours with:

```python
keymap.editor = "CotEditor"          # app name or path, or a callable(path)
```

Unset, Keyhac tries VS Code, then Xcode, then TextEdit on macOS; Notepad on Windows.

## One config, both OSes

The same file runs on Windows and macOS. Where the OSes genuinely differ, branch on
`keymap.platform` (`"windows"` or `"mac"`). Two constants absorb most of it — this is
the pattern the template uses throughout:

```python
mac = keymap.platform == "mac"
LEADER = "Fn" if mac else "User0"    # the modifier your bindings hang off
MOD = "Cmd" if mac else "Ctrl"       # the OS's primary shortcut modifier

kt[f"{LEADER}-C"] = f"{MOD}-C"
```

A plain Python constant is the answer here, not a built-in name: there is no `Mod-`
modifier in the key expression language. Modifier names mean the same thing on both
OSes, and one that quietly changed meaning per OS would not survive the round trip —
the console reports what actually fired, `Cmd-C` or `Ctrl-C`. The constant also scales
to whatever else your config wants to vary by OS, which is why `LEADER` above works the
same way.

Config diagnostics are cross-platform aware: a key name that exists only on the other
OS says so in the error, and after loading, Keyhac warns once about bindings whose
modifiers no key on this OS can produce (for example a `Cmd-` binding running on
Windows — it parses, but would silently never fire).

## Key tables

A key table holds bindings and a condition for when they apply:

```python
# global (matches everything)
kt = keymap.define_keytable(focus_path_pattern="*")

# portable: by application, by window title (fnmatch wildcards, "|" alternation,
# case-insensitive; ".exe" optional on Windows)
kt_term = keymap.define_keytable(app="WindowsTerminal|Terminal|iTerm2")
kt_edit = keymap.define_keytable(app="Code", title="*myproject*")

# platform extras
kt_np    = keymap.define_keytable(app="notepad", class_name="Edit")        # Windows
kt_area  = keymap.define_keytable(focus_path_pattern="*/AXTextArea(*)")    # macOS

# arbitrary logic
kt_x     = keymap.define_keytable(custom_condition_func=lambda focus: ...)

# named, detached table = a multi-stroke second stroke (see below)
kt_ctrlx = keymap.define_keytable(name="Ctrl-X")
```

Matching:

- `app` — process/exe base name on Windows, localized application name on macOS.
- `title` — window title.
- `class_name` — Win32 window class (Windows only).
- `focus_path_pattern` — the control hierarchy down to the focused element: the AX
  tree on macOS, the UI Automation tree on Windows. Watch the console's
  "Focus path" field to see the live value while you focus things:

  ```
  macOS:    /AXApplication(Xcode)/AXWindow(...)/.../AXTextArea()
  Windows:  /Application(Code)/Window(...)/.../Edit(Message input)
  ```

  Use `*` to skip levels: `focus_path_pattern="*/Edit(*)"`. A component is
  `Role(Name)`, and many controls carry a name, so `*/Edit()` matches only
  unnamed ones — usually you want `*/Edit(*)`.
- `custom_condition_func(focus)` — your own test; receives the [Focus
  object](#the-focus-object).

**Every matching table is active at once**, merged in definition order — later tables
win per key. Define your global table first and app-specific tables after it, and the
specific ones override exactly the keys they bind.

## Key expressions

`{O-|D-|U-}{Modifier-}...{Key}` — case-insensitive.

- **Modifiers**: `Alt`, `Ctrl`, `Shift`, `User0`–`User3`, plus `Cmd` and `Fn` on
  macOS, `Win` on Windows. Each has `L`/`R` variants (`LCtrl-`, `RAlt-`).
- **Short forms** are accepted as aliases: `A-`, `C-`, `S-`, `W-`, `U0-`–`U3-`,
  and their `L`/`R` forms (`LC-`, `RA-`, …). The full names are the documented
  spelling.
- **Left/right rules**: a condition is side-agnostic unless you say otherwise —
  `Ctrl-A` matches either Ctrl, `LCtrl-A` only the left one. Output goes out with
  left-side modifier keys.
- **Prefixes**: `O-` one-shot (see below), `D-` key-down only, `U-` key-up only.
- **Key names**: letters, digits, punctuation (`Semicolon`, `Slash`,
  `OpenBracket`, …, plus JIS names like `Yen` and `Atmark`), `F1`–`F20` (to `F24`
  on Windows), the navigation cluster (`Left`, `Home`, `PageUp`, …), numpad
  (`Num0`, `NumAdd`, …), `Kana`/`Eisu` (macOS), `Apps`/`PrintScreen`/`ScrollLock`/
  `Pause` (Windows), and the modifier keys themselves as primary keys (`LWin`,
  `RCmd`, …). Any unmapped key is expressible as its raw code: `"(124)"`.
- **macOS Fn-arrow gotcha**: Apple keyboards translate `Fn-Left/Right/Up/Down` into
  `Home`/`End`/`PageUp`/`PageDown` in hardware (the `Fn` modifier itself still
  arrives). A `Fn-…-Left` binding can therefore never fire — bind `Fn-…-Home`
  instead. The template's MoveWindow samples show the per-OS spelling.

## Assignments

```python
kt["Fn-J"] = "Left"                          # key -> key
kt["Fn-N"] = "Cmd-1", "Cmd-2"                # key -> sequence
kt["Fn-A"] = some_callable                   # key -> function / action object
kt["Ctrl-X"] = kt_ctrlx                      # key -> multi-stroke table
kt["O-RAlt"] = "Space"                       # one-shot
```

Anything callable can be bound: a plain function, a lambda, or one of the action
objects below (`MoveWindow(...)`, `ShowClipboardHistory()`, … — they are all
callables).

**Multi-stroke tables**: assigning a named table arms it as a prefix — press
`Ctrl-X`, then a key bound in `kt_ctrlx`. While armed, a balloon shows the table's
name. A key that is not bound in the armed table cancels it (and is swallowed, so a
typo does not leak a stray keystroke into your app).

## Remapping and user modifiers

```python
keymap.replace_key("CapsLock", "LCtrl")      # swap a key outright
keymap.define_modifier("RAlt", "RUser0")     # turn a key into your own modifier
```

- `replace_key` runs before everything else; the rest of the config only ever sees
  the replacement.
- `define_modifier` makes a key act as one of `User0`–`User3` — modifiers that no
  application sees. While defined, the key loses its original meaning entirely
  (it is never emitted), so `User0-J` bindings cannot clash with anything an app
  understands.

**One-shot modifiers** (`O-` prefix) give a modifier key a second life: held with
another key it modifies as usual; tapped *alone*, it fires the one-shot binding.

```python
kt["O-LCmd"] = "Eisu"        # tap left Cmd alone -> IME off; held -> still Cmd
```

A one-shot is canceled by any intervening key or mouse click, so half-finished
shortcuts do not trigger it.

## Sending input from functions

```python
def wrap():
    with keymap.get_input_context() as ctx:
        ctx.send_key("Ctrl-C")               # key expression, incl. modifiers
        ctx.send_text("hello")               # literal text, any characters
```

`get_input_context()` batches virtual input and reconciles modifiers for you: held
physical modifiers are released around the batch and restored after, so
`ctx.send_key("Ctrl-C")` works even while your binding's own modifiers are still
physically down. It is safe from a `ThreadedAction` worker thread too.

For simple cases there are ready-made actions:

```python
kt["Fn-Semicolon"] = InputText("me@example.com")   # type a literal string
```

**Mouse output**: `MouseMove(dx, dy)`, `MouseButtonDown/Up/Click(button)` (`"left"`
by default, or `"right"` / `"middle"`), `MouseWheel(notches)`,
`MouseHorizontalWheel(notches)` (positive is away from you / to the right, 1.0 =
one notch) — plus the matching `ctx.send_mouse_*` methods. Buttons and wheels release held
modifiers first (so a `User0-…` binding does not click with a phantom modifier);
moves keep them. Relative moves are injected acceleration-proof on both OSes, and
rapid synthetic clicks register as double-clicks.

## Clipboard

```python
keymap.clipboard_history.max_items = 500
kt["Fn-V"] = ShowClipboardHistory()
```

`ShowClipboardHistory()` opens the chooser popup over the focused window: type to
filter (multi-word AND match), `Up`/`Down` to select, **Enter pastes into the app
you came from**, **Shift-Enter only sets the clipboard**, `Escape` cancels. Pressing
the hotkey again closes it.

```python
kt["Fn-Shift-V"] = ShowClipboardSnippets([
    ("📧", "me@example.com"),                          # (icon, text)
    ("📮", "Mailing address", "400 Broad St, ..."),    # (icon, label, text)
    ("🕒", "Date", DateTimeSnippet("%Y-%m-%d")),       # (icon, label, callable)
])

kt["Fn-Ctrl-V"] = ShowClipboardTools([
    ("🔄", "Quote", ShowClipboardTools.quote),
    ("🔄", "Upper case", str.upper),
    ("🔄", "Pretty JSON", my_pretty_json),             # str -> str
])
```

Tools transform the current clipboard text; the built-ins are `quote`, `unindent`,
`to_half_width` and `to_full_width`, and any `str -> str` callable works.

History behavior is configurable via `keymap.clipboard_history`: `max_items`
(default 1000), `max_data_size` (largest text captured, default 10 MB),
`max_persist_data_size` (largest entry written to disk, default 64 KB), and
`persist = False` to keep history in memory only. The API is scriptable too:
`items()`, `get_current()`, `set_current(text)`.

Custom choosers derive from `ChooserAction`: implement
`list_items() -> [(icon, label, ...)]` and `on_chosen(item, modifier_flags)`; the
open/filter/refocus flow is inherited.

## Windows, screens and applications

```python
kt["Fn-1"] = ActivateWindow(app="code|Visual Studio Code")   # bring forward
kt["Fn-T"] = LaunchApplication("Terminal.app")               # launch

kt["Fn-Ctrl-Left"] = MoveWindow(direction="left", distance=20)
kt["Fn-Alt-Left"]  = MoveWindow(direction="left", distance=9999,
                                window_edge=True, screen_edge=True)
kt["Fn-Ctrl-J"] = SnapWindow("left")          # tile: left/right/top/bottom/full
kt["Fn-F"]      = SnapWindow("full")          # ratio=2/3 etc. picks the split
```

`MoveWindow` nudges by `distance` pixels (default 10), or with
`window_edge=`/`screen_edge=` travels until it hits other windows' edges / the
screen edge (hopping to the next monitor when already there). Only `screen_edge`
is on by default. `SnapWindow` tiles within the screen's *work area* — menu bar,
Dock and taskbar stay uncovered — taking half the screen unless `ratio=` says
otherwise.

For your own logic, `Window` objects are fully portable:

```python
window = keymap.get_active_window()           # or find_window(app=, title=,
x, y, w, h = window.get_frame()               #    class_name=), list_windows()
window.set_frame(x + 100, y)
window.activate(); window.minimize(); window.restore(); window.is_minimized()
window.title; window.app_name; window.pid; window.class_name  # class_name: Windows
```

`find_window` matches exactly like `define_keytable`: wildcards, `|` alternation,
case-insensitive, `.exe` optional. Screen geometry lives on `keymap`:
`screen_frames()` (whole screens, primary first), `screen_work_frames()` (minus
menu bar / Dock / taskbar) and `window_frames()` (all normal on-screen windows) —
each frame is an `(x, y, w, h)` tuple in a shared coordinate space.

**Thread contract**: `Window` objects, `focus.element` and `screen_work_frames()`
are UI-thread only — never touch them from a `ThreadedAction.run()`. The
thread-safe geometry pair is `screen_frames()` / `window_frames()`. A
`ThreadedAction` reads windows in `starting()`, computes in `run()`, and writes
back in `finished()`.

## The Focus object

`keymap.focus` — and the argument of every `custom_condition_func` — is a snapshot
of the current keyboard focus: `app_name`, `pid`, `window_title`, `class_name`
(Windows only), `path` (the focus path string), `element` and `native`.

`focus.element` is the focused *semantic* element — an AX element on macOS, a UI
Automation element on Windows. Both have the same shape:
`get_attribute_names()`, `get_attribute_value(name)`, `get_action_names()`,
`perform_action(name)`, `parent()` — but each uses **its own OS's vocabulary**:

| | macOS (AX) | Windows (UI Automation) |
|---|---|---|
| role | `AXRole` | `ControlType` (`"Edit"`, `"Window"`, …) |
| label | `AXTitle` | `Name` |
| text value | `AXValue` | `Value` |
| selection | `AXSelectedText` | `SelectedText` |
| press | `perform_action("AXPress")` | `perform_action("Invoke")` |

Element-level code therefore branches on `keymap.platform`. The failure modes are
gentle: an unsupported attribute reads `None`, an unknown action logs and returns
`False`. `focus.native` is the platform power object — the same AX element on
macOS, an HWND wrapper on Windows.

### Driving another application: the action API

Reading and writing another application's UI — searching element trees, waiting
for a screen to change, filling fields — is a separate surface reached through
`keymap.ui` (or `self.ui` inside a `ThreadedAction`):

```python
class Extract(ThreadedAction):
    def run(self):
        window = self.ui.window(app="Safari")
        window.wait_for(role="AXTable", message="the results to load")
        for row in window.find_all(role="AXRow"):
            print([cell.all_text for cell in row.children])
```

It is deliberately its own namespace and method-style: a config binds keys, an
action drives somebody else's UI, and only `UINode`, `WaitTimeout` and
`FillFailed` are importable. **See [Action API](action_api.md)** for the full
surface, and `keyhac/skills/action-authoring/` for how to write one.

## Keyboard macros

```python
kt["Fn-OpenBracket"]  = ToggleRecordingKeys()     # record on/off
kt["Fn-CloseBracket"] = PlaybackRecordedKeys()    # replay
```

`StartRecordingKeys()` / `StopRecordingKeys()` exist if you prefer separate keys.
Replayed keys run back through your keymap, so recorded bindings expand on
playback. The buffer is `keymap.replay_buffer`.

## Background work: ThreadedAction

Anything slow — network, subprocess, sleeping, heavy computation — must not run
inline: your functions execute inside the keyboard hook's deadline. Subclass
`ThreadedAction` instead:

```python
class Fetch(ThreadedAction):
    def starting(self):          # main thread, before run
        logger.info("fetching...")
    def run(self):               # worker thread - the slow part
        return do_network_call()
    def finished(self, result):  # main thread, after run
        logger.info(f"got {result}")

kt["Fn-G"] = Fetch()
```

`starting()` and `finished()` run on the main thread (UI and window access allowed);
`run()` runs on a worker (input contexts allowed, windows/elements not — see the
thread contract above).

The pool is a **single worker shared by every threaded action**, so a `run()` that
sleeps or loops delays every other one until it returns. Keep long waits short, and
prefer `keymap.call_on_main_thread(func)` — thread-safe, and the supported way to
reach the main thread from anywhere — over holding the worker to wait for something.

## Balloons

```python
keymap.pop_balloon("hello", "Keyhac is running", 2.0)   # name, text, timeout
keymap.close_balloon("hello")
```

A balloon is a small frameless tooltip near the focused window. Multi-stroke tables
pop one automatically. In `--no-ui` mode these attributes are absent — guard with
`getattr(keymap, "pop_balloon", None)` if your config must run headless.

## Logging and the console

`print()` and `getLogger(name)` both land in the console window:

```python
logger = getLogger("Config")
logger.info("loaded")
```

The console's dropdown filters by level; `-d`/`--debug` on the command line enables
debug logging from startup. The console also shows the last key event and the live
focus path — the two things you need when writing new bindings.

## Runtime API summary

| API | Notes |
|---|---|
| `keymap.platform` | `"windows"` / `"mac"` |
| `keymap.define_keytable(...)` | key tables + focus conditions |
| `keymap.replace_key(src, dst)` / `define_modifier(key, mod)` | pre-engine remap / user modifiers |
| `keymap.get_input_context()` | virtual input batch; thread-safe |
| `keymap.focus` | current `Focus` snapshot |
| `keymap.get_active_window()` / `list_windows()` / `find_window(...)` | portable `Window` objects; UI thread only |
| `keymap.screen_frames()` / `screen_work_frames()` / `window_frames()` | screen/window geometry; `screen_work_frames` UI thread only |
| `keymap.clipboard_history` | settings + `items()` / `get_current()` / `set_current()` |
| `keymap.editor` / `edit_config()` / `reload_config()` | config lifecycle (tray menu uses these) |
| `keymap.replay_buffer` | macro buffer behind the record actions |
| `keymap.pop_balloon(name, text, timeout)` / `close_balloon(name)` | balloons (UI mode only) |
| `InputText(s)` | type a literal string |
| `MoveWindow(...)` / `SnapWindow(...)` / `ActivateWindow(...)` / `LaunchApplication(...)` | window & app actions |
| `MouseMove` / `MouseButtonDown/Up/Click` / `MouseWheel` / `MouseHorizontalWheel` | mouse output actions |
| `ShowClipboardHistory()` / `ShowClipboardSnippets(...)` / `ShowClipboardTools(...)` / `DateTimeSnippet(fmt)` | clipboard UI actions |
| `ChooserAction` | base class for custom chooser popups |
| `ThreadedAction` | background work |
| `Start/Stop/Toggle/PlaybackRecordedKeys()` | keyboard macros |
| `getLogger(name)` | console logging |

Exact signatures, defaults and per-argument notes for all of these are in the
[API reference](api_reference.md).
