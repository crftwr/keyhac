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

## Clean-room skill testing

A packaged skill is a snapshot with no repository behind it, and this
repository is full of the answers. So "is the skill self-contained?" cannot be
asked from a session that has read `doc/`, `examples/actions/` or the source —
it cannot unsee them. `.claude/skills/keyhac-skill-cleanroom/` is the
procedure: build the versioned bundle, unpack it outside the checkout, hand a
**fresh session** the bundle and a task, and forbid everything else.

The distinction that makes it workable: **the MCP endpoint is allowed, the
checkout is not.** Reading the screen through Keyhac's own tools is what the
skill tells an author to do — it is the product's public interface. Reading the
package's source, or another action somebody wrote with the repository open,
is inside information.

**The output is `QUESTIONS.md`, not the action.** Every point where the clean
room had to guess is a line the skill should have carried; a run that produces
a working action and no questions has measured nothing. Two rules follow from
that and are easy to get wrong: the operator must not answer questions during
the run (an answered question is a destroyed finding), and the case's "must"
list stays out of the room, because it is the scoring key.

Scoring is `evals/check.py` for the mechanical rules and the case's "must" list
for judgement, and `evals/cases.md`'s rule stands: a "must" missed is a skill
defect, not a model defect.

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
- **A live test that opens an app has to close the window it opened — and only
  that one.** Windows 11 ships Notepad as a packaged app: System32's
  `notepad.exe` is a stub that hands the work to one process shared by every
  Notepad window, so `Popen.terminate()` closed nothing and the UIA probes left
  a window behind per run. That is precisely the "other work of ours" above.
  `TestUIAPatternsAgainstNotepad` takes the window that was not on screen
  before the launch and posts `WM_CLOSE` to that one, which also stops the
  fixture from typing into a Notepad the developer has a real file open in —
  `find_window(app="notepad")` was free to answer with theirs.
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
  `HasKeyboardFocus` — which was too weak a question, as the focus pass below
  found: both were the element's own opinion of itself. `get_selection()`
  returns the selection, and the modal three-beat plus an idempotent
  `set_checked` run end to end against Notepad's Find UI.
- **The `set_value` measurement `ai-integration.md` §10 asks for**, timed
  against a real control: `set_value` 15–33 ms, `paste` 48–95 ms, `keys`
  114–272 ms. All three work; the ordering matches macOS with a wider spread,
  and none of it changes the **paste, then keys** default — speed is not the
  axis that matters when the fastest is the one that fails invisibly.
