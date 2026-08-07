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
- **Anything reading the app's window rects must be DPI-aware.** The app writes
  its frame in physical pixels; a DPI-unaware reader gets those rects
  virtualized, so on a 200% monitor a frame round-trip compares `900x620`
  against `450x310` and fails for a reason unrelated to what is under test.
  `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` goes before the first
  rect read (`tools/bundle_pass.py`).
- **A harness must open and identify its own document.** Every modern Windows
  app worth driving is single-instance and tabbed, so launching one gets you
  the operator's session, not a clean one — and a class-name lookup cannot tell
  the difference. Open a scratch file, find the window by its title, identify
  the element by a sentinel only the harness writes, and refuse to write when
  that does not line up. See the element-API entry below for what this cost.
- **The console starts in whatever state it was last left.** `console_visible`
  in `settings.json` means a console last closed to the tray starts hidden, and
  a harness waiting for its window waits forever. Force it and put it back, the
  way the frame in the registry already is — this is what kept the bundle pass
  from ever running.

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
- **macOS element tree and text layer** (2026-08-06, Safari 18 / Chrome on a page
  built to the shape in `doc/dev/ai-integration.md` §2): `children()`,
  `describe()`, `get_ui_tree`, `find_element` by DOM id / label / role / text,
  table extraction row by row, `get_text()` on a container whose own value is
  empty, `get_line_at_caret()` at three caret positions, `get_selection()`,
  `element_at_point()`, and `set_manual_accessibility()` both on and off. Two
  bugs came out of it: `_from_ax` raised on every range attribute (CFRange
  arrives as a tuple, not a struct), and the first cut of `format_tree` hid an
  unchecked checkbox's `0` behind a truthiness test.
  **Not covered**: Terminal.app and iTerm2 whole-value reads — neither had a
  window open — so the terminal half of §6 is still unmeasured.
- **macOS waiting and AX notifications** (2026-08-06): the three-beat modal
  cycle end to end in the real thread architecture — `CFRunLoop` on the main
  thread, the action on a worker, every UI read dispatched back — press,
  `wait_for_element` (modal seen in 10–25 ms), read, press, `wait_until_gone`
  (22 ms), `wait_for_stable`, a timeout that raised at 1.03 s, and the guard
  that refuses to wait on the loop thread. `UIObserver` delivered
  `AXWindowCreated` / `AXCreated` / `AXValueChanged` / `AXUIElementDestroyed`
  for a Finder window opening, and **nothing at all** for a Safari `<dialog>`
  opening — registered on the application element and on the `AXWebArea`
  alike. Writing `AXValue` to a plain text input also did nothing, silently.
  Those two negatives are why `wait_for` polls rather than waiting on
  notifications, and they are recorded in `ai-integration.md` §5 and §7.3.

- **Windows element API, the text layer and the write side** (2026-08-07,
  `tools/uia_pass.py` against Notepad on Windows 11 Home 10.0.26200, 26/26 on
  four consecutive runs). Everything the AI-integration work added to
  `platform/win/uielement.py` had been written against `UIAutomationClient.h`
  on a Mac and had never executed; this is the pass that settled it. Every
  previously-unrun vtable slot answers correctly — `GetFirstChildElement` 4 /
  `GetNextSiblingElement` 6 (49 nodes with their real roles and AutomationIds),
  `get_DocumentRange` 7, `ExpandToEnclosingUnit` 6 with `TextUnit_Line` = 3
  (the caret's line, and *not* the whole document, which is what a wrong unit
  produces), and `ElementFromPoint` 7. `set_focus()` agrees with
  `HasKeyboardFocus`, `get_selection()` returns the selection, and the modal
  three-beat plus an idempotent `set_checked` run end to end against Notepad's
  Find UI.
- **The `set_value` measurement `ai-integration.md` §11 asks for**, timed
  against a real control: `set_value` 15–33 ms, `paste` 48–95 ms, `keys`
  114–272 ms. All three work; the ordering matches macOS with a wider spread,
  and none of it changes the **paste, then keys** default — speed is not the
  axis that matters when the fastest is the one that fails invisibly.
- **The bug the pass caught is in `fill.py`, not in the Windows layer.**
  `_paste` holds the clipboard swapped until `confirm()` answers, precisely
  because restoring as the keystroke goes out races the target's read of the
  pasteboard. With `verify=False` there is nothing to confirm, so `confirm()`
  returned immediately and the guard evaporated: the document received the
  shell command the operator had copied an hour earlier — the documented
  failure, reproduced through the one door left open. Now an unverified paste
  holds for `PASTE_SETTLE` and says in the log that it is guessing, with the
  ordering pinned in `tests/test_fill.py`.

