# CLAUDE.md — Keyhac2

## What this project is

Keyhac2 is a ground-up unification of **Keyhac for Windows** (`../keyhac-win`, v1.83) and
**Keyhac for macOS** (`../keyhac-mac`, v1.68): a keyboard customization / macro tool whose
behavior is scripted by the user in Python (`~/.keyhac/config.py`). Keyhac2 has **one shared
Python codebase** for both OSes, with thin per-OS platform modules. All UI is built on
**PuiKit** (`../puikit`), the author's portable Python UI toolkit.

Detailed design documents live in [doc/](doc/) — start with
[doc/00-overview.md](doc/00-overview.md).

## Sibling repositories (read-only references)

| Repo | What it is | What to learn from it |
|---|---|---|
| `../keyhac-win` | Python 3.13 + thin C++ launcher (`main.cpp`). UI via `ckit`, input via `pyauto` (external C++ extension repos, **not present in this checkout**). | Synchronous `WH_KEYBOARD_LL` hook semantics, modifier-state engine, one-shot/multi-stroke logic, hook-recovery (sanity check), `hookCall` sentinel trick, clipboard history, list window UX, full feature set. |
| `../keyhac-mac` | Swift/SwiftUI menu-bar app embedding CPython 3.13 via a C++ bridge. | CGEventTap semantics (re-enable, event-source filtering, deferred-event reordering), AX focus paths, the **modern snake_case config API that Keyhac2 adopts as its baseline**, ThreadedAction model. |
| `../puikit` | Pure-Python UI toolkit, v1.0.3, PyPI `puikit`. Backends: curses / macOS (PyObjC+AppKit) / Windows (ctypes+Direct2D) / web / memory. | The UI layer for Keyhac2. Its `CLAUDE.md` documents a strict additive API-compatibility policy — all Keyhac2-driven extensions must follow it. |

## Key decisions (details and rationale in doc/)

1. **Languages**: Python 3.13 for everything at runtime. Platform bindings via **ctypes**
   (Windows) and **PyObjC** (macOS) — no custom compiled extension modules. The only
   non-Python code is a tiny PEP 587 embedding **launcher** per OS (C/C++, no app logic),
   needed for packaging and for a stable app identity (macOS Accessibility permission is
   granted per bundle). See [doc/06-packaging.md](doc/06-packaging.md).
2. **Key hook**: both OS hooks are *synchronous consume-decisions* running on the main
   thread; the differences (tap re-enable + injected/real event reordering on macOS;
   silent-unhook recovery on Windows) are encapsulated behind one `InputHook` interface.
   See [doc/02-platform-layer.md](doc/02-platform-layer.md).
3. **Config API**: keyhac-mac's snake_case API is the base, extended with portable focus
   conditions (`app=`, `title=`) and the keyhac-win features it lacks (mouse output, window
   actions, shell execute, balloons). One `config.py` runs on both OSes; keyhac-win
   configs require migration (guide in [doc/03-config-api.md](doc/03-config-api.md)).
4. **PuiKit must be extended** — additively, per its compatibility policy: secondary
   windows, frameless/topmost/no-activate styles, agent-app mode, system tray / menu-bar
   extra, screen geometry, `call_later`. See [doc/04-puikit.md](doc/04-puikit.md).
5. **Single process, main-thread rule**: the native event loop on the main thread services
   the hook *and* all PuiKit windows. Slow work goes to `ThreadedAction`; results come
   back via `call_on_main_thread`.

## Planned source layout

```
keyhac/
  core/        # OS-independent: keymap engine, key expressions, input context,
               # actions, clipboard history, config loader, logging  (pure, unit-testable)
  platform/    # interface definitions (InputHook, Injector, FocusProvider, ...)
    win/       # ctypes: WH_KEYBOARD_LL, SendInput, Win32 focus/window, clipboard listener
    mac/       # PyObjC: CGEventTap, CGEventPost, AXUIElement, NSWorkspace, NSPasteboard
  ui/          # PuiKit-based: console window, chooser, balloon/toast, tray icon
  main.py      # bootstrap: loop setup, hook install, config load
```

## Documents (doc/)

