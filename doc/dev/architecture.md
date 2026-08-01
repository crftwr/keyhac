# Architecture

## Process & thread model

Single process. The **main thread owns everything time-critical**:

- the OS-native event loop (NSApplication run loop on macOS, `GetMessage` pump on Windows),
- the keyboard hook callback (both OSes deliver it on the thread that installed it,
  provided that thread pumps its loop),
- all PuiKit windows (PuiKit is single-threaded by design; `Panel.call_on_main_thread`
  is the only cross-thread entry).

Background work (anything that could exceed the hook deadline — network, subprocess,
file I/O, slow AX queries) runs on a worker thread via `ThreadedAction` (single shared
worker, as in keyhac-mac) and posts results back with `call_on_main_thread`.

This mirrors what both predecessors already proved in production:

- keyhac-win: `Keymap` *is* a `ckit.Window`; hook callbacks, timers and UI share its
  message loop. Long work goes to `JobQueue` threads.
- keyhac-mac: the CGEventTap source lives on the main CFRunLoop; the tap callback calls
  Python synchronously under the GIL. Long work goes to `ThreadedAction`'s
  single-worker `ThreadPoolExecutor`.

## Layers

```
┌────────────────────────────────────────────────────────────┐
│ user config (~/.keyhac/config.py)  — configure(keymap)     │
├────────────────────────────────────────────────────────────┤
│ keyhac.core      keymap engine, key expressions, focus     │
│ (pure Python)    conditions, input context, actions,       │
│                  clipboard history, macro, config loader   │
├──────────────────────────────┬─────────────────────────────┤
│ keyhac.ui  (PuiKit)          │ keyhac.platform (interfaces)│
│ console / chooser / balloon  │ InputHook Injector Focus    │
│ tray / menus                 │ Clipboard WindowCtl Screen  │
├──────────────────────────────┼──────────────┬──────────────┤
│ puikit                       │ platform.win │ platform.mac │
│ MacOSBackend / WindowsBackend│ ctypes/Win32 │ PyObjC/Quartz│
└──────────────────────────────┴──────────────┴──────────────┘
```

Rules:

- `keyhac.core` imports neither OS modules nor PuiKit. It is deterministic and fully
  unit-testable.
- `keyhac.ui` imports PuiKit only.
- `keyhac.platform.win` / `.mac` are the *only* modules touching ctypes/PyObjC.

## Module map

| Module | Contents | Ported from |
|---|---|---|
| `core/keymap.py` | `Keymap` singleton: key tables, modifier state machine, one-shot logic, multi-stroke state, replace_key/define_modifier, dispatch | keyhac-mac `keyhac_main.py` + keyhac-win modifier engine (`keyhac_keymap.py`) |
| `core/key.py` | `KeyCondition`, `KeyTable`, key-expression parser, portable key names | keyhac-mac `keyhac_key.py`; hash/eq semantics from keyhac-win (`__hash__` = vk only; `__eq__` does L/R-agnostic modifier match) |
| `core/vk.py` | Portable key-name ↔ per-OS virtual-key tables, layout variants (US/JIS) | both (`keyhac_const.py`, `str_vk_table_*`) |
| `core/focus.py` | `FocusCondition`: portable `app`/`title` match + per-OS extras + `custom_condition_func` | keyhac-mac `keyhac_focus.py` + keyhac-win `WindowKeymap.check` |
| `core/input.py` | `InputContext`: batch building, modifier reconciliation, lone-Win/Alt cancellation | keyhac-mac `keyhac_input.py` + keyhac-win `setInput_*` |
| `core/action.py` | `ThreadedAction`, `LaunchApplication`, `InputText`, record/playback actions | keyhac-mac `keyhac_action.py` + keyhac-win commands |
| `core/clipboard_history.py` | history model, persistence (JSON), snippets/tools listers | keyhac-mac `keyhac_clipboard.py` (persistence) + keyhac-win `cblister_*` (UX) |
| `core/replay.py` | key record/normalize/playback buffer | keyhac-mac `keyhac_replay.py`, keyhac-win macro normalization |
| `core/config.py` | config loading: copy template on first run, compile+exec, `configure(keymap)` call, reload | both |
| `core/log.py` | `getLogger`, stdout/stderr redirection into the console ring buffer | keyhac-mac `keyhac_console.py`, keyhac-win `Log` |
| `core/settings.py` | persisted app state (console visibility, flags) as JSON — replaces keyhac.ini | keyhac-win `keyhac_ini.py` (concept) |
| `core/vk.py`, `core/const.py` | key-name tables, modifier constants | both |
| `actions.py` | action objects needing platform/UI wiring: `MoveWindow`, `SnapWindow`, `ActivateWindow`, mouse actions, `ChooserAction` + clipboard choosers | keyhac-mac `keyhac_action.py` + keyhac-win commands |
| `platform/base.py` | abstract interfaces (`InputHook`, `FocusProvider`, `EventLoop`, `ClipboardProvider`, `AppControl`, `Window`, `WindowProvider`) + `KeyEvent`/`Focus` | new |
| `platform/fake.py` | scripted fake hook/providers for engine unit tests | new |
| `platform/win/*` | hook, injection, focus/UIA elements, windows, clipboard, apps, instance guard, loop | pyauto behaviors, reimplemented in ctypes |
| `platform/mac/*` | tap, injection+reordering, AX focus/elements, windows, pasteboard, apps, instance guard, loop | keyhac-mac Swift `KeyhacCore_*.swift`, reimplemented in PyObjC |
| `ui/console.py` | console window: LogView, hook toggle, log level, last-key / focus inspector | keyhac-mac `ConsoleWindowView.swift`, keyhac-win `ConsoleWindow` |
| `ui/chooser.py` | candidate window: incremental filter, ↑↓/Enter/Esc, modifier-aware select | keyhac-mac `ChooserWindowView.swift`, keyhac-win `ListWindow` |
| `ui/balloon.py` | frameless topmost tooltip (multi-stroke help, macro status) | keyhac-win `keyhac_balloon.py` |
| `ui/tray.py` | tray icon / menu-bar extra + menu | keyhac-win `keyhac_tasktrayicon.py`, keyhac-mac `MenuView.swift` |
| `ui/runtime.py` | holds the app's PuiKit backend for secondary windows (chooser/balloon) | new |
| `main.py` | bootstrap (below) | both |