- **`SelectionItem`, and the first action to run on both platforms**
  (2026-08-07). Porting `examples/actions/snapshot_settings.py` to Windows
  stopped before it reached a selector: a Win32 `TabItem` supports no `Invoke`,
  no `Toggle` and no `Expand` — `get_action_names()` returned `[]` — and has no
  value, so neither selecting a tab nor asking which tab was current could be
  expressed. `SelectionItemPattern` (10010) now provides both, pinned against
  `TCM_GETCURSEL` on a real `SysTabControl32` built in the test
  (`tests/test_win_focus.py`), which is the control's own answer and is not
  reachable through UI Automation — so it cannot agree with a wrong slot by
  accident. The action then walked Mouse Properties' five tabs live and wrote 15
  values to JSON, leaving the originally-selected tab selected.

**Two things the pass itself got wrong, both worth keeping in mind for any
Windows UI harness:**

- **It adopted the operator's document.** Windows 11's Notepad is tabbed and
  single-instance, so launching it merely activates the window already open and
  `FindWindowW("Notepad", None)` names *that* — on the first run, the real
  `~/.keyhac/config.py`, whose buffer every write then replaced. Nothing
  reached the disk, but a harness that can overwrite the file it is testing
  against is a harness with a bug in it. It now opens a scratch file, locates
  the window by *its* title, identifies the text element by a sentinel string
  only this pass writes, requires that element to hold keyboard focus, and
  refuses to write at all if any of that does not line up.
- **It looked for a role where it should have looked for a capability.** The
  three-beat waited for a `CheckBox`; Windows 11's Find panel has none, and its
  "Match case" is a `MenuItem` one press further in, behind "More options".
  Searching for *an element with a `ToggleState`* finds it whatever it calls
  itself — the Windows form of the macOS lesson in the authoring skill, that a
  control is defined by what it can do rather than the role it reports. The
  search has to be scoped to the panel as well as by capability: run against
  the whole window it finds the formatting toolbar's **Bold** button, which
  also has a `ToggleState` and has nothing to do with Find.

**Notepad 11 drops and reorders injected input, and this is the target's doing,
not ours.** `hello-keys` arrived in its text box as `helloke-ys`; a `Ctrl-V`
came through as a bare `v`; and an injected `Ctrl-V` is silently dropped
outright often enough that the pass retries it. The same strings down the same
code path — including with a real hook installed and through `InputContext`,
which is what `fill.py` uses — land intact in a plain Win32 control 30/30
(`tests/test_win_send_text.py` and a scratch probe), so the reordering is
WinUI's, not `SendInput`'s and not the hook's. Two consequences: `keys` is not
trustworthy against XAML editors, which is an argument for the existing
paste-first default; and every one of these arrived as a **loud** failure
because `set_text` reads back what it wrote. That rule is what turned a
silently corrupted document into a `FillFailed` naming the text it actually
found.
- **Instance guard**: cross-process on both OSes — mutex/flock contention, refusal
  reaches stderr before the std-stream redirect, kernel drops the flock on SIGKILL.
- **Bundles**: `macos_app/` built, signed, notarized and run live end-to-end (tap
  installs under the bundle identity, template config created, SIGINT quits
  cleanly); `windows_app/` passed end-to-end, 11/11 in `tools/bundle_pass.py`
  (tool-window styles, the saved frame restored and the moved one written back,
  a poisoned off-screen frame rejected onto a monitor, clean quit on the tray's
  teardown path, cross-process instance guard, no `keyhac-error.log`), with the
  tray icon + menu and the console's log pane / hook checkbox / log-level
  dropdown passed by hand alongside it.
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

## The interactive pass before a release

The genuinely-interactive checks were tracked in issue #10 and are all through
as of 2026-08-06, but they are a **standing pass, not a burnt-down backlog**:
they describe what to repeat before a release, since nothing here is covered by
the automated harnesses.

- **The `Keyhac.exe` bundle** — `tools/bundle_pass.py` does the mechanizable
  half; run it with no Keyhac running, or the instance guard makes every check
  fail for the wrong reason. By hand alongside it: the tray icon and its menu
  clicks, and the console's log pane, hook checkbox and log-level dropdown.
- **The chooser filter box under a Japanese IME** — that 「か」 composes inline
  rather than typing `ka`, that Enter is consumed by the IME to commit instead
  of choosing the highlighted item, and that the committed text filters the
  list. Needs puikit's per-window input contexts (keyhac #20, puikit `6906146`).
- **JIS layout detection on real JIS hardware** — `GetKeyboardType(0) == 7`, one
  line. The tables it selects are already pinned against `kbd106.dll`, so this is
  the only part a JIS keyboard is needed for. It cannot be faked on this machine:
  the VM presents a generic HID keyboard reporting type 4.
- **On macOS**: the tray "Edit Config" menu click and mouse output feel in real
  apps (wheel direction, drag, double-click registration). Neither is drivable
  from the sandbox — the agent shell holds Accessibility but never window-server
  key focus — so both are hand checks by construction.

On macOS: nothing outstanding — the tray "Edit Config" click and mouse feel were
both verified live.