- [00-overview.md](doc/00-overview.md) — goals, background, answers to the founding questions
- [01-architecture.md](doc/01-architecture.md) — layers, event loop, threading, key-event lifecycle
- [02-platform-layer.md](doc/02-platform-layer.md) — OS-specific analysis: hooks, injection, focus, clipboard, permissions
- [03-config-api.md](doc/03-config-api.md) — user-facing API spec + compatibility/migration
- [04-puikit.md](doc/04-puikit.md) — what PuiKit provides, gaps, extension plan
- [05-features.md](doc/05-features.md) — feature parity matrix and per-feature design notes
- [06-packaging.md](doc/06-packaging.md) — launchers, embedding, bundles, data paths
- [07-roadmap.md](doc/07-roadmap.md) — milestones, spikes, risks, open decisions

## Conventions

- Public API is snake_case (keyhac-mac style). No new camelCase API.
- `keyhac/core/` must not import OS modules (`ctypes.windll`, `AppKit`, `Quartz`, Win32
  constants). All OS access goes through `keyhac/platform/` interfaces.
- UI code talks to PuiKit only; no direct AppKit/Win32 in `keyhac/ui/`.
- Tests: pytest. Keymap engine is tested with a scripted fake `InputHook` (no OS needed);
  UI is tested against PuiKit's `MemoryBackend`.
- PuiKit changes are developed in `../puikit` and must respect its additive API policy
  (new capability flags default off; new `Backend` methods get base no-op/raise).
  **Always on a feature branch with a pull request — never commit directly to its main.**
- `../keyhac-win` and `../keyhac-mac` are **read-only** references. Never modify them.

## Status

**M1 in progress.** Done so far:

- `keyhac/core/` engine complete and unit-tested (`​.venv/bin/python -m pytest`, 64 tests):
  key expressions (full names + keyhac-win short aliases), 16-bit modifier planes
  (Alt/Ctrl/Shift/Win/Cmd/Fn/User0-3 × generic/L/R), one-shot, multi-stroke, user
  modifiers, replace_key, focus conditions (app/title/class_name/focus_path_pattern/
  custom func), InputContext modifier reconciliation.
- `keyhac/platform/mac/` complete and **validated live on this machine** (M0 spike S2
  answered yes): CGEventTap from PyObjC works, incl. private event sources, own-event
  filtering, replay re-entry, deferred-real-event machinery, Carbon layout detection via
  ctypes (four-char codes 'ANSI'/'JIS '/'ISO ' — removed from modern SDK headers).
- `keyhac/platform/win/` first Windows bring-up done (commit 37cfd5c): every ctypes
  prototype is now declared explicitly — mandatory on 64-bit, where the default c_int
  restype truncates handles (this broke SetWindowsHookExW with error 126). Validated:
  hook install/callbacks, SendInput + dwExtraInfo classification, focus query, pump,
  timers. Not yet exercised interactively: physical-key consume decisions, per-VK
  extended-key flags, sanity-check re-install (see the platform/win module docstrings).
- Deliberate ports of subtle behaviors (do not "simplify" these): KeyCondition hashes by
  vk only with L/R-agnostic `__eq__`; output resolves modifiers to left-side keys
  (`force_LR`); user modifiers are never physically emitted (except replay); unmatched
  key-down leaving multi-stroke mode is still consumed; errors in user callables pass the
  key through. Three upstream keyhac-mac bugs intentionally fixed in the mac hook port —
  see the module docstring of `keyhac/platform/mac/hook.py`. The third (fresh real
  input could overtake re-posted deferred reals during the flush window) was found by
  the ordering verification added later: `tests/test_mac_hook.py` unit-tests the
  deferral state machine against faked Quartz calls, and `tools/hook_echo.py
  --stress-ordering` manufactures the injected-vs-physical race live (a third private
  CGEventSource / unsigned-`dwExtraInfo` `SendInput` posts events the hook must
  classify as real); 100/100 rounds green on macOS after the fix, and a crippled-hook
  negative control confirms the harness catches violations.

M2 progress:

