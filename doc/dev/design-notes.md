# Feature design notes

Per-feature design decisions and the subtle behaviors deliberately carried over from
the predecessors (keyhac-win 1.83 / keyhac-mac 1.68 — the frozen feature references).
Keyhac2 targets the **union** of both feature sets; the few remaining gaps are
tracked as GitHub issues (migemo matching, cron/periodic API, themes/fonts, i18n,
portable mode, rich clipboard formats).

## Deliberate ports of subtle behaviors — do not "simplify" these

- `KeyCondition` hashes by vk only, with an L/R-agnostic `__eq__` — the dict-lookup
  trick behind side-agnostic matching.
- Output resolves modifiers to **left-side** physical keys (`force_LR`).
- User modifiers are never physically emitted (except during replay).
- An unmatched key-down leaving multi-stroke mode is still consumed (no stray
  keystroke leaks into the app).
- Errors in user callables pass the key through — typing keeps working on a broken
  config.
- Three upstream keyhac-mac hook bugs are intentionally *fixed* in the mac hook port —
  see the module docstring of `keyhac/platform/mac/hook.py`. The third (fresh real
  input overtaking re-posted deferred reals during the flush window) was found by the
  ordering verification in [testing.md](testing.md).

## Clipboard history

- Model from keyhac-mac (`max_items=1000`, label truncation, size quotas);
  persistence `~/.keyhac/clipboard.json`, format-compatible with keyhac-mac.
- Saves are debounced (upstream rewrote the whole JSON on every copy); flushed on
  quit and session end.
- Monitoring is poll-based on both OSes behind `ClipboardProvider.poll()`:
  sequence-number probe on Windows, `changeCount` on macOS (~1 s tick — history does
  not need 33 ms latency).
- Paste flow (the battle-tested keyhac-win shape): set clipboard → refocus target
  app → serialized `Ctrl-V`/`Cmd-V` injection. Shift-select = copy-only.

## Chooser

- Async, callback-based (`ChooserAction.list_items/on_chosen`) — keyhac-win's
  blocking `popListWindow` is deliberately not carried over (its nested message loop
  was the worst reentrancy source in 1.x).
- Filtering: multi-word AND substring (keyhac-mac behavior). The match function is a
  hook point for a future migemo port.
- Placement: centered on the focused window, clamped to its screen; one chooser at a
  time — the same action's hotkey toggles it closed, a different chooser replaces it,
  and the replacement inherits the app to refocus.

## Console

- `LogView` ring buffer with per-level colors, log-level dropdown, hook on/off toggle
  (with AX permission recheck on macOS), last-key + focus-path inspector fields with
  copy buttons. Log text at 11pt, one below the 12pt UI font.
- `print()` and `getLogger` both land here; stdout/stderr are redirected into the
  console ring buffer.
- Closing hides; visibility persists in `settings.json` (polled from the console's
  health tick — PuiKit has no visibility-change callback).

## Balloon

- Frameless topmost no-activate PuiKit window near the focused window. Used for
  multi-stroke help (restores a keyhac-mac FIXME) and macro record state; timeout via
  `call_later`.

## Tray / menu-bar extra

- Same `Menu` model on both OSes: Open Console, Edit Config, Reload Config,
  Keyboard Hook (toggle), Quit.
- The keycap icon artwork is hand-maintained SVG in `art/` (`icon.svg` for the app
  icon, `MenuExtraTemplate.svg` for the macOS menu extra — an AppKit-template glyph:
  line art only, since template rendering keeps only alpha; strokes kept light so the
  tapering side faces don't fuse shut at menu-bar size). `tools/make_icons.py`
  renders both through `tools/svgrender.py` (a pure-stdlib SVG-subset rasterizer that
  runs identically on both OSes) into `keyhac/ui/assets/` — `.ico`, `.icns`, and the
  menu-extra PNG pair (deliberately bitmaps, not runtime-loaded SVG: macOS caches a
  system-side rasterization of vector status-item images by file identity).

## Macro record/playback

- keyhac-mac's design on both OSes: a dedicated replay event source, and replayed
  keys re-enter the keymap (recorded bindings expand on playback).
- keyhac-win's normalization rules in `core/replay.py`: drop unmatched downs,
  1000-event cap, release-modifiers-before-play.

## Window actions

- `MoveWindow`: keyhac-mac's direction/edge/multi-monitor logic in core, backed by
  `Window.set_frame` (SetWindowPos / AXPosition+AXSize). Reads windows on the UI
  thread, computes on the worker — the `ThreadedAction` pattern.
- `SnapWindow(position, ratio=0.5)`: left/right/top/bottom/full tiling within the
  screen's **work area**; a plain main-thread action (no edge scan, and the work-area
  source on macOS is AppKit — UI-thread only).
- `ActivateWindow(app=, title=)`: on Windows, `Window.activate()` attaches to
  **both** the foreground and target threads (the classic single attach is no longer
  honored by Windows 11 under an armed foreground lock); on macOS it writes
  AXFrontmost, with the cooperative `activateWithOptions:` as fallback (macOS 14+
  ignores the cooperative call when the caller is not the active app).

## Mouse output

- Portable actions over `InputHook.send_mouse`/`cursor_pos`. Buttons/wheels release
  held modifiers (keyhac-win behavior); relative moves are injected as absolute
  positions so pointer acceleration cannot distort them (native on Windows; on macOS
  CG events are inherently absolute and relative moves accumulate onto
  `cursor_pos()`).
- macOS specifics: motion while a button is held posts the *dragged* event type;
  `kCGMouseEventClickState` escalates rapid same-button downs so synthetic
  double-clicks register; wheels scroll 3 lines per notch (Windows default feel).
- One-shot cancel on click: observation-only `WH_MOUSE_LL` on Windows; button-down +
  scrollWheel types join the tap mask on macOS (motion deliberately untapped —
  Python must not sit in the path of every pointer move). Own output is recognized
  via `dwExtraInfo` / event source and ignored.