- **Does Windows `set_focus()` report a focus that never landed?**
  (2026-09-04, `tools/win_focus_pass.py`, 22/22, plus a browser probe, on
  Windows 11 Home 10.0.26200.) It did, and not for the reason the question
  assumed. Every HWND-backed scenario *lands*: a control in the foreground
  window, in a background one, asked from a process holding no foreground
  rights, disabled, clipped offscreen, in a minimized window, a label, a tab
  item with no HWND of its own — all of them, and Windows even lets the focus
  onto a disabled control and into a `WS_EX_NOACTIVATE` window. What lags is
  the *observer*: `GetGUIThreadInfo` trailed a landed focus by 23–86 ms. That
  gap is not the macOS bug in Windows clothing, though, because a keystroke
  injected the moment `set_focus()` returned still arrived at the target —
  the focus change and the key queue on the same thread, in that order.

  The lie needs a provider that is not the HWND proxy. In an Edge page, a
  `<div>` and a `<p>` both answered `SetFocus` with `S_OK` while the focus
  stayed in the text field that already had it; Notepad's WinUI status bar
  does the same, which is what `tests/test_win_focus.py` now pins against a
  real application. That is the shape a wrong action target has, and
  `set_text()` on that answer types the caller's data into whatever field the
  user was last in.

  The cheap fix — reading the already-declared `HasKeyboardFocus` — was
  measured and rejected: an Edge field reports it `True` while the browser is
  behind another window and the keyboard is elsewhere, which is exactly the
  failure macOS's docstring describes. So the answer comes from
  `GetFocusedElement` compared with `CompareElements` (slot 3, newly pinned
  against handles Win32 already knows apart).

  **`set_focus()` no longer answers at all.** Returning a verdict was the
  deeper mistake — the released code returned the HRESULT, and the first fix
  here returned the landing, and both are one bool too few: there are two
  honest verdicts —
  the focus is *on* this element, or it is *inside* it — and the platform
  layer cannot choose between them for the caller. Both are now named and
  separately testable, on both platforms: `has_focus()` and
  `contains_focus()`. `set_focus()` performs the act and returns `None`, so a
  caller who forgets to check fails closed rather than open, which is the
  shape of the bug this whole entry is about.

  Choosing between them is the caller's job, and the caller of record is the
  action author. `focus()` and `set_text()` require `has_focus()` — there is
  no list of roles allowed to write on an inside-answer, because such a list
  is platform *data* (`ComboBox`, `AXComboBox`, and whatever the next
  framework calls it), and inventing a portable vocabulary for platform data
  is the thing this layer has refused to do since the element API existed.
  The Action API exposes both questions instead: `UINode.has_focus()` and
  `UINode.contains_focus()`, with the combo box written up in the authoring
  skill's `quirks.md`.

  Refusing everything strictly is only reasonable because the delegating case
  has a target that works. Measured: a Win32 ComboBox shows its `Edit` part in
  the public tree with `AutomationId '1001'`, `fill.focus()` on the part
  answers True in 43 ms, and `set_text()` on it reads back as `'typed-inner'`
  on the part *and* on the combo box. So the advice “name the part” is advice
  that works, not a shrug. The cost is real and worth stating: `set_text()`
  aimed at a combo box worked in 2.3.0 (everything did — `set_focus()`
  returned True unconditionally) and now fails after the full `FOCUS_TIMEOUT`.

  What makes that failure survivable is that it says what happened.
  `contains_focus()` is read for *diagnosis* and nowhere for permission: when
  the strict check fails and the focus turned out to be inside the target, the
  `FillFailed` names the element that actually took it — “the focus landed
  inside it, on Edit (identifier '1001') - write to that element if it is the
  one you meant”, measured against a real combo box and a real page. It does
  not say *why* the focus is inside: a control delegating to its part and a
  container merely holding the focused control look the same from there, and
  naming what took it serves both. Permission and diagnosis are
  different uses of the same fact, and only one of them can put text in the
  wrong field.

  Containers stay refused by the same one rule, with no special case: with a
  page field focused, the `<div role=group>` around it, the Document and
  *three* Panes above it all answer `contains_focus()` true, as do the probe
  window and the desktop root in the Win32 case — six levels of elements that
  cannot take a keystroke.

  The principled alternatives that were tried and failed measurement, since
  they will occur to the next person too. `IsKeyboardFocusable` (slot 27) does
  not discriminate: a top-level Window and the desktop Pane both report True,
  and correctly so, since a window with no focusable child really does hold
  the focus itself. “The value can be read back from the target” does not
  either: a Chromium Document reports its URL as a value. And the delegating
  family is smaller than it looks — a Win32 Spinner does **not** delegate (the
  focus lands on the Spinner itself), and neither does a page's `<select>`.

  What this costs when the selector is simply wrong, measured through the
  public path (`find(name=...)` on a page, then `focus()` / `set_text()`): a
  named container is refused after the full `FOCUS_TIMEOUT` with
  `FillFailed(attempted=())` and nothing typed anywhere, where allowing
  containment answered True in 7 ms and put the keystroke into a field the
  action never named. The asymmetry is the whole argument.

  **The macOS half was written unverified, and the measurement found a
  defect** (2026-09-05, macOS 26.6.2, TextEdit). `contains_focus()` answered
  **False for every container** — the exact opposite of the contract above.

  The cause was one shared fallback. `AXUIElementCreateSystemWide()` lists
  `AXFocusedUIElement` and `AXFocusedApplication` among its attributes and
  then answers `kAXErrorCannotComplete` (−25204) for both — every read, with
  the messaging timeout raised to two seconds — while the frontmost
  application answers the same attribute instantly. Both predicates used that
  read as their primary path and fell back to the element's own `AXFocused`,
  which answers `has_focus()`'s question and not `contains_focus()`'s. So
  `has_focus()` stayed right by luck and `contains_focus()` never walked at
  all: measured above a focused `AXTextArea`, the `AXScrollArea` and
  `AXWindow` holding it both reported False.

  `MacFocusProvider.get_focused_element()` has had the correct two-step
  resolution since it was ported — system-wide first, then
  `NSWorkspace.frontmostApplication()` — because this failure was already
  known here. The predicates, written on a machine with no Mac, re-derived
  the system-wide read without it. The fix is one resolution
  (`uielement.focused_element()`) shared by both callers, and no `AXFocused`
  fallback anywhere: when the focus cannot be read, False is the safe answer.
  After it, the same probe reports `contains_focus()` true on the
  `AXScrollArea`, `AXWindow` and `AXApplication` above the focused element,
  with `has_focus()` true on the element alone.

  The lesson is the one the Windows text layer taught: a second
  implementation of a question the codebase has already answered is where the
  known workaround gets left out. Pinned hermetically in
  `tests/test_mac_uielement.py` — the front-application fallback, the
  unreadable-focus case, and that neither predicate consults `AXFocused`.

  Still unmeasured on a Mac: whether an AXComboBox delegates to an inner
  AXTextField the way the Win32 one does — if it does not, macOS simply never
  takes that path.

  The payoff, measured after the fix: `fill.focus()` on a real field answers
  True in 50 ms, and on a `<div>` spends the whole of `FOCUS_TIMEOUT` asking
  again before answering False (1053 ms) — the retry loop `7f9edfa` added
  could not engage on Windows before, because the first ask always said yes.
  `set_text()` on that element then raises `FillFailed` with an empty
  `attempted`, and nothing is typed anywhere. No change to `keyhac/core/`.

  One thing the pass cannot measure and now says so: every UIA call into a
  window whose thread has stopped pumping blocks indefinitely — over 20 s for
  `from_hwnd` alone, before `set_focus` is even reached. A hung application is
  a hang, not a lie, and section I of the pass runs behind a watchdog because
  a first run took the whole pass down with it.
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