- PuiKit window-management extensions live on branch `keyhac-window-extensions` in
  `../puikit` (**PR #76**, awaiting review): `WindowStyle`
  (frameless/topmost/activates/resizable/tool), `MacOSBackend activation_policy=
  "accessory"` (agent app), `Backend.call_later`. All additive per PuiKit's policy;
  1540 puikit tests green; validated live on macOS. (Shipped in puikit 1.0.4;
  keyhac2 now depends on `puikit>=1.0.6` from PyPI. The Makefile installs a
  local checkout editable only when `PUIKIT_DIR` is set — via gitignored
  `Makefile.local` or the environment — and `make install-puikit` switches an
  existing venv between the two sources. **Caveat**: `set_tray(image=…)`
  (PR #82) merged after the 1.0.6 release, so until 1.0.7 ships the venv
  needs the editable checkout or the menu bar extra silently degrades to the
  tiny "⌨" text glyph — this happened once when a PyPI install replaced the
  editable one; `Makefile.local` with `PUIKIT_DIR = ../puikit` now pins it.
  The main-window visibility API (PR #84, below) is in the same boat.)
- `keyhac/ui/console.py`: the console window (LogView + hook toggle + log level +
  last-key/focus-path inspector). The console backend runs the process event loop;
  the hook shares it (tap source on the same run loop / GetMessage pump). Verified
  live on macOS incl. screenshot. `keyhac/main.py`: console by default, `--no-ui`
  for the headless M1 mode.
- Still open in M2: Windows console session, stdout redirect to console.
- Tray / menu-bar extra (`keyhac/ui/tray.py`): menu (console / reload / hook
  toggle / quit) plus the keycap icon from issue #8 — the keyhac-win app-icon
  design. The vector artwork is the hand-maintained source of truth, both in
  `art/`: `icon.svg` (color) and `MenuExtraTemplate.svg` (the menu extra — an
  AppKit-template glyph: the keycap as line art, outline plus key-top edge
  lines, faces open, y squashed 0.87, strokes kept light (1.7pt/1.1pt)
  because heavier ones fuse the tapering side faces shut at menu-bar size;
  shaded faces are no option either — template rendering keeps only alpha,
  so they let the menu bar bleed through). `tools/make_icons.py`
  renders both through `tools/svgrender.py` — a pure-stdlib SVG-subset
  rasterizer (documented subset, fails loudly outside it) that runs
  identically on macOS and Windows, no NSImage/Direct2D/pip deps — into
  `keyhac/ui/assets/keyhac.ico` (Windows tray + app icon, 16-256 px),
  `keyhac.icns` (macOS app icon up to 1024 px), and
  `MenuExtraTemplate.png` + `@2x` (the menu extra; deliberately a bitmap
  pair, not a runtime-loaded SVG — macOS caches a system-side
  rasterization of vector status-item images by file identity, so
  in-place SVG edits could leave menu bars compositing stale artwork);
  store banners etc. are one more line in its `main()`. Uses puikit's
  `set_tray(image=…)` (PR #82, merged). macOS NSStatusItem verified live
  incl. menu-bar screenshots; Windows tray icon not yet run.

Windows bring-up (second session, all verified live on Windows):

- **PuiKit gaps closed** (PRs #76-#79, all merged): window extensions, the DPI
  font-cache fix (every widget label rendered at half size on a 200% display —
  text formats resolved before `open()` survived at the placeholder 1.0 scale),
  Windows `create_window()` (real secondary HWNDs, one DXGI swap chain each on the
  shared D3D device — this unblocked the chooser and balloon), and the
  `system_tray` capability flag.
- **Cross-platform config diagnostics**: an unknown key that exists on the *other*
  OS says so; parse errors carry their reason; a warning names modifiers no key can
  produce on this OS (`Cmd-`/`Fn-` bindings from a macOS config parse fine on
  Windows but silently never fire).
- **`keyhac/platform/win/uielement.py`**: UI Automation via raw ctypes (no comtypes),
  vtable-slot calls like puikit's `_win32_dragdrop`. Element attributes, control-view
  parent walk, Invoke/Toggle/Expand/Collapse actions, Value/SelectedText patterns.
  UIA rather than the HWND tree because a UWP/Electron/Chrome window is one HWND
  holding the whole UI. **Every slot index is pinned by a test cross-checking it
  against the Win32 answer for the same window** — two wrong slots were caught that
  way (BoundingRectangle read a UiaRect of doubles over a RECT of LONGs; TextRange
  `GetText` sat at 12, and slot 11 access-violated).
- **Windows focus path** is now the full UIA control hierarchy
  (`/Application(Code)/Window(…)/…/Edit(Message input)`), matching macOS AX
  granularity. A full walk costs ~33 ms, so the provider caches on a ~0.01 ms Win32
  probe and walks only on change — it runs inside the hook on every key event.
- **Portable `Window`/`WindowProvider`** (`platform/base.py` + `win/window.py` +
  `mac/window.py`): find/enumerate/activate/restore/move. `MoveWindow` and
  `ActivateWindow` now run on both OSes. Thread contract is explicit: window
  accessors are UI-thread only (AX SIGTRAPs off-main on macOS; window-title reads
  are a blocking `SendMessage` on Windows), and only `screen_frames()` /
  `window_frames()` are safe from a `ThreadedAction` worker.
- `tools/hook_echo.py --stress-ordering` now run live on Windows: 50/50 rounds
  green — the SendInput queue-order assumption behind the `send()` contract is
  empirically validated, not just assumed.
- Not yet run on Windows: clipboard provider, `send_text`, balloon, and — most
  importantly — **key consumption** (every session so far logged only PASSTHRU).
  See [doc/windows-session.md](doc/windows-session.md).
- Windows tray now runs live; first Keyhac.exe bundle session surfaced two fixes:
  the console (and chooser) are now `WindowStyle(tool=True)` — tray-only presence,
  no taskbar button, the Windows analog of `activation_policy="accessory"` — and
  puikit **PR #83** (awaiting review) fixes frame autosave persisting a minimized
  window's iconic rect (−32000,−32000 → console restored unreachably off-screen;
  the poisoned `HKCU\Software\PuiKit\FrameAutosave\KeyhacConsole` value was
  deleted by hand). PR #83 (merged) also adds tests/conftest.py so puikit's
  suite runs on Windows at all (pytest-timeout signal→thread), exposing 4
  pre-existing Windows-only test failures (background_3d gate,
  terminal_graphics ×2, a measure_text metric) left for a separate fix.
- Single-instance guard + console-visibility restore (verified live on
  Windows and macOS, incl. cross-process): `platform/{win,mac}/instance.py` — a
  session-local named mutex on Windows; flock under ~/.keyhac on macOS —
  checked in main() *before* the std-stream redirect so the refusal reaches
  stderr. A second UI-mode launch exits 1 and re-shows the running instance's
  console (FindWindow "PuiKitWindowClass"/"Keyhac" — a deliberately pinned
  puikit internal — with a message-box fallback). The console's shown/hidden
  state persists as `console_visible` in `settings.json` (`core/settings.py`,
  write-through JSON; lives beside the config under --config like
  clipboard.json), polled from the console's health tick since PuiKit has no
  visibility-change callback. Uses puikit **PR #84** (`start_hidden` ctor
  flag, `Backend.hide_main_window` / `is_main_window_visible`; merged, and
  the MacOSBackend implementation verified live) — feature-detected via
  `hasattr(Backend, "is_main_window_visible")`, so on a pre-1.0.7 PyPI puikit
  the console just always starts visible.

M3 progress (macOS verified live; Windows pending): clipboard history
(`core/clipboard_history.py` + `platform/*/clipboard.py` poll-based providers, JSON
format compatible with keyhac-mac), chooser window (`ui/chooser.py`, on puikit
`create_window`), actions (`keyhac/actions.py`: ChooserAction/ShowClipboard* with
refocus-then-paste flow; `core/action.py`: ThreadedAction/LaunchApplication).
`keyhac/ui/runtime.py` holds the app's PuiKit backend for chooser/balloon windows.
With `--config PATH`, clipboard.json lives beside the config (sandbox isolation).
Remaining in M3: the Windows session (clipboard provider + WinAppControl +
chooser + `send_text`/InputText). The macOS side of M3 is closed: the
UIElement AX port (`platform/mac/uielement.py`, the full keyhac-mac API
surface incl. the 3-arg `set_attribute_value(name, type, value)`) and
ActivateWindow/MoveWindow are live-verified.
See [doc/07-roadmap.md](doc/07-roadmap.md).

macOS session (2026-08-01, all verified live on this machine):

- `tests/test_mac_window.py` — 15 live AX tests mirroring test_win_window.py
  (identity, enumeration, find_window, frame writes, minimize/restore,
  activate, worker-thread geometry). Two macOS facts the tests encode: AX
  requests into *our own* process are serviced by the run loop we would be
  blocking, so the mutable test window lives in a helper child process that
  pumps its loop (a bare NSApplication also needs `finishLaunching()` or its
  AX server never registers and every query fails with kAXErrorCannotComplete);
  and NSWorkspace state (frontmostApplication etc.) is refreshed only by run
  loop callbacks — a sleep-polling loop reads the process-start snapshot
  forever, so waits must pump the run loop.
- **`MacWindow.activate()` fix**: NSRunningApplication `activateWithOptions:`
  is a *cooperative* request that macOS 14+ ignores when the caller is not the
  active app (which Keyhac usually is not). Activation now writes AXFrontmost
  on the application element — the call keyhac-mac shipped with — with the
  cooperative route kept as fallback.
- `platform/mac/instance.py` cross-process pass: flock contention between
  real processes, holder-pid note preserved, kernel drops the lock on SIGKILL
  (no stale-lock case), and main() refuses a second launch in both UI and
  --no-ui modes — tested against a genuinely running dev instance.
- Window-control gap-fill (same session): `SnapWindow(position, ratio=0.5)`
  in `keyhac/actions.py` — left/right/top/bottom/full tiling within the
  screen's **work area**, best-screen pick reused from MoveWindow;
  deliberately a plain main-thread action (no edge scan, and the work-area
  source is AppKit). New `WindowProvider.screen_work_frames()`
  (NSScreen.visibleFrame flipped to AX coords on macOS — UI-thread only,
  unlike the CG-backed thread-safe pair; rcWork on Windows, written to
  spec, not yet run there) plus `keymap.screen_frames() /
  screen_work_frames() / window_frames()` wrappers. Template gained snap
  (`LEADER-Ctrl-J/L/I/K`, `LEADER-F`) and minimize (`LEADER-M`) samples.
  Geometry pinned in tests/test_actions.py (fakes); live macOS snap +
  work-frames tests in test_mac_window.py. Testing lesson recorded there:
  fixture discovery polls must pump the run loop too (NSWorkspace-backed
  find_window in a sleep loop intermittently never sees the helper app).
- Chooser keyboard focus (user-reported): opening the chooser from a hotkey
  left keyboard focus in the previous app. `activate_pid` used the
  cooperative `NSRunningApplication activateWithOptions:`, which macOS 14+
  ignores when the caller is not the active app — exactly the chooser-open
  moment (refocus-for-paste kept working because there Keyhac *is* active
  and merely yields). Now: own pid → `NSApp activateIgnoringOtherApps:`
  (AX cannot target our own process — the write would be serviced by the
  run loop we block; if this ever stops being honored, plan B is
  keyhac-mac's proven trick of letting LaunchServices activate us via a
  registered URL scheme); other pids → AXFrontmost write with the
  cooperative call as fallback, mirroring `MacWindow.activate()` (verified
  live against Finder). **Both directions verified interactively** — the
  chooser takes typing on open. Caveat learned the hard way: the sandboxed
  agent-shell environment can order windows front but is never granted
  window-server key focus, so in-sandbox chooser-focus probes give
  unreliable negatives; only an interactive pass is conclusive.
- `UIElement.parent()` added on macOS (AXParent) — the element-shape parity
  doc/03-config-api.md promises had it on Windows only; live-tested (an
  AXWindow's parent is its AXApplication).
- Macro record/playback verified interactively on macOS (template
  `LEADER-[` / `LEADER-]`): the end-to-end record → playback loop works on
  top of the already stress-tested replay machinery. (M4 item; Windows
  pass still pending with the rest of the Windows session.)
- `macos_app/` launcher verified live end to end: build.sh rebuilt the bundle
  (Developer ID signed; the notarize+staple+DMG path had already run
  successfully on Jul 30 — the stapled bundle validates), and the app runs the
  real thing — embedded Python boots, keyhac.main imports from bundled
  Resources, template config created on first run, **CGEventTap installs under
  the bundle identity** (the Accessibility grant is attached to
  crftwr.Keyhac2), console + menu-bar extra live, SIGINT quits cleanly (hook
  uninstalled, exit 0 through the launcher; a second launch exits 1). Bundle
  note: Resources/python_packages ships all of PyObjC incl. PyObjCTest —
  pruning would cut bundle size and the per-.so signing time noticeably.

Packaging (M5, pulled forward): release pipeline + both native launchers exist,
ported from XeFM's Makefile/bundle system (the standard to follow for build
infra). `make tag` / `release-github` / `release-whl` / `release-status` +
`tools/{release_preflight,bump_version,_version_source}.py`;
`windows_app/` (launcher.c + build.ps1, **built and import-smoke-tested on
Windows**, interactive run pending) and `macos_app/` (main.m + AppDelegate +
build.sh + create_dmg.sh, **built, signed, notarized and run live on macOS** —
see the macOS session notes above). Bundle id
defaults to `crftwr.Keyhac2` (`BUNDLE_ID=` overrides; roadmap open decision #4).
Details in [doc/06-packaging.md](doc/06-packaging.md).
