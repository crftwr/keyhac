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
- **And sample it *during* a burst, not only at the end.** The steal is usually
  transient — a window opens, takes focus and gives it back — so an
  end-of-burst check sees nothing wrong while the test still fails one
  keystroke short. `type_keys` now watches focus on every pump and skips if it
  ever left the probe. This is what turned an intermittent red into an honest
  skip: the live input tests were failing roughly one run in four, and the
  cause was the desktop being busy with *other work of ours* — Notepad, a
  terminal and VS Code being opened and closed by the UIA probes running
  alongside. On a quiet desktop the same tests passed 4/4 full runs before the
  change and 353/353 after, with no skips, so the guards are inert until
  something really is interfering.
- **Injected input is occasionally lost before any hook sees it, and that is
  not the engine's doing.** Measured on the mouse side, which is where it is
  cleanly attributable: `SendInput` returns 1 with no error, the `WH_MOUSE_LL`
  handle is still valid, and no callback ever arrives — while a second wheel
  sent immediately after is seen normally, so the hook is alive and the *event*
  went missing. Rare, timing-sensitive, and more likely when other injection
  preceded it. Two consequences for tests that inject: retry the stimulus
  rather than asserting on one particular event surviving, and where the count
  is the assertion, measure what actually reached the engine first. The typing
  harness records every vk the hook was handed for exactly that reason — 100
  taps is 200 events, and fewer means the burst never arrived to be translated,
  which is a skip rather than a translation failure.
- **An IME that was just closed costs the *next* test's injection its tail.**
  The same loss, arriving through a neighbour rather than through the test's
  own burst. `tests/test_win_ime.py` opens the IME; without settling after it
  closes one again, the Japanese case of `tests/test_win_send_text.py` received
  `日本語入力` for `日本語入力のテスト` about one run in five, while that module
  alone passed 8/8 and the pair passed 6/6 with the IME test deselected — which
  is how it was attributed. A test that changes IME state owns putting it back
  *and* pumping afterwards.
- **A guard must not be able to hide the defect the test exists to catch.**
  These skip only on *detected interference* — focus observed to leave the
  probe, or the pointer observed moving while nothing is injecting — never on
  "the assertion failed". Under deliberately manufactured hostility (a Notepad
  spawned every 1.5 s plus the cursor jogged continuously) the integrity tests
  correctly skip, while a burst that genuinely loses keystrokes still fails,
  and the latency characterization still reports a 342 ms callback overrun.
  That last one is a true result about a machine under that much load, not
  noise to be suppressed.