- **Candidate-window streaming, macOS** (PR #125, real `MacOSBackend`, not the
  memory one). The measurement that mattered was of the *delivery*, not the walk:
  a bare tick callback runs at the idle rate, **10.0 Hz measured** (median gap
  100.0 ms), so `_drain`'s 2 ms slice is a 2% duty cycle. End to end, same window
  and sources, before → after moving the accessibility walks to a worker:
  Chrome menus **11,723 → 257 ms** (212 rows), VS Code menus 4,724 → 213,
  iTerm2 menus 7,420 → 311, Chrome controls 9,544 → 342. First row 130 → ~60 ms.
  A 212-row fill is 6 `_append` calls rather than 212. Dismissed 40 ms into a
  walk: 0 rows landed afterwards and no worker thread survived.

  What made the worker safe was measured rather than argued. **A frozen target
  blocks only the worker**: against an application deliberately stopped on its
  main thread, an AX call sat in the worker for 1.51 s — returning
  `kAXErrorCannotComplete` on AX's own ~1.5 s messaging timeout — while the main
  thread's Python throughput did not move (4.91M vs 5.0M iterations per half
  second). **The contention runs the right way round**: a main thread busy with
  Python starves the worker (149 AX calls/s against 24,400), not the reverse.
  **Cost to the main thread** with the walk flat out: a hook-sized piece of work
  goes from 0.06 ms median to 0.19 (p95 0.20, max 0.32). **One worker is the
  right number**: six applications on six threads took 948 ms against 935 ms for
  the same six in a row — the GIL is the ceiling, not the IPC wait. **No
  autorelease pool needed**: RSS plateaus across 25 repeated walks with and
  without one.

  The rule that had ruled all this out was one level too broad — `platform/base.py`
  says AX *into our own process* off the main thread SIGTRAPs, and design-notes
  had generalised it. Both now say so.

