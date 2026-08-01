# PuiKit: what Keyhac uses, and the extensions built for it

[PuiKit](https://github.com/crftwr/puikit) (`../puikit` as a sibling checkout) is pure
Python: AppKit via PyObjC on macOS, Direct2D via raw ctypes on Windows, plus
curses/web/memory backends. Its `CLAUDE.md` defines a strict **additive** API policy —
every extension below complies (new capability flags default off, new `Backend` methods
get base no-ops, appended ctor params).

Keyhac2 requires **`puikit >= 1.0.8`** from PyPI — the first release carrying
everything it uses. A local editable checkout is needed only for puikit development:
set `PUIKIT_DIR` (via gitignored `Makefile.local` or the environment) and run
`make install-puikit` to switch an existing venv between the two sources.

## Used as-is

| Keyhac2 need | PuiKit piece |
|---|---|
| Console log view | `LogView` — virtualized, per-line styles, `max_lines` ring buffer, tail-follow, drag-select+copy |
| Console extras | `Checkbox` (hook toggle), `DropDown` (log level), `Label`/`TextEdit` (last key / focus path + copy), `VSplit`/`HSplit` layout |
| Chooser list | `ListView` (`row_factory` for icon+label rows) + `TextEdit` search field; filtering app-side via `set_items()` |
| Dialogs | `show_message_box` |
| Menus | `Menu`/`MenuItem` model + native `NSMenu`/`HMENU` — the tray menu reuses this model |
| Clipboard (UI-side) | `Panel.get_clipboard`/`set_clipboard` (engine-side monitoring stays in `keyhac.platform`) |
| Cross-thread posting | `Panel.call_on_main_thread` — real on both GUI backends (AppHelper.callAfter / PostMessage) |
| Theming, fonts, CJK, DPI, IME | built-in (Windows Per-Monitor-V2 + `WM_DPICHANGED`; NSTextInputClient/IMM32) |
| Testing | `MemoryBackend` (snapshot, style_at, feed_event, injectable capability profiles) |
| Loop seam | `run_event_loop` / `run_event_loop_iteration` |

## Loop integration facts (verified against source and live)

- **macOS**: `run_event_loop` = `NSApp.run()`; the CGEventTap source added to the main
  CFRunLoop (common modes) fires during it. `call_on_main_thread` wakes a blocked loop.
- **Windows**: `run_event_loop` = blocking `GetMessageW` pump — exactly what
  `WH_KEYBOARD_LL` needs on the installing thread, with zero idle CPU.
  (`run_event_loop_iteration`'s empty-queue path is `time.sleep(≤50ms)` polling — fine
  for other embedders, wrong for a hook; Keyhac uses the blocking pump.)
- **Windows global side effects** in `open()`: OLE init, DPI awareness — idempotent for
  Keyhac's single-process use.

## Extensions delivered for Keyhac2

All developed in `../puikit` on feature branches with PRs, per its additive policy;
all merged and released by puikit 1.0.8.

| PR | What it added |
|---|---|
| #76 | `WindowStyle` (frameless / topmost / activates / resizable / tool), `MacOSBackend activation_policy="accessory"` (agent app, no Dock icon), `Backend.call_later` |
| #77 | DPI font-cache fix: text formats resolved before `open()` survived at the placeholder 1.0 scale — every widget label rendered at half size on a 200% display |
| #78 | Windows `create_window()`: real secondary HWNDs, one DXGI swap chain each on the shared D3D device — unblocked the chooser and balloon |
| #79 | `system_tray` capability flag |
| #82 | `set_tray(image=…)` — bitmap tray/menu-bar icons |
| #83 | Frame-autosave fix: a minimized window's iconic rect (−32000,−32000) was persisted, restoring the console unreachably off-screen. Also added `tests/conftest.py` so puikit's suite runs on Windows at all (pytest-timeout signal→thread) |
| #84 | `start_hidden` ctor flag, `Backend.hide_main_window` / `is_main_window_visible` — console visibility persistence |
| #85 | Windows test-suite fixes for the failures #83 exposed (all stale test contracts) |
| #86 | `LogView` sized-font clip fix: rows wrap-packed by native measure but clipped/hit-tested by grid columns lost their right-hand tail — needed for the 11pt console log |

Keyhac2-side usage notes:

- The console (and chooser) use `WindowStyle(tool=True)` on Windows — tray-only
  presence, no taskbar button — the Windows analog of macOS
  `activation_policy="accessory"`.
- The console's WM_CLOSE hides instead of quitting (`main_window_close="hide"`);
  shown/hidden state persists via #84's visibility API.
- The chooser deliberately activates our own process to take keyboard input, then
  re-activates the original app on selection/cancel. A true non-activating chooser
  needs an `NSPanel` with `nonactivatingPanel` — a possible future PuiKit extension.

## Known limit

IME composition stays attached to the main HWND on Windows, so a popup text field
types ASCII but does not compose — the chooser's filter box with a Japanese IME is
the case to watch. Fix would be puikit work; tracked in the issue tracker.
