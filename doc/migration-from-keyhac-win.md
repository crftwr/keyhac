# Migrating a Keyhac-for-Windows (1.x) config.py to Keyhac 2

Keyhac 2 adopts the modernized snake_case API (introduced by Keyhac for macOS) rather
than the 1.x camelCase one, and there is no compatibility shim — a 1.x config needs a
translation pass. The pass is mechanical: the concepts are the same, most names map
one-to-one, and key expressions are unchanged (the short modifier forms `C-`, `A-`,
`S-`, `W-`, `U0-` … are still accepted).

The entry point is the same (`def configure(keymap):`), but the file's location moved:
Keyhac 2 reads `~/.keyhac/config.py`, not `%APPDATA%\Keyhac\config.py`. Copy your
config there before translating. Clipboard history and settings move with it
(`~/.keyhac/clipboard.json` / `settings.json`); the 1.x ini and its history are not
imported.

## Translation table

| Keyhac 1.x (Windows) | Keyhac 2 |
|---|---|
| `keymap.defineWindowKeymap(exe_name="notepad.exe", class_name="Edit", window_text=…)` | `keymap.define_keytable(app="notepad", class_name="Edit", title=…)` |
| `keymap.defineWindowKeymap()` | `keymap.define_keytable(focus_path_pattern="*")` |
| `keymap.defineMultiStrokeKeymap(help_string)` | `keymap.define_keytable(name=…)` (the name shows in a balloon while armed) |
| `keymap.replaceKey(src, dst)` | `keymap.replace_key(src, dst)` |
| `keymap.defineModifier(key, mod)` | `keymap.define_modifier(key, mod)` |
| `keymap.InputKeyCommand("C-X")` | plain assignment: `kt["…"] = "Ctrl-X"` (or a tuple for a sequence) |
| `keymap.InputTextCommand(s)` | `InputText(s)` |
| `keymap.ActivateWindowCommand(exe_name=…)` | `ActivateWindow(app=…)` |
| `keymap.MouseMoveCommand(dx, dy)`, `MouseButtonClickCommand(…)`, `MouseWheelCommand(…)` | `MouseMove(dx, dy)`, `MouseButtonClick(…)`, `MouseWheel(…)`, `MouseHorizontalWheel(…)` |
| `keymap.MoveWindowCommand(…)` / monitor-edge commands | `MoveWindow(direction=…, distance=…, window_edge=…, screen_edge=…)`, `SnapWindow(position)` |
| `keymap.ShellExecuteCommand(None, "app.exe", …)` | `LaunchApplication("app.exe")`; for verbs/params use `subprocess` in a function |
| `keymap.command_ClipboardList` | `ShowClipboardHistory()` |
| `cblister_FixedPhrase(…)` | `ShowClipboardSnippets([...])` |
| `keymap.command_RecordStart/Stop/Toggle/Play` | `StartRecordingKeys()` / `StopRecordingKeys()` / `ToggleRecordingKeys()` / `PlaybackRecordedKeys()` |
| `JobQueue` / `JobItem` | `ThreadedAction` (subclass with `starting()` / `run()` / `finished()`) |
| `keymap.delayedCall(func, msec)`, `CronItem` / `CronTable` | no built-in equivalent — schedule with `threading` and hand the callback back with `keymap.call_on_main_thread(func)`; see [Timers](#timers-delayedcall--crontable) |
| `keymap.popBalloon(name, text, timeout)` / `closeBalloon(name)` | `keymap.pop_balloon(…)` / `keymap.close_balloon(…)` |
| `keymap.popListWindow(listers)` (blocking) | subclass `ChooserAction` (callback-based; there is no blocking list window) |
| `keymap.getWindow()`, `pyauto.Window` | `keymap.get_active_window()` / `find_window(…)` / `list_windows()` — portable `Window` objects; the raw HWND wrapper is `focus.native` |
| `keymap.editor = "…"` | unchanged |

## Not carried over

- **`keymap.delayedCall(func, msec)`** and **`CronItem`/`CronTable`** — no built-in
  equivalent, but both are a couple of lines of `threading` now that
  `keymap.call_on_main_thread()` exists. See [Timers](#timers-delayedcall--crontable)
  below.
- **`keymap.setFont` / `keymap.setTheme`** — UI theme/font settings are not
  configurable yet (tracked in the issue tracker).
- **Migemo matching** in the list window — not available (tracked in the issue
  tracker); the chooser filters by multi-word substring match.
- **Blocking `popListWindow`** — dropped deliberately; its nested message loop was
  the worst source of reentrancy bugs in 1.x. `ChooserAction` is the replacement.
- **`keymap.wnd`**, the `send_input_on_tru` ini hack, and profile-mode flag
  semantics — dropped.
- **Portable mode** (config next to keyhac.exe) — not implemented yet (tracked in
  the issue tracker).

## Timers (delayedCall / CronTable)

Keyhac 2 has no timer API of its own, because it does not need one: schedule with
`threading`, and hand the callback back to the main thread with
`keymap.call_on_main_thread(func)`. That last call is the important half — it is
thread-safe, and it is what makes the callback safe to touch windows, the UI and
the engine, exactly as `delayedCall` did in 1.x.

```python
import threading
import time

def configure(keymap):

    # delayedCall(func, msec) — one-shot
    def call_later(seconds, func):
        timer = threading.Timer(seconds, lambda: keymap.call_on_main_thread(func))
        timer.daemon = True   # so a pending timer never delays quitting
        timer.start()
        return timer          # .cancel() if you want to call it off

    # CronItem / CronTable — periodic
    def every(seconds, func):
        def tick():
            while True:
                time.sleep(seconds)
                keymap.call_on_main_thread(func)
        threading.Thread(target=tick, daemon=True).start()

    call_later(0.5, lambda: logger.info("half a second later"))
    every(60, lambda: logger.info("once a minute"))
```

Two things to know:

- Do **not** sleep inside a `ThreadedAction.run()` to get a delay. That pool is a
  single worker shared by every threaded action, so the sleep stalls all of them —
  and a periodic `while True:` loop there would hold the worker for the life of the
  process.
- Timers you start keep running until the process exits. Reloading the config does
  **not** cancel them, so a timer scheduled at load time is scheduled again on every
  reload. Keep them at module scope, or cancel your own if that matters to you.

## What you gain

- The same config runs on macOS — branch OS specifics on `keymap.platform`.
- Portable `app=` / `title=` focus conditions and focus-path matching via UI
  Automation (works inside UWP/Electron/Chrome windows, where the HWND tree is
  opaque).
- `focus.element` — scriptable access to the focused control (UI Automation on
  Windows), `SnapWindow` tiling, balloon-assisted multi-stroke tables, User2/User3,
  `F21`–`F24`.

The full API is in [configuration.md](configuration.md).