- **The chooser's keystrokes off the hook callback, macOS half** (PR #126, whose
  own numbers were all measured on Windows and which says so). Verified here on
  real hardware — real `MacOSBackend`, real `Keymap`, real `ChooserWindow`, keys
  fed the way the hook feeds them — 7/7: keys are queued while the callback runs
  and not acted on there; they land in order (`alp` from A, L, P) and filter;
  five in a burst all land; a closing key is not followed by one acted on after
  it; `dismiss()` drops what was queued. **The callback is 0.021 ms median with
  8,000 candidates in the window.**

  The macOS *before* figures, for comparison with the Windows table in #126 —
  and the reason the two look so different is row length, not the platform:

  | candidates | 45-char rows | 200-char | 800-char |
  |---|---|---|---|
  | 250 | 0.7 ms | 1.3 ms | 4.0 ms |
  | 1,000 | 1.5 ms | 3.9 ms | 13.2 ms |
  | 4,000 | 5.5 ms | 15.0 ms | 53.3 ms |
  | 8,000 | 10.4 ms | 30.6 ms | 109.3 ms |

  So Windows' 259 ms at 8,000 is not anomalous. macOS had more headroom than
  Windows did — `kCGEventTapDisabledByTimeout` is less eager than a 300 ms
  `LowLevelHooksTimeout` — but the shape is the same on both, and the deferral
  is correct and harmless here. What makes deferring safe is not a macOS
  property: `Keymap._on_key_down` returns True for a grab unconditionally,
  without consulting the modal handler's return value, so the callback was
  never deciding anything.

- **Candidate-window streaming, Windows** (PR #125's other half, real
  `WindowsBackend`, targets addressed by HWND so the same window is measured on
  every repeat). The tick is a genuine 60 fps here, not macOS's idle 10 Hz:
  **median gap 15.935 ms** over 40 intervals (62.76 Hz), which makes the 2 ms
  slice a **12.55% duty cycle**. The improvement that predicts — about 8x — does
  not appear, and the reason is worth keeping: **the slice is smaller than one
  row costs.** A single `next()` on the controls walk is a median 2.77 ms
  (Explorer), 3.19 (VS Code), 3.39 (Chrome), and **84-89% of rows exceed the
  2 ms budget on their own**. `_drain` checks its deadline *after* appending, so
  a slice that can only afford one row still takes one: the old path ran at
  roughly one row per frame, ~16 ms/row, whatever the duty cycle said. Against
  `rows x 15.9 ms`: Explorer 96 rows predicts 1527 and measured 1721; Chrome 36
  predicts 572 and measured 723. The ceiling on the ratio is therefore
  `tick / per-row cost`, ~15.9/5.6 ~= 2.8x.

  | window (rows) | walk alone | before | after | ratio |
  |---|---|---|---|---|
  | Explorer, 5-file folder (96) | 517 ms | 2143 ms | 646 ms | 3.3x |
  | VS Code (202) | 1296 ms | 5016 ms | 1637 ms | 3.1x |
  | Chrome, `about:blank` (36) | 216 ms | 723 ms | 293 ms | 2.5x |
  | Notepad, WinUI (22) | 144 ms | 367 ms | 228 ms | 1.6x |
  | XeFM, PuiKit (10) | 32 ms | 65 ms | 78 ms | 0.83x |

  First row is 47-162 ms after and 28-198 ms before — the old path was never slow
  to *start*. **XeFM is the honest counter-example**: 10 rows at 0.44 ms each fit
  several to a slice, so the drain was never the bottleneck and the worker's
  cross-thread dispatch costs slightly more than it saves. The win scales with row
  count, not with tree size. `MenuItemsSource` yields nothing on Windows and
  `get_attribute_values` is a plain loop here, so all of this is the worker and
  nothing else. A Settings (UWP) `CoreWindow` yields **0 named controls**; not
  investigated.

  These are the numbers the `CacheRequest` question was waiting on: the per-row
  figures above are its sizing — ~5.6 ms mean per *reported* row against a 15.9 ms
  frame.

  **Cost to the main thread**, hook-sized work every 10 ms, 200 samples: idle
  baseline 0.38 median / 0.75 p95, a worker walking with rows discarded 0.35 /
  0.68 — *indistinguishable from idle* — and the full background path 0.55 / 5.41.
  The tax is `_append` re-ranking each batch, not the UIA traffic. (`max` is not
  trustworthy at this resolution: the idle baseline itself threw a 15.10 ms
  outlier.) **A frozen target blocks only the worker**, same as macOS: against a
  window that stops draining its message queue, `ElementFromHandle` sat in the
  worker for **24,746 ms** — returning the instant the target resumed — while main
  thread throughput held at **99.9%** of baseline. Note there is no timeout, and
  `_stop_background`'s event is only checked between rows, so closing the chooser
  does not free a worker wedged inside a call. It is a daemon thread, so it never
  blocks exit; repeatedly opening the chooser against a hung application leaks one
  thread per invocation. Not addressed.

