# 04 — PuiKit: what we use, what we must extend

PuiKit v1.0.3 (`../puikit`) is pure Python: AppKit via PyObjC on macOS, Direct2D via raw
ctypes on Windows, plus curses/web/memory backends. One `Backend` = one standard,
resizable, regular-app window. Its `CLAUDE.md` defines a strict **additive** API policy —
every extension below is designed to comply (new capability flags, new methods with base
no-ops, appended ctor params).

## Used as-is (no changes)

| Keyhac2 need | PuiKit piece |
|---|---|
| Console log view | `LogView` — virtualized, per-line styles, `max_lines` ring buffer, tail-follow, drag-select+copy. Near-perfect fit for the console window. |
| Console extras | `Checkbox` (hook toggle), `DropDown` (log level), `Label`/`TextEdit` (last key / focus path + copy), `VSplit`/`HSplit` layout |
| Chooser list | `ListView` (`row_factory` for icon+label rows) + `TextEdit` search field; filtering done app-side via `set_items()` (PuiKit's `ComboBox` shows the pattern) |
| Dialogs | `show_message_box` |
| Menus | `Menu`/`MenuItem` model + native `NSMenu`/`HMENU` via `set_menu_bar`/`popup_menu` — tray menu reuses this model |
| Clipboard (UI-side) | `Panel.get_clipboard`/`set_clipboard` (engine-side monitoring stays in `keyhac.platform`) |
| Cross-thread posting | `Panel.call_on_main_thread` — real on both GUI backends (AppHelper.callAfter / PostMessage) |
| Theming, fonts, CJK, DPI, IME | built-in (Windows Per-Monitor-V2 + `WM_DPICHANGED`; NSTextInputClient/IMM32) |
| Testing | `MemoryBackend` (snapshot, style_at, feed_event, injectable capability profiles) |
| Loop seam | `run_event_loop` / `run_event_loop_iteration` |

## Loop integration facts (verified against source)

- **macOS**: `run_event_loop` = `NSApp.run()`; a CGEventTap source added to the main
  CFRunLoop (common modes) fires during it. `call_on_main_thread` wakes a blocked loop.
  → integration works today.
- **Windows**: `run_event_loop` = blocking `GetMessageW` pump — exactly what
  `WH_KEYBOARD_LL` needs on the installing thread, with zero idle CPU.
  (`run_event_loop_iteration`'s empty-queue path is `time.sleep(≤50ms)` polling — fine
  for xefm, wrong for a hook; we use the blocking pump, not iteration mode.)
- **Windows global side effects** in `open()`: OLE init, DPI awareness — idempotent for
  our single-process use.

## Gap list → extension plan

Ordered by how hard Keyhac2 depends on them. "Cap" = new/implemented capability flag.

### E1. Secondary windows (the big one)

Keyhac2 shows **console + chooser + balloon** (up to all three) simultaneously; PuiKit
binds one backend to one window, and `Panel` to one backend.

**Fidelity decision (2026-07, crftwr):** secondary windows are *real* windows on every
backend that has them — native OS windows on macOS/Windows, real browser windows on the
web backend (`window.open` companion page, same server session) — and degrade to layers
only on TUI. Recorded in puikit `docs/window_management.md`.

Proposed additive design (details to be negotiated in ../puikit):

```python
win = backend.create_window(width, height, title="", style=WindowStyle(...),
                            frame_autosave_name=None)   # -> WindowHandle
panel = Panel(backend, window=win)                      # appended kwarg, default = main window
win.show() / hide() / close(); win.move(x, y); win.resize(w, h)
win.set_title(s); win.frame() -> Rect                   # in screen coords
```

- macOS: extra `NSWindow`s share `NSApp` — straightforward.
- Windows: extra HWNDs share the thread's pump — straightforward; per-window render
  targets already exist per backend instance, so the work is factoring window state
  ("one D2D target + swap chain per WindowHandle") out of the backend singleton state.
- Events gain a window identity internally; each `Panel` receives only its window's
  events (dispatch by hwnd / NSWindow — invisible to widget code).
- Cap: `multi_window`.

Fallback if E1 stalls: multiple `Backend` instances, one per window, pumped by a host
loop. Works in principle on Windows (pump is thread-wide); on macOS requires E3 anyway
(activation policy) and `run_event_loop` ownership fixes. The clean path is E1; the
spike in M0 decides.

### E2. Window styles: frameless / topmost / no-activate

Style masks are currently hardcoded (`NSWindowStyleMask Titled|...`,
`WS_OVERLAPPEDWINDOW`). Needed:

- **balloon**: frameless + topmost + no-activate + click-through optional
- **chooser**: thin-frame or frameless + topmost + takes-keyboard (but must not
  deactivate the target app on macOS — use `NSPanel` with
  `nonactivatingPanel`, as keyhac-mac's SwiftUI chooser effectively behaves)
- Windows: `WS_POPUP` + `WS_EX_TOPMOST` + `WS_EX_NOACTIVATE` (+ `WS_EX_TOOLWINDOW` to
  stay out of the taskbar/Alt-Tab).

`WindowStyle(frameless=…, topmost=…, activates=…, resizable=…, tool=…)` on
`create_window`. Cap: `window_styles`.

### E3. Agent-app mode (macOS)

`MacOSBackend.open()` hardcodes `NSApplicationActivationPolicyRegular` +
`activateIgnoringOtherApps_(True)` — a Dock app that steals focus. Keyhac2 is a
menu-bar-only agent (`LSUIElement`, like keyhac-mac). Additive ctor param
`activation_policy="regular"|"accessory"` (+ don't force-activate for accessory).
Windows: no-op.

### E4. System tray / menu-bar extra

Declared in `PROFILE_GUI_DESKTOP` but `system_tray: False` on both real backends
(explicitly "not on the punch list" — Keyhac2 is now the customer).

```python
backend.set_tray(icon=..., tooltip=..., menu: Menu, on_click=None)  # None removes
```

- macOS: `NSStatusBar.systemStatusBar().statusItemWithLength_` + existing `_macos_menu`
  NSMenu builder.
- Windows: `Shell_NotifyIconW` + `WM_APP` callback message on a message-only window +
  existing `_win32_menu` HMENU popup.
- Reuses PuiKit's `Menu` model unchanged. Cap: flip `system_tray` to True.

### E5. Screen geometry

No `NSScreen`/`MonitorFromWindow` exposure today. Needed for chooser/balloon placement
near caret/window and for `MoveWindow` edge logic.

```python
backend.screen_frames() -> list[ScreenFrame(full: Rect, work: Rect, scale: float, main: bool)]
```

Cap: `screen_info`. (Keyhac's *caret* query stays in `keyhac.platform` — it needs
GetGUIThreadInfo/AX, out of scope for a UI toolkit.)

### E6. `call_later`

Only per-frame `request_animation_ticks` exists. Add
`backend.call_later(delay_s, callback) -> cancel_handle` (NSTimer / SetTimer /
tick-loop fallback). Used by `keymap.call_later`, balloon timeouts, watchdog timers.
Cap: none needed (base fallback via animation ticks).

### E7. Nice-to-have, not blocking

- `ListView` incremental-search protocol (copy `TableView.search_*`) — Keyhac2 filters
  app-side initially.
- Toast/notification capability — Keyhac2's balloon is its own frameless window (E1+E2
  cover it); OS notification-center integration can come later.
- `run_event_loop_iteration` on Windows using `MsgWaitForMultipleObjectsEx` — only if
  some embedder needs iteration mode with hooks; Keyhac2 doesn't.

## Process

- Extensions are developed in `../puikit` behind its normal review/test flow
  (MemoryBackend tests + demo_catalog pages where visual).
- Keyhac2 pins `puikit >= 1.1` once E1–E6 land; during development, editable install of
  the sibling checkout.
- Every extension lands with: capability declaration in `capability.py`, base-class
  no-op/raise, both GUI backend implementations, memory-backend recording for tests,
  docs page. (This is PuiKit's own additive recipe.)
