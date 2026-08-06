# Testing

How Keyhac2 is tested, the reusable harness patterns, and what has been verified
live on each OS.

## Test layers

- **Engine (pure Python, CI-safe)**: `.venv/bin/python -m pytest` — the keymap engine
  runs against scripted fakes (`keyhac/platform/fake.py`), no OS access, no
  permissions. Key expressions, modifier planes, one-shot, multi-stroke, user
  modifiers, replace_key, focus conditions, InputContext reconciliation, mouse
  translation, replay normalization.
- **UI**: PuiKit `MemoryBackend` (snapshot, style_at, feed_event).
- **Platform (live)**: `tests/test_win_*.py` / `tests/test_mac_*.py` drive the real
  OS APIs — they need a desktop session, and on macOS the Accessibility permission.
  Tests marked `slow` launch real third-party apps; skip with `-m 'not slow'`.
- **Interactive tools**: `tools/hook_echo.py` echoes key events through the real
  hook; `--stress-ordering` manufactures the injected-vs-physical race (see below).

## Cross-cutting patterns

- **Fake-Quartz hook tests**: `tests/test_mac_hook.py` unit-tests the macOS
  deferral state machine against faked Quartz calls — the ordering machinery is
  testable without a tap.
- **Ordering stress**: `tools/hook_echo.py --stress-ordering` posts events from a
  third private CGEventSource (macOS) / with an unsigned `dwExtraInfo` `SendInput`
  (Windows) that the hook must classify as *real*, racing them against Keyhac's own
  output. 100/100 rounds green on macOS, 50/50 on Windows — and a crippled-hook
  negative control confirms the harness actually catches violations. This validated
  the SendInput queue-order assumption behind the `send()` contract empirically.

## Windows harness patterns (worth keeping)

- **In-process app harness**: host the real engine in the test process — real
  `WinInputHook`, real config loading, real focus provider/console, the same wiring
  `main.py` does — and play the user with *untagged* `SendInput` (no Keyhac
  `dwExtraInfo` sentinel), which the hook classifies as real. A probe window records
  every `WM_KEYDOWN`/`WM_CHAR` that reaches an app. No subprocess, so no
  single-instance-guard collision: a concurrently running production Keyhac is
  untouched (its hook sits later in the LL chain; the harness binds only F13–F24,
  which no human config uses). Lives in `tests/test_win_typing_load.py`.
- **Latency is measured, not felt.** `WH_KEYBOARD_LL` is synchronous, so typing lag
  *is* the callback duration, and a callback overrunning `LowLevelHooksTimeout`
  (300 ms) gets Keyhac silently unhooked. The harness times the callback and
  reports percentiles, which turns "does typing feel OK" into a number.
- **Load must come from other processes.** Loading the box with Python *threads*
  measures GIL contention, not system load — a different (and much worse) thing.
  `SystemLoad` spawns subprocesses; `GILLoad` is the separate, deliberate
  in-process case.
- **Re-assert keyboard focus per test, not once per module.** A heavy run can cost
  the probe its focus, and every later test then silently injects into somebody
  else's window. Skip rather than fail when focus is refused.
- **Keyboard-type DLLs pin the layout tables** (`tests/test_win_layout.py`).
  `kbdus.dll` / `kbd106.dll` export `KbdLayerDescriptor()` → `KBDTABLES`, whose
  `pusVSCtoVK` is the scancode→vk truth for that keyboard *regardless of what is
  plugged in*. `VkKeyScanEx` cannot do this job: `kbdjpn.dll` picks its variant
  from `GetKeyboardType()`, so on ANSI hardware the "Japanese" layout returns the
  US-101 mapping and would cheerfully confirm a wrong JIS table.
- **Foreground-lock probes must arm the lock.** An idle desktop does NOT arm it
  (steals succeed), and any process spawned from the foreground-process chain
  inherits steal permission — both give false-green results. A valid probe:
  WMI-spawn (outside the chain) a target that takes foreground and keeps receiving
  synthesized key taps, then reproduce/steal from a second WMI-spawned process.
- **Window classes + ctypes wndprocs in tests**: keep the probe module-scoped.
  Re-registering the class in a second fixture instance leaves `lpfnWndProc`
  pointing at the first instance's freed thunk — the next message faults the
  interpreter.

## macOS testing lessons (encoded in `tests/test_mac_window.py` etc.)

- AX requests into *our own* process are serviced by the run loop we would be
  blocking — so a mutable test window must live in a **helper child process** that
  pumps its loop. A bare NSApplication in the helper also needs
  `finishLaunching()`, or its AX server never registers and every query fails with
  `kAXErrorCannotComplete`.
- NSWorkspace state (`frontmostApplication` etc.) is refreshed only by run-loop
  callbacks — a sleep-polling wait reads the process-start snapshot forever.
  **Every wait, including fixture discovery polls, must pump the run loop.**
- The sandboxed agent-shell environment holds an Accessibility grant but is never
  granted window-server *key focus*: it can order windows front, but in-sandbox
  keyboard-focus probes give unreliable negatives. Only an interactive pass is
  conclusive for focus behavior (chooser typing, etc.).

## Live verification record

Everything automatable has been run live on both OSes (Windows 11 Home 10.0.26200;
macOS 15 on this machine). Highlights and the bugs the passes caught:

- **Windows key consumption** (in-process harness, 19/19): remap, sequences,
  replace_key, one-shot tap/held, short forms, multi-stroke incl. unmatched-consume,
  class_name table, extended-key flags, macro record/playback. The sanity-check
  re-install is verified by covert `UnhookWindowsHookEx` behind the hook's back
  (indistinguishable from the OS doing it): four modifier flips with no callbacks
  trigger re-install, and consumption works again after. Note: this Windows build
  survives a 0.6 s callback stall without unhooking, so the silent-unhook path
  cannot be provoked via `LowLevelHooksTimeout` here.
- **Chooser paste flow end-to-end** (14/14): open-with-focus, filter, Enter →
  refocus original cross-process app → Ctrl-V into a real EDIT control; Shift-Enter
  copy-only; hotkey toggle.
- **UIA slot pinning**: every COM vtable slot index in `platform/win/uielement.py`
  is pinned by a test cross-checking it against the Win32 answer for the same
  window — two wrong slots were caught that way (BoundingRectangle read a UiaRect
  of doubles over a RECT of LONGs; TextRange `GetText` sat at 12, and slot 11
  access-violated).
- **Windows clipboard**: round-trip incl. Japanese and non-BMP emoji — the emoji
  case caught a real bug (`create_unicode_buffer` sizes by code points, truncating
  surrogate pairs; `set_text` now encodes UTF-16-LE explicitly).
- **macOS AX window tests**: 15 live tests (identity, enumeration, find_window,
  frame writes, minimize/restore, activate, worker-thread geometry), mirroring the
  Windows set.
- **Instance guard**: cross-process on both OSes — mutex/flock contention, refusal
  reaches stderr before the std-stream redirect, kernel drops the flock on SIGKILL.
- **Bundles**: `macos_app/` built, signed, notarized and run live end-to-end (tap
  installs under the bundle identity, template config created, SIGINT quits
  cleanly); `windows_app/` built and import-smoke-tested, tray ran live.
- **Typing latency** (`tests/test_win_typing_load.py`, Windows 11 in a VM): at a
  sustained 60 keys/s the hook callback runs p50 ≈ 1.0 ms / p95 ≈ 2.1 ms; a
  200-event unpaced burst is p50 ≈ 0.8 ms with every keystroke translated and in
  order; and with every core busy in *other* processes the numbers barely move
  (p50 ≈ 1.4 ms). Typing feel is not load-sensitive in the ordinary sense.
- **The one load shape that does hurt is our own GIL.** A single CPU-bound
  pure-Python thread in-process — what a `ThreadedAction` doing heavy Python work
  looks like — pushes the callback to p50 ≈ 64 ms / p95 ≈ 131 ms / max ≈ 500 ms,
  i.e. past the 300 ms `LowLevelHooksTimeout`. `sys.setswitchinterval(0.001)`
  roughly halves it (p50 ≈ 42 ms, max ≈ 295 ms) but does not remove it. Config
  authors should keep CPU-bound Python out of `ThreadedAction`, or accept that
  Windows may drop the hook and `check_health()` re-install it.
- **`InputContext` from a worker blocks typing for exactly as long as it is held**
  — it takes the same `RLock` the hook callback needs. Measured: a 50 ms hold
  produces a ~50 ms callback. A hold longer than 300 ms will get Keyhac unhooked.
- **Windows layout tables**: keyhac's `ansi` and `jis` vk tables match `kbdus.dll`
  and `kbd106.dll` exactly on every physical key position where the two keyboards
  differ — the semicolon, colon, atmark, caret and bracket keys, and the two JIS
  backslash keys (¥ and ろ), which a US 101 renders as one. This is
  hardware-independent, so only the `GetKeyboardType(0) == 7` *detection* still
  needs a real JIS keyboard.
- **`edit_config` on Windows**: `WinAppControl.edit_file` → `ShellExecuteW` run
  live, 8/8 — default editor, explicit editor, a path containing spaces opening as
  one file (the quoting), an unresolvable editor logging a warning instead of
  raising (`ShellExecute` code 2), the full `Keymap.edit_config()` chain, and the
  deleted-config-recreated-from-template path.

## Remaining genuinely-interactive / hardware checks

Tracked in issue #10. What is genuinely left on Windows:

- **JIS layout detection on real JIS hardware** — `GetKeyboardType(0) == 7`, one
  line. The tables it selects are already pinned against `kbd106.dll`, so this is
  the only part a JIS keyboard is needed for. It cannot be faked on this machine:
  the VM presents a generic HID keyboard reporting type 4.
- **The chooser filter box under a Japanese IME** — blocked upstream on #20.
  PuiKit routes secondary-window messages through `_handle_secondary_message`,
  which has no `WM_IME_*` cases, and all `Imm*` association targets the main HWND.
- **The Keyhac.exe bundle's tray icon and console widgets by hand** — the tray
  menu rendering and click response, and the log pane / hook checkbox / log-level
  dropdown. Everything else about the bundle is mechanized in
  `tools/bundle_pass.py` (tool-window styles, frame-autosave round trip,
  off-screen-frame rejection, clean quit, single-instance guard). Run it with no
  Keyhac running — it refuses otherwise, because the instance guard would
  otherwise make every check fail for the wrong reason.

On macOS: nothing outstanding — the tray "Edit Config" click and mouse feel were
both verified live.
