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
  which no human config uses).
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

## Remaining genuinely-interactive / hardware checks

Tracked as GitHub issues: JIS layout detection on a real JIS keyboard and typing
feel under load (Windows), the chooser filter box under a Japanese IME (Windows),
a full interactive pass of the Keyhac.exe bundle, `edit_config` on Windows, the
macOS tray "Edit Config" menu click, and macOS mouse feel (wheel direction, drag,
double-click) in real apps.