## The key-event lifecycle

```
OS delivers key event (hook callback, main thread, deadline-bound)
  → platform normalizes to KeyEvent(vk, down, injected_kind)
  → Keymap._on_key(event)
       1. filter self-injected events (platform tags them)
       2. replace_key mapping
       3. update modifier state (incl. user modifiers, L/R planes)
       4. refresh focus if changed → select active KeyTables (merged in definition order)
       5. look up KeyCondition; on hit:
            a. string / tuple  → InputContext builds & injects a batch → consume
            b. callable        → run inline (must be fast) or ThreadedAction → consume
            c. KeyTable        → enter multi-stroke mode (+ balloon help) → consume
            d. one-shot logic on key-up
       6. no hit → pass through (with modifier-flag correction on macOS)
  → return consume/pass decision synchronously to the OS
```

The engine treats "which thread, which deadline, how injected events are distinguished
and reordered" as platform concerns. What it requires from the platform is stated in
[platform-layer.md](platform-layer.md).

## Event loop integration

### macOS

PuiKit's `MacOSBackend.run_event_loop` is `NSApp.run()`. The CGEventTap's run-loop source
is added to the main CFRunLoop (common modes) *before* starting it — identical to
keyhac-mac, where the tap coexists with the SwiftUI app loop. Timers are `NSTimer` /
CFRunLoopTimer on the same loop. Nothing special is needed beyond PuiKit's planned
multi-window support.

### Windows

`WH_KEYBOARD_LL` callbacks are delivered *during message retrieval* on the installing
thread — any `GetMessage`/`PeekMessage` wait services them. PuiKit's
`WindowsBackend.run_event_loop` is exactly such a `GetMessage` pump, so installing the
hook on the main thread before calling it is sufficient; hook latency is bounded by pump
responsiveness, and a blocking `GetMessage` pump is ideal (no polling, no idle CPU).
One pump serves all PuiKit windows (puikit `create_window`), and timers come from
puikit's `Backend.call_later` (see [puikit.md](puikit.md)).

### Hook health watchdogs (timer-driven, both OSes)

- Windows: the OS silently removes a `WH_KEYBOARD_LL` hook that exceeds
  ~300 ms (`LowLevelHooksTimeout`). Port keyhac-win's `checkSanity`: poll modifier state
  via `GetAsyncKeyState` on a 100 ms timer; if state changes N times with no hook
  callback observed, re-install the hook and reset modifier state.
- macOS: the WindowServer disables a tap that stalls. Port keyhac-mac's timer:
  `CGEvent.tapIsEnabled` check + `tapEnable(true)` + reset modifier state + notify the
  engine (`hook_restored`). Also handle `kCGEventTapDisabledByTimeout/ByUserInput` event
  types directly in the callback.

## Threading contract

| Context | May do | Must not do |
|---|---|---|
| Hook callback (main thread) | engine dispatch, injection, fast callables, posting ThreadedActions, opening PuiKit windows | blocking I/O, subprocess waits, sleeping, slow AX walks |
| ThreadedAction `run()` (worker) | slow work; `InputContext` use is allowed (it serializes with the hook via an engine lock, as keyhac-mac's `Hook.acquire_lock` does) | touching PuiKit widgets directly |
| ThreadedAction `starting()/finished()` | UI + engine access (runs on main thread via `call_on_main_thread`) | slow work |

Design change from keyhac-mac: instead of exposing a raw `Hook.acquire_lock()`, the
engine owns one `threading.RLock` guarding modifier state + injection, and
`InputContext.__enter__` takes it. Same semantics, less API surface.

## Error containment

User config code runs inside the hook deadline, so every user callable is wrapped:
exceptions are caught, logged to the console window with traceback, and the key event is
still answered (passed through on error, matching both predecessors — typing keeps working
even when the config is broken).
A config that fails to compile keeps the previous keymap active and shows the error in
the console (keyhac-win behavior).

## Testing architecture

- **Engine**: a `FakeInputHook` feeds scripted `KeyEvent` sequences and records
  consume/inject decisions — pure Python, no OS. All modifier/one-shot/multi-stroke
  semantics (the subtlest part of both old codebases, e.g. keyhac-win's L/R-agnostic
  `KeyCondition.__eq__`, oneshot cancellation, user-modifier swallowing) get regression
  tests derived from the changelog's historical bug fixes.
- **UI**: PuiKit `MemoryBackend` snapshots (`snapshot()`, `style_at`, `feed_event`).
- **Platform**: thin manual/integration test apps per OS (hook echo, injection ordering,
  focus dump) — run by hand, since CI cannot grant AX permission or install hooks.