- **A claim one layer makes about another needs a test that spans both.**
  `tests/test_cancel.py` checks the engine's Esc rule by handing
  `on_key_event` a `KeyEvent`, and its docstring states the rest as fact:
  Keyhac's own translated output "never reaches on_key_event at all (the
  platform layer drops it on its own tag)". That is a claim about
  `platform/win/hook.py`, and nothing was checking it — so an action pressing
  Escape to dismiss a dialog was only *believed* not to kill itself.
  `tests/test_win_cancel.py` settles it by varying nothing but `dwExtraInfo`
  across real `SendInput` at a real `WH_KEYBOARD_LL` hook: untagged cancels
  and is swallowed, `EXTRA_INFO_OWN` never reaches the engine, and
  `EXTRA_INFO_REPLAY` arrives but does not cancel — each also checked for what
  the focused window did or did not receive.
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
- **A Win key as a user modifier, on Windows 11** — the pass that produced the
  refusal in `define_modifier` ([design-notes.md](design-notes.md#why-a-windows-key-cannot-be-a-user-modifier)).
  With `define_modifier("LWin", "LUser0")`: an unbound `LWin-G` opened the Game Bar
  even though Keyhac emits no Win key, and a `U0-G` action typing "git status" typed
  "it status" — the injected `g` eaten as Win+G, every other letter arriving. Also
  measured: `replace_key("LWin", 235)` + `define_modifier(235, "LUser0")` changes
  nothing; the same user modifier on `RAlt` is clean; and injecting `U-LWin` at the
  head of the batch fixes the typing *and* does not open the Start menu. Also
  confirmed: `Win+L` locks the screen with the Win key retired, which is Windows
  reserving that combination ahead of the hook chain and is not fixable. Reproducing
  any of this needs a real Game Bar, so none of it is in the automated suite.

  A scratch probe that consumes a physical key in its own low-level hook while
  watching `GetAsyncKeyState` and a raw input sink shows *nothing* surviving the
  consume, for a Win key or an ordinary one - with the key passed through, both
  channels register, which is what makes the negative readable. So the Game Bar is
  not reading either of those. Which route it does use is still open.

  Checked against keyhac-win 1.83 on the same machine - the released build run
  portable (a `config.py` beside `keyhac.exe` keeps it off `~/.keyhac`), stock
  configuration, Keyhac2 shut down: `Win+L` locks, `Win+G` opens the Game Bar, and a
  `U0-G` bound to `InputKeyCommand("G","I","T",...)` types "it status". The same
  command under a real Alt keeps its `g` and does not move focus to Notepad's menu
  bar - which is `cancel_oneshot_win_alt` working upstream, and the behavior the
  Keyhac2 port reproduces. The behavior is inherited, not a regression.
- **Lone Win/Alt cancelling** (Notepad, Windows 11): `kt["Alt-G"]` bound to an
  action, triggered with Alt physically held. The action runs and the menu bar does
  not take focus - the `VK_LCONTROL` tap marks the modifier used before the Alt is
  released and again after it is re-pressed. Notepad is the check that matters here:
  it has a menu bar to lose focus into.
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
- **macOS IME** (2026-08-23, `tests/test_mac_ime.py`, Kotoeri installed): reading
  the state off `kTISPropertyInputModeID` was verified against both ends
  (`com.apple.keylayout.US` → off, `...RomajiTyping.Japanese` → on), and on/off
  round-trips. The measurement that shaped the implementation: `TISSelectInputSource`
  on the Roman mode fails with **OSStatus −50 (paramErr)** because that mode is
  disabled on a default Japanese setup — hence the ASCII-capable-layout fallback,
  which is also what the Eisu key reaches on such a setup. The probe also showed
  the palette input sources (`CharacterPaletteIM`, `50onPaletteIM`, `PressAndHold`)
  report no input mode, so the "first enabled non-Roman mode" selection cannot land
  on one. The tests restore the input source they found.
- **Windows IME** (2026-08-23, `tests/test_win_ime.py`, Windows 11 26200 with
  Microsoft IME for Japanese): 11 live tests, and the pass that **caught two real
  bugs** — the module as first written did not work at all, and its own tests had
  passed against the broken behavior because they only ever compared the API with
  itself. What broke that circle was checking against ground truth: driving the IME
  with the OS's own `VK_IME_ON`/`VK_IME_OFF` and typing `aiueo` into a real control
  to see whether あいうえお or `aiueo` came out.
  1. **The foreground window is the wrong window to ask.** A frame and its focused
     control resolve to different default IME windows. With the IME genuinely on,
     the focused control's answered `open=1` while the frame's stayed at `0`, so
     `get_status()` reported off while Japanese was composing, and `set_status(True)`
     returned True, read back True, and changed nothing visible. Fixed by resolving
     through `GetGUIThreadInfo(...).hwndFocus`; `test_the_focused_control_is_asked_not_the_frame`
     is the guard.
  2. **An open status under a non-IME layout is a phantom.** Under en-US,
     `set_status(True)` reported success, composed nothing, and the flag was gone
     after the next layout switch (measured — it is not even latent). Both calls are
     now gated on `ImmGetProperty(hkl, IGP_CONVERSION)`. Note `ImmIsIME()` is *not*
     usable for that gate: it answered true for the US layout too.
  The tests switch the probe thread's layout with `ActivateKeyboardLayout`, so both
  halves — IME layout and plain layout — run on one machine without touching its
  settings. Timing held throughout: 0.17 ms per query, far inside `SEND_TIMEOUT_MS`.
  Three application families were swept by hand, since the frame-vs-focus shape
  differs between them: **Notepad** (focus is a `RichEditD2DPT` child of the frame —
  the case that exposed the bug), **Edge/Chromium** (focus *is* the frame; correct
  either way, and what every Electron app looks like), and a **PuiKit window** — where
  `set_status(True)` returns False and should: PuiKit associates no input context
  with a window until a text field wants one (`ImmGetContext` → 0), so there is no
  IME to open, and the API refuses rather than reporting a success it did not have.
  Still not covered on this machine: a **TSF-only IME** that answers nothing, which
  is the only path to `None` (none installed to test against).
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
  window open — so the terminal half of §6 is still unmeasured *on macOS*. The
  Windows half is measured; see the Text-pattern survey below.
- **macOS waiting and AX notifications** (2026-08-06): the three-beat modal
  cycle end to end in the real thread architecture — `CFRunLoop` on the main
  thread, the action on a worker, every UI read dispatched back — press,
  `wait_for_element` (modal seen in 10–25 ms), read, press, `wait_until_gone`
  (22 ms), `wait_for_stable`, a timeout that raised at 1.03 s, and the guard
  that refuses to wait on the loop thread. `UIObserver` (since removed —
  see below) delivered
  `AXWindowCreated` / `AXCreated` / `AXValueChanged` / `AXUIElementDestroyed`
  for a Finder window opening, and **nothing at all** for a Safari `<dialog>`
  opening — registered on the application element and on the `AXWebArea`
  alike. Writing `AXValue` to a plain text input also did nothing, silently.
  Those two negatives are why `wait_for` polls rather than waiting on
  notifications, and they are recorded in `ai-integration.md` §5 and §7.3.
  **Not covered: notifications from an Electron application.** The Safari
  result is about WebKit web content, and the separate Chromium/Electron
  measurement the same day was about *tree exposure* — 59 nodes until
  `set_manual_accessibility()`, 119 after — which is a different question.
  Expecting the Safari answer to carry over to Electron is reasonable and is
  inference, not measurement. `tools/ax_notification_pass.py` settled it on
  macOS: the Finder control passed in the same run (`AXFocusedUIElementChanged`,
  `AXCreated`, `AXValueChanged`, `AXUIElementDestroyed`), Chrome's tree was
  exposed at 522 nodes, and **Chrome posted nothing at all** across three runs
  while a driven in-page change verifiably happened inside the listening window
  — a `<dialog>` opened and closed and the page's status went "3 items pending"
  → "0 items pending". Chromium content behaves like WebKit content.

  Two bugs in the pass, both found only by running it: `open -a Finder ~` is a
  no-op when a Finder window is already open — the state the pass's *own*
  previous run leaves behind, so the second run reported a dead control and
  thereby invalidated its own Electron row — and `ELECTRON_CANDIDATES` said
  "Visual Studio Code" where NSWorkspace says "Code".

  **The observer, `wake=` and this pass were then removed** on the strength of
  that result: notifications never arrive for the content this workload
  targets, and the native-only win did not justify the surface. All three are
  in git history (`a60fa81` and its parents) should the question be reopened —
  which is also where to start if a future macOS or Electron version is worth
  re-measuring. A true Electron *application* was never measured; this was
  Chromium the browser.

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
- **The `set_value` measurement `ai-integration.md` §10 asks for**, timed
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
  (2026-08-07). Porting `examples/actions/mac/snapshot_settings.py` to Windows
  stopped before it reached a selector: a Win32 `TabItem` supports no `Invoke`,
  no `Toggle` and no `Expand` — `get_action_names()` returned `[]` — and has no
  value, so neither selecting a tab nor asking which tab was current could be
  expressed. `SelectionItemPattern` (10010) now provides both, pinned against
  `TCM_GETCURSEL` on a real `SysTabControl32` built in the test
  (`tests/test_win_focus.py`), which is the control's own answer and is not
  reachable through UI Automation — so it cannot agree with a wrong slot by
  accident. `examples/actions/win/snapshot_settings.py` then walked Mouse
  Properties' five tabs live and wrote 15 values to JSON, leaving the
  originally-selected tab selected.

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

- **The Text pattern in the applications people actually run** (2026-08-07,
  `tools/text_pattern_survey.py`). Notepad had been the only Windows evidence
  for §6's cheap rung, and it is a weak proxy — one document surface, no
  renderer. **Windows Terminal** returns 366 characters of scrollback from a
  `Text` element with `get_line_at_caret()` giving one line of it; **VS Code**
  exposes the editor as an `Edit` named for the open file. Both answer, so the
  read-everything-and-match path holds on Windows as it does on macOS.
  Notepad runs alongside as a **control**, because a survey that finds nothing
  in VS Code cannot otherwise tell "VS Code exposes no text" from "the probe is
  broken" — and on the first two runs the probe *was* broken, so this earned
  itself immediately.
- **An Electron window's text is absent from the first read.** VS Code offered
  12 Text-pattern elements and no buffer on one probe and 26 with the buffer
  minutes later, from identical code: Chromium enables renderer accessibility
  when a UIA client attaches, and has not finished by the time that client's
  first read returns. Windows needs no equivalent of macOS's
  `set_manual_accessibility()`; it needs a retry. The mechanism was not
  isolated — the process was the operator's own VS Code and could not be
  restarted cold — but the observation repeated and the consequence is the same
  either way.
- **Two ways to launch a terminal wrongly**, both of which cost a run.
  `wt.exe` treats `;` as its *own* subcommand separator, so a PowerShell
  `-Command` containing one is split into extra tabs and never arrives intact;
  use `-File`. And `wt --title` holds only until the hosted program names the
  window, which PowerShell does immediately, using its own command line — which
  contained the survey's sentinel, so the terminal's window matched the *next*
  target's title search and got measured and reported as VS Code. Set the title
  from inside the shell, and give every target its own token.

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
  hardware-independent; the `GetKeyboardType(0) == 7` *detection* is the one
  part that needed a real JIS keyboard, and it was confirmed on one by hand
  (2026-08-07). Both halves of the layout story are now closed: the tables
  against the keyboard-type DLLs, the detection against the hardware.
- **`edit_config` on Windows**: `WinAppControl.edit_file` → `ShellExecuteW` run
  live, 8/8 — default editor, explicit editor, a path containing spaces opening as
  one file (the quoting), an unresolvable editor logging a warning instead of
  raising (`ShellExecute` code 2), the full `Keymap.edit_config()` chain, and the
  deleted-config-recreated-from-template path.

## The interactive pass before a release

The genuinely-interactive checks were tracked in issue #10 and are all through
as of 2026-08-07, but they are a **standing pass, not a burnt-down backlog**:
they describe what to repeat before a release, since nothing here is covered by
the automated harnesses. The two that needed hardware this machine does not
have — a JIS keyboard, and a Japanese IME driving the chooser — were passed by
hand on 2026-08-07 and stay on the list for the next release rather than being
struck off.

- **The chooser on Windows, after the non-activating rework** *(new, not yet
  passed)* — this branch changed the chooser on both OSes but was developed and
  live-checked only on macOS, and **Windows takes the other code path**:
  `overlay_input` has no Windows equivalent and is inert, so the window is a
  plain `WS_EX_NOACTIVATE` + `WS_POPUP` one with no panel behind it. Nothing
  below is covered by the harnesses, because all of it is about what the OS does
  with a window that never takes focus.

  - *Symbols in the filter field.* `tests/test_win_char_for_key.py` covers the
    mechanism (`ToUnicodeEx`) and runs on any layout; what it cannot reach is a
    layout with an **AltGr layer** (`@` and the backslash on German/Nordic) or a
    **dead key**. If such a keyboard is available, type both; if not, skip it
    honestly — the dead-key path deliberately calls `ToUnicodeEx` twice for the
    builds that ignore `TOUNICODE_NO_STATE`, and only real hardware proves the
    next keystroke is not left composing.
  - *The window never takes focus.* Opening the chooser leaves the foreground
    window's title bar active and its caret blinking; typing reaches the filter
    (through the hook, not the window); clicking a row selects it without the
    window underneath deactivating; **Enter pastes with no settle delay** —
    `_PASTE_DELAY` is now skipped entirely on this path, so a paste that lands
    in the wrong place or not at all is this check failing.
  - *It looks right without a title bar.* `frameless` is set on both OSes
    because macOS needs it to hide the title bar its panel mask forces; on
    Windows that means `WS_POPUP`, so the chooser lost the frame it had in
    2.x. Confirm it still reads as a window and not as a floating rectangle.
  - *Auto-dismiss.* Switch virtual desktops (Win+Ctrl+arrow) and the chooser
    closes — then the hotkey opens a fresh one *on the new desktop*, which is
    the bug that started this. Clicking another window closes it; scrolling a
    background window with the wheel or trackpad does **not**.
  - *Two panes.* Down steps from the field into the list, Up off the first row
    steps back, typing anywhere returns to the field, and the selection draws
    in the accent colour while the list has focus and not at all while the
    field does.
  - *The console stays put.* It never came forward on Windows (the macOS cause
    was app-scoped activation, which `WS_EX_NOACTIVATE` never did), so this is
    confirming nothing regressed rather than checking a fix.
  - *Migemo loads.* `pymigemo` is a hard dependency now. The LP64 dictionary
    patch is inert on Windows (`array('L')` is already 4 bytes there), so this
    is confirming the engine reports itself loaded and that romaji finds
    Japanese in the filter — not the patch.

- **The `Keyhac.exe` bundle** — `tools/bundle_pass.py` does the mechanizable
  half; run it with no Keyhac running, or the instance guard makes every check
  fail for the wrong reason. 11/11 for 2.2.1, on a bundle rebuilt against the
  PyPI PuiKit wheel — which exercises two paths an editable checkout never
  does: the bundled fonts coming from the wheel instead of `scripts/
  fetch_fonts.py`, and PuiKit's LICENSE being read out of `puikit-*.dist-info`
  instead of a checkout root. By hand alongside it *(passed 2026-08-08)*: the
  tray icon and its menu clicks, and the console's log pane, hook checkbox and
  log-level dropdown.
- **`is_stale()` against a really-destroyed element** — `tools/uia_pass.py`'s
  staleness section, which owns its own window precisely because it has to
  destroy it. It is not a formality: matching UIA_E_ELEMENTNOTAVAILABLE alone
  was wrong, because a control destroyed underneath us answers **E_UNEXPECTED
  for its first ~90 ms** and only then settles on the named constant — so
  `is_stale()` said False during the one moment it is asked, and `_press`
  blamed the operator's selector for a dialog that had closed. A destroyed
  *top-level window* is a separate matter and deliberately only reported, not
  checked: UIA sometimes keeps answering S_OK for one with its ControlType
  degraded Window→Pane, and sometimes fails, so an assertion either way is a
  coin flip.
- **The chooser filter box under a Japanese IME** *(passed 2026-08-07,
  re-passed 2026-08-08)* — that 「か」 composes inline rather than typing `ka`,
  that Enter is consumed by the IME to commit instead of choosing the
  highlighted item, and that the committed text filters the list. Needs
  puikit's per-window input contexts (keyhac #20, puikit `6906146`). Repeat it
  whenever that input-context code moves — which is exactly why 2.2.1 repeated
  it: that commit is what `puikit>=1.0.10` pins, and 2.2.1 is the first release
  taking it from the published wheel rather than a local checkout.
- **The Windows JIS IME key names** *(new, not yet passed)* — that `Kanji`,
  `Henkan` and `Muhenkan` actually match the 半角/全角, 変換 and 無変換 keys when
  pressed on JIS hardware. The VKs (0x19 / 0x1C / 0x1D) are the documented ones,
  but no JIS keyboard was available to confirm what the hardware really reports;
  the console's last-key display is the one-line check. Same hardware dependency
  as the entry below, so pass them together. Still open after the 2026-08-23 IME
  pass — that machine reports `GetKeyboardType(0) == 4`, so it cannot answer this.
  What the pass *did* confirm is the other half of those names: injecting
  `VK_IME_ON`/`VK_IME_OFF` (what `Kana`/`Eisu` send) drives Microsoft IME exactly
  as `set_ime_status()` does.
- **JIS layout detection on real JIS hardware** *(passed 2026-08-07; **skipped
  for 2.2.1** — no JIS keyboard available)* — `GetKeyboardType(0) == 7`, one
  line. The tables it selects are pinned against `kbd106.dll` independently, so
  the keyboard is needed only for the detection. It still cannot be faked on the
  development VM, which presents a generic HID keyboard reporting type 4 — so
  this one needs the hardware present, or it needs skipping honestly rather than
  assuming. 2.2.1 took the second option: nothing in this release touches
  layout detection, and recording the skip is the point of the sentence above.
- **On macOS**: the tray "Edit Config" menu click and mouse output feel in real
  apps (wheel direction, drag, double-click registration). Neither is drivable
  from the sandbox — the agent shell holds Accessibility but never window-server
  key focus — so both are hand checks by construction.

Where 2.2.1 left it, since "nothing outstanding" is a claim that goes stale one
release at a time: on macOS the tray "Edit Config" click and mouse feel were
both verified live (2026-08-07). On Windows, 2.2.1 re-passed the Japanese IME,
the tray icon and menu, and the console's log pane, hook checkbox and log-level
dropdown by hand (2026-08-08), and ran the bundle pass 11/11 against a bundle
built from the PyPI PuiKit wheel. **The one check not repeated for 2.2.1 is JIS
layout detection**, for want of the keyboard — skipped and recorded, not
assumed.