- **The COM apartment the walk runs in, Windows** (PR #126). The suspected failure
  does not exist on this OS, and looking for it found a different one. **No failure
  could be provoked**: 65,555 HRESULT-returning COM calls from uninitialised
  workers into Explorer, Chrome, Notepad, VS Code, Settings and XeFM — including
  six concurrent workers sharing the main thread's interface pointers — returned
  not one negative HRESULT. Every vtable return was inspected by wrapping
  `_com_call`, not just the ones callers look at. `CoGetApartmentType` says why: an
  uninitialised worker reports **`MTA / IMPLICIT_MTA`**, Windows 8+ placing a
  thread that touches COM without initialising it into the process-wide implicit
  MTA — the apartment Microsoft recommends for UIA clients anyway.

  **The reachable bug is the ordering.** Before anything in the process touches COM
  a fresh thread reports `CO_E_NOTINITIALIZED`, and `get_automation()` initialises
  *its own* thread as an STA. Measured: a worker reaching it before `main()`
  becomes the process **MAINSTA**, leaving the cached process-wide automation
  pointer bound to an apartment that dies with that thread. Only ordering stops it
  today. Fixed by claiming `COINIT_MULTITHREADED` up front, after which the same
  call returns `RPC_E_CHANGED_MODE` — already accepted.

  **What the fix does not reach, with the evidence that sizes it**: element
  pointers are made on the main thread and called from the worker unmarshalled.
  Asked directly, `IUIAutomationElement` implements **`IAgileObject`** — the bulk
  of the traffic is legitimate by declaration, not by luck. `IUIAutomation` and
  `IUIAutomationTreeWalker` do **not** (`E_NOINTERFACE`; the root's `IMarshal`
  names unmarshal class `0000033a-...`, not the free-threaded `0000001b-...`), and
  the worker calls both — the root once per walk, the walker once per node. If a
  failure ever appears, try a per-thread automation instance and walker, not
  `CoMarshalInterThreadInterfaceInStream`. **Untested**: an elevated target.
  `regedit` refused to launch (`WinError 740`) and nothing elevated had a window,
  so the one case where UIPI could still produce the predicted `RPC_E_*` has not
  been exercised.

- **The chooser's keystrokes off the hook callback, Windows half** (PR #126, whose
  numbers these are). Two warnings — 204 ms then 344 ms, both vk 67 — with two
  separate causes. **Migemo's dictionary**, read lazily on the first query long
  enough to reach the engine: `_load_engine`'s docstring budgets ~50 ms, measured
  it is **190 ms alone and 267 ms through `compile()`**. Not one character's fault
  — `MIN_LENGTH` is 3, and measured, a one- or two-character query never loads the
  engine at all (0.04-0.19 ms, engine untouched); it lands on the third keystroke,
  once. And **filtering, which is linear in the candidate count**: 250 rows 17 ms,
  1,000 37 ms, 4,000 131 ms, 8,000 259 ms, nearly all of it the ranking sort and
  needing no Migemo to get there. That reaches the warning at ~6,000 rows and
  `LowLevelHooksTimeout` at ~9,000. After moving the work off the callback, the
  callback is **0.05 ms whether the window holds 250 candidates or 8,000**, and the
  dictionary read goes off it too, so both warnings go. Worst single query, found
  by brute-forcing 400 three-letter romaji against the real 258-entry clipboard
  history: `shi`, whose Migemo regex is 1,772 characters — hit 52 ms + rank 18 ms.
  `_UnionMatch.spans` runs base *and* Migemo unconditionally where `hit` short-
  circuits, so `rank` pays the finditer a second time; not fixed.

