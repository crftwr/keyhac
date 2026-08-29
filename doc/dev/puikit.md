# PuiKit: what Keyhac uses, and the extensions built for it

[PuiKit](https://github.com/crftwr/puikit) (`../puikit` as a sibling checkout) is pure
Python: AppKit via PyObjC on macOS, Direct2D via raw ctypes on Windows, plus
curses/web/memory backends. Its `CLAUDE.md` defines a strict **additive** API policy —
every extension below complies (new capability flags default off, new `Backend` methods
get base no-ops, appended ctor params).

Keyhac2 requires the PyPI release named by the `puikit>=` pin in
[pyproject.toml](../../pyproject.toml) — the only place that floor is written, and
its comment says what each move of it was for. A local editable checkout is needed
only for puikit development:
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

All developed in `../puikit` on feature branches with PRs, per its additive policy,
and merged and released there before the pin moved to them.

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
- The chooser is `WindowStyle(frameless=True, activates=False,
  overlay_input="mouse")` and is typed into through the key hook, not through the
  window — see the Chooser notes in [design-notes.md](design-notes.md).
  `overlay_input` is puikit PR #126; without it a *click* on the popup activates
  Keyhac even though a borderless window cannot become key, which deactivated the
  window underneath and broke the paste. `"keyboard"` is the same window taking
  key status instead — the Spotlight shape, where an input method works. Inert on
  Windows, where `WS_EX_NOACTIVATE` refuses both.
- Guaranteeing the window opens on the active Space would want a `WindowStyle` field
  reaching `collectionBehavior` (`NSWindowCollectionBehaviorMoveToActiveSpace`).
  Not needed while the chooser builds a fresh window per invocation.
- Being frameless costs the chooser everything a frame provides, so it draws its
  own (issue #117): a border from the outermost `Frame`, a drag handle under the
  magnifier and a resize grip in the bottom-right corner
  ([grips.py](../../keyhac/ui/grips.py)). Only the last needed puikit —
  `WindowHandle.resize_to_px` (PR #131), the pair to `move_to_px`, since nothing
  could set a window's size. Moving and resizing stay Keyhac's: macOS
  `movableByWindowBackground` and the Windows `WM_NCHITTEST` → `HTCAPTION` reply
  both mean "drag from anywhere the content is not a control", which in a window
  that is mostly a list is a gesture arguing with the list. A handle is a place,
  and the place is the application's to choose.
- macOS gives the chooser a window shadow already (its panel mask is not
  borderless, so AppKit's default applies); a Windows `WS_POPUP` has none, which
  is the other half of why the edge is drawn rather than asked for.

## Known limit

Popup text fields do not compose with an input method — and for the chooser that
is now by design rather than a gap. PuiKit gained per-window IME input contexts
in PR #90 (keyhac #20), which fixed it for a popup that holds the keyboard
focus; the chooser then stopped holding it, and composition follows OS keyboard
focus wherever it goes. See the Chooser notes in
[design-notes.md](design-notes.md) for why that trade was taken and what
`overlay_input="keyboard"` would cost to undo it. PR #90 still matters for
windows that *do* take focus.