- **The chooser could not be resized or moved on Windows**, and it was PuiKit's
  (puikit #133; no Keyhac-side change). `_on_mouse_down` called
  `SetCapture(self._hwnd)` — the *main* window — while also running for a secondary
  window's messages inside `_window_scope`, so a press in the chooser handed the
  capture to the console and every later move and the button-up went with it.
  Capture is what keeps a drag alive once the pointer leaves the window it started
  in, which is exactly what a frameless window's own resize edge and drag handle
  are: dragging an edge outwards leaves the window within a few pixels. macOS
  routes a drag to the window that took the mouse-down and has no capture to aim
  wrong, so this was Windows-only. Measured with real `SendInput`, before → after:
  a 90 px corner drag resized by nothing → 1224x780 to 1324x880, and a 90 px handle
  drag moved (0, 0) → (90, 90). **The diagnostic trap worth remembering**: posting
  the messages by hand works, because that skips the OS delivery the capture
  governs; it reproduces only with `SendInput`. `WM_MOUSELEAVE` tracking had the
  same mistake and moved with it.


## The interactive pass before a release

The genuinely-interactive checks were tracked in issue #10 and are all through
as of 2026-08-07, but they are a **standing pass, not a burnt-down backlog**:
they describe what to repeat before a release, since nothing here is covered by
the automated harnesses. The two that needed hardware this machine does not
have — a JIS keyboard, and a Japanese input method — were passed by hand on
2026-08-07 and stay on the list for the next release rather than being struck
off. What the IME entry checks **inverted on 2026-08-26**: the chooser no
longer takes keyboard focus, so its filter field no longer composes, and that
is now the thing to confirm rather than the thing to fix.

- **ActivateApplication's window rotation, on both OSes** *(new, not yet
  passed)* — `tests/test_activate.py` pins the choice of target against fake
  windows; what it cannot exercise is the ordering the real platforms report.
  Open three windows of one application in different screen positions and hold
  the key down through a full cycle: every window must be reached, in screen
  order, with no pair swapping back and forth (the failure mode the walk over
  positions exists to prevent). Then, with the application behind, one press
  must bring it forward rather than advance the rotation; quit it and one press
  must launch it. On macOS repeat with a localized application name, since
  `app=` matches what the OS reports.

- **Window geometry against Windows' own snap** *(new, not yet passed)* —
  `WinWindow.get_frame`/`set_frame` now work in the **visible** frame (DWM's
  `EXTENDED_FRAME_BOUNDS`) instead of the window rect, so `SnapWindow` and
  `MoveWindow` place a window where the OS itself would; before, every edge
  landed ~7px short at 100% DPI, twice that between two tiles.
  `tests/test_win_window.py` pins the round-trip on a window this process owns,
  and cannot judge the result by eye. Snap one window left with `SnapWindow`
  and another with Win+Left and compare the edges; tile two side by side and
  look for a seam; repeat at a display scale other than 100%, and on a window
  with no resize border (Settings, or another UWP app), where the compensation
  must come out zero rather than wrong.

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
- **The chooser filter box with a Japanese input method selected** *(rewritten
  2026-08-26; the previous version of this check asserted the opposite and
  would now fail by design)* — the chooser stopped taking keyboard focus, and
  composition follows that focus, so the filter field cannot compose and is not
  meant to. Confirm instead:

  - typing `ka` with the IME **on** puts `ka` in the field — the hook feeds it
    characters directly and the input method never sees the keys;
  - `kensaku` finds an entry containing 検索, which is how Japanese is reached
    now (`keyhac/core/migemo.py`; `pymigemo` is a hard dependency, so an engine
    that fails to load is a release blocker, not a degradation);
  - Enter chooses rather than being eaten by a composition;
  - **the target application's own composition survives.** Open the chooser
    while mid-composition in the editor underneath — that window keeps its
    focus, so its composition is still sitting there — and check that choosing
    an entry pastes somewhere sane rather than into a half-finished
    composition. This one is new with the non-activating window and has no
    equivalent in the old check.

  The reasoning behind giving composition up, and what `overlay_input="keyboard"`
  would cost to get it back, is in [design-notes.md](design-notes.md). PuiKit's
  per-window input contexts (keyhac #20, puikit PR #90) are still what the
  console relies on; they are simply no longer on the chooser's path.
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
