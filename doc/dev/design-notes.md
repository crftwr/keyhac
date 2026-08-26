# Feature design notes

Per-feature design decisions and the subtle behaviors deliberately carried over from
the predecessors (keyhac-win 1.83 / keyhac-mac 1.68 — the frozen feature references).
Keyhac2 targets the **union** of both feature sets; the few remaining gaps are
tracked as GitHub issues (migemo matching, cron/periodic API, themes/fonts, i18n,
rich clipboard formats).

## Deliberate ports of subtle behaviors — do not "simplify" these

- `KeyCondition` hashes by vk only, with an L/R-agnostic `__eq__` — the dict-lookup
  trick behind side-agnostic matching.
- Output resolves modifiers to **left-side** physical keys (`force_LR`).
- User modifiers are never physically emitted (except during replay). The Win keys
  are refused as user modifiers rather than made an exception to this — see below.
- An unmatched key-down leaving multi-stroke mode is still consumed (no stray
  keystroke leaks into the app).
- Errors in user callables pass the key through — typing keeps working on a broken
  config.
- A lone held Win/Alt is fenced with a Ctrl tap on Windows, so the OS does not read
  it as a tap on the key itself — see below.
- Three upstream keyhac-mac hook bugs are intentionally *fixed* in the mac hook port —
  see the module docstring of `keyhac/platform/mac/hook.py`. The third (fresh real
  input overtaking re-posted deferred reals during the flush window) was found by the
  ordering verification in [testing.md](testing.md).

## Why a Windows key cannot be a user modifier

`define_modifier` refuses `LWin` / `RWin`. The reason is that consuming a key-down
does not take the key away from everything.

The Windows sample configuration nevertheless reaches User0 through the Win key, via
`replace_key("LWin", 235)` — the route keyhac-win always took, and one the refusal
does not close. That is deliberate, not an oversight: `define_modifier` promises a
modifier nothing outside Keyhac can see, and that promise it cannot keep for a Win
key; `replace_key` promises only "this key is now that key", which is true. Windows
has no other key every keyboard can spare, so the alternative was no sample user
modifier at all. What the operator has to know is in the template's own comment: an
action under that modifier must not begin by typing `g`, and Win+L still locks.

Returning 1 from `WH_KEYBOARD_LL` keeps the Win key-down out of every message queue,
and that much works: no application receives it, and the Start menu does not open on
a tap (the shell decides that on the key-up, which is consumed too). Two things
survive it anyway.

**Win+L locks the screen.** Windows reserves that combination, like Ctrl+Alt+Del,
and resolves it before the hook chain runs, so the physical Win key is tracked
somewhere below where any program can reach. Nothing Keyhac does — consuming,
renaming through `replace_key` — changes it.

**Win+G opens the Game Bar, and it eats the keystroke.** Live findings, with
`define_modifier("LWin", "LUser0")` in effect:

- an unbound `LWin-G` opened the Game Bar, although Keyhac emits no Win key at all
  and the binding could not match (`LWin-G` parses to `MODKEY_WIN_L`, and the key
  now produces `MODKEY_USER0_L`);
- `U0-G` bound to an action typing "git status" typed **"it status"** — the injected
  `g` was swallowed as Win+G. The other letters survived: `Win+I`, `Win+T`, `Win+S`
  and `Win+A` are shell hotkeys, and those go through the path that *did* honour the
  consume.

None of this is new. All of it reproduces on keyhac-win 1.83 - the released build,
run portable with its stock configuration, on the same machine, with the Win keys
retired by `replaceKey`/`defineModifier` exactly as that configuration ships them.
Win+L locks; Win+G opens the Game Bar; and a `U0-G` bound to
`InputKeyCommand("G","I","T",...)` types "it status" there too. The same command
under a real Alt (`A-G`) keeps its `g`, which places the loss on the Win key being
held rather than on anything about the head of an injected batch. So this is not a
Keyhac2 regression; the Win key has never been fully retirable, and the sample
configuration inherits the trade rather than introducing it.

By what route the Game Bar sees the key is **not settled**. It is not the route a
bystander would take: a probe that consumes a physical key in its own low-level hook
and watches for it on the two channels open to any process — `GetAsyncKeyState` and
a raw input sink — sees nothing on either, for a Win key and for an ordinary key
alike (`tools/`-grade scratch probe, not in the suite; the same probe with the key
passed through registers on both channels, which is what validates it). So a hook
installed *after* Keyhac's, and therefore called before it, remains the most likely
explanation, and the reserved-combination path that Win+L proves exists is the other
candidate. Restarting Keyhac (making its hook the most recent) would separate them
and has not been tried.

Two dead ends, both measured:

- keyhac-win's idiom — `replaceKey("LWin", 235)` then `defineModifier(235, "User0")`,
  which is how its sample configuration reached User0 — changes nothing. It renames
  the key inside Keyhac; the physical event is identical.
- Injecting a Win key-up at the head of each batch *does* fix it, and does not open
  the Start menu. It was implemented and then dropped: it makes a user modifier emit
  an unmatched key-up, which is a hole in the invariant above, and it leaves the OS
  believing the key is released while the user is still holding it. A modifier that
  is invisible to applications but not to the shell is not what `define_modifier`
  promises, so the key is refused instead of patched around.

keyhac-win refused *every* key that already was a modifier. Keyhac2 refuses only the
Win keys: the rest of that rule would break `define_modifier("RAlt", "RUser0")`,
which migrated keyhac-mac configurations use. Redefining a modifier is reported at
INFO rather than refused — legitimate, but it costs that key its modifier
everywhere, so the sample no longer demonstrates the feature that way.

Related, and a separate problem — about *real* Win/Alt, not user modifiers: a held
Win or Alt whose companion key Keyhac consumed reaches the OS as a lone tap, opening
the Start menu or moving focus to the menu bar. keyhac-win cancelled that with a
`VK_LCONTROL` tap, in two places, and both are ported (Windows only; macOS has no
such behavior to cancel):

- `InputContext.send_modifier_keys` taps Ctrl between its press and release loops
  whenever a lone Win/Alt is being released or taken — the transitions that
  reconciling modifiers around a batch produces.
- `Keymap._cancel_oneshot_win_alt` taps before a callable action runs and before
  entering a multi-stroke table, for actions that emit no key output at all.

A callable that *does* send keys therefore emits the tap three times, which is what
keyhac-win emitted too, and none of the three is safe to drop on its own:

```
SEND : D-LCtrl U-LCtrl                          <- before the callable
SEND : D-LCtrl U-LCtrl U-LAlt D-G ... D-LAlt D-LCtrl U-LCtrl
```

The last one is unconditional: the re-pressed `D-LAlt` is a fresh Alt-down as far as
the OS is concerned, so the user's own eventual release opens the menu bar unless
something marks it used. The middle one is the only chance a key-output assignment
gets — those never reach `_cancel_oneshot_win_alt`. The first is the only chance an
action that emits nothing at all gets. The redundancy is between the first two, and
only for the case where the callable happens to send keys, which cannot be known
before running it.

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
- Filtering: a pluggable `Matcher` (`keyhac/core/matcher.py`). The default is
  multi-word AND substring (keyhac-mac behavior) **unioned with Migemo**, so romaji
  finds Japanese; `WildcardMatcher` restores 1.x's `*`/`?`. Migemo only ever adds
  matches — see [Migemo](#migemo) below.
- **Two panes, one focus.** The filter field starts with it, and while it has it
  the list shows *no* selection (`ListView(allow_no_selection=True)`, puikit
  PR #126) — not the muted unfocused highlight, which still reads as a proposal.
  Down steps into the list, Up off its first row steps back out, and typing any
  character does too, so the field is never more than one keystroke away. Enter
  takes the selected row, or the top match while the field has the focus: typing
  a few letters and pressing Enter is the flow the window exists for, and making
  Enter inert there would have been a regression. A click picks a row and moves
  the focus into the list but deliberately does **not** choose it — the payload
  can be a destructive action, so choosing stays an explicit Enter.
- **Focus is marked one container at a time.** A child draws focused only if
  every container above it is focused too, and a container marks only its *own*
  direct child. The list sits inside the `Frame`, so focusing it means the page
  focuses the frame and the frame focuses the list; naming the list to the page
  marks nothing and the selection draws in the muted unfocused colour. The
  symptom was a grey highlight that turned the accent colour only while a mouse
  button was held — the press ran `focus_on_click`, which marks the frame
  correctly, and the release handler then put it back to the broken form.
- Placement: centered on the focused window, clamped to its screen; one chooser at a
  time — the same action's hotkey toggles it closed, a different chooser replaces it.
- **It closes when the user moves away from it** (`_DismissWatch` in `actions.py`).
  A chooser is transient, and nothing used to end it but Enter/Esc/the hotkey — so
  one could survive on another virtual desktop, and the hotkey then toggled closed a
  window the user could not see, which read as the chooser refusing to open. Two
  triggers: the frontmost window changed, or a click landed outside it. The first is
  one observation covering three cases — a window belongs to exactly one desktop, so
  a desktop switch necessarily changes which window is frontmost, as do an app
  switch and a window switch. It is keyed on `(pid, window title)` and deliberately
  **not** on the focus path: the macOS path runs down to the focused *element*, so
  it changes when the user Tabs between fields and would pull the chooser out from
  under them. Both triggers treat "could not read it" as "change nothing".
  **Buttons only, never the wheel.** The mouse hook fires on both, and both
  still cancel a one-shot — but macOS scrolls the window under the pointer
  without focusing it, so dismissing on a wheel turn made the chooser vanish
  whenever the user nudged a background list. Spotlight survives that; so
  does this. `InputHook`'s `on_mouse` carries `"button"` / `"wheel"` for it.
  Keyhac's **own process is also "could not read it"**: the chooser is our
  window, so the focus landing on us is the chooser's doing, not the user's —
  on macOS a click on the popup can make us the AX-focused application even
  though a borderless window cannot take key status, and the activating path
  focuses us on purpose. Without that check a click on the chooser could close
  it, and an activating chooser would have closed itself on its first tick.
  Dismissal never refocuses anyone — the user moved away on purpose.
- Polled (250 ms, only while one is open) rather than pushed: the native
  notifications differ per OS (`NSWorkspaceActiveSpaceDidChange`,
  `SetWinEventHook(EVENT_SYSTEM_FOREGROUND)`) and would be two platform-layer
  implementations, while `FocusProvider.get_focus()` already runs on every key
  down — a user typing pays it faster than the watch does.
- An armed multi-stroke prefix does **not** get the same treatment yet: it still
  survives a desktop or application switch. Same bug class, undecided.
- **It does not take OS keyboard focus** (discussion #112). It used to, which was not
  a decision anybody made — it is what a secondary PuiKit window does by default —
  and three things followed from it, all now gone: the console came to the front
  alongside it (activation is app-scoped, and macOS 26 refuses
  `activateWithOptions:` for self-activation, so there is no narrower call);
  reopening could jump to another Space, because the OS follows the app's frontmost
  window; and pasting needed a 150 ms settle delay because the target application
  was deactivated and reactivated around it. `ChooserAction.activates = True` opts
  one source back into the old behavior, with the old costs.
- **Not taking focus is not the same as not being clickable**, and on macOS being
  both takes a specific window kind. `activates=False` alone stops the window
  taking focus *when it opens*; it does not stop a **click** activating the
  application — borderless prevents a window becoming key, not the app coming
  forward. That was a real defect: clicking the chooser deactivated the window
  underneath and the paste then had nowhere to go. The window is a PuiKit
  non-activating panel that takes key status only on demand
  (`nonactivating_panel` + `becomes_key_on_demand`, puikit PR #126): clicks reach
  it, the application is never activated, and the target keeps its focus, caret
  and selection. `WS_EX_NOACTIVATE` already refuses both on Windows, so the flags
  are inert there. `frameless` goes with them: the panel's mask forces a title
  bar (a borderless panel cannot become key), and `frameless` is what hides it
  again and puts the content rect back to the frame rect. All of these travel
  with `activates` rather than being separately settable, so no caller can ask
  for a combination that does not work.
- The same PuiKit primitive without `becomes_key_on_demand` is a panel that *is*
  key while the app stays inactive — the Spotlight shape, in which an input method
  works. That is the route back to IME in the filter field if it is ever wanted;
  it would also mean dropping the hook route on macOS (a key window gets the
  keystrokes itself, and both paths at once would double every character) and
  re-checking the paste, so it is a deliberate separate decision, not a default.
- The keystrokes arrive through the key hook instead — `Keymap.push_modal_input`
  plus `keyhac/ui/keyroute.py`. That route carries letters, digits, space and the
  named keys; it cannot carry shifted punctuation (a vk does not say which glyph the
  layout produces — solvable with `ToUnicodeEx`/`UCKeyTranslate` if it is ever
  wanted) and it cannot carry an input method at all, ever. Composition follows OS
  keyboard focus: IMM32 delivers `WM_IME_*` only to the focused HWND and
  `NSTextInputClient` serves only the key window. That is why Migemo is part of the
  default matcher rather than an option — for a localised list it is what makes the
  filter reach the rows.
- A key grab and a multi-stroke prefix are the same kind of state and are kept
  mutually exclusive: pushing a grab disarms any armed prefix.
- **Esc precedence.** `on_key_event` normally offers Esc to a running `ThreadedAction`
  before the key tables. While a grab is up it does not: Esc there means "close this
  window", and the window is what the user is looking at.

## Migemo

- Engine: oguna's `pymigemo` — pure Python, BSD-3, dictionary bundled in the wheel.
  **Not a hard dependency**: absent, `keyhac/core/migemo.py` degrades and the default
  matcher is exactly `SubstringMatcher`.
- **Union, never replace.** Migemo is added on top of the caller's own matching, so
  an engine quirk can only ever *add* matches. XeFM reached this rule the hard way;
  a hard dependency would invert it and let a pymigemo bug remove matches.
- **The minimum-length gate is load-bearing.** Generating the regex costs ~1.4 s for
  a 1-character query and ~0.5 ms for seven (measured, Apple silicon). A filter field
  recompiles on every keystroke, so without the gate the window freezes on the first
  character.
- **The LP64 runtime patch.** pymigemo 0.0.1 reads a 32-bit dictionary field as
  `array('L')`, which is 8 bytes on macOS/Linux; `_Array32` swaps the binding before
  the engine is built. Inert on Windows and once upstream lands its own fix. Pin the
  version — the patch reaches into internals.
- **Wildcards bypass Migemo.** A generated regex would collide with `*`/`?`, so a
  query using them keeps exactly the wildcard semantics it asked for.

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

## IME on/off

- The two OSes model this at different depths, and the API deliberately stops at
  the shallower one. macOS has **one** level — the selected input source — so
  "on" there means *selecting* a Japanese input source, reachable even from a US
  layout. Windows has **two** — the thread's layout/TIP, and the IME's open status
  inside it — and `set_ime_status()` only drives the second. Asking for "on" while
  a plain layout like en-US is active therefore returns `False` instead of
  switching the input language.
- That is a decision, not a gap. Switching the input language is user-visible and
  as heavy as the user's own Win+Space; it is not undone by the matching "off"
  (which closes the IME and leaves the language switched), so a symmetric
  implementation would have to remember and restore the previous `HKL`. Both were
  measured on Windows 11 — the language switch does work through
  `WM_INPUTLANGCHANGEREQUEST` posted to the *focused* window, so this is a choice
  about scope rather than about what is possible.
- The asymmetry only reaches users who run an IME language **and** a non-IME one.
  On a Japanese-only setup — the classic one, where 半角/全角 opens and closes the
  only installed IME — every layout satisfies the gate, the two levels collapse
  into one, and the two OSes behave the same. `tests/test_win_ime.py` accounts for
  that configuration: its plain-layout half skips when every installed layout has
  an IME behind it.
- Two Windows-only outcomes survive any language configuration: a window that has
  no input context at all (PuiKit's own windows until a text field wants one) can
  never be turned on, and a TSF-only IME that does not answer IMM32 is the one
  path to `None`.

## Data directory and Windows portable mode

`keyhac/core/paths.py` is the single place that decides where `config.py` and the
state files beside it (`extensions/`, `clipboard.json`, `settings.json`) live.
Three ways, first match winning — `main()` resolves once and hands explicit paths
to `Keymap`, `ClipboardHistory` and `Settings`, so nothing downstream re-derives a
default:

1. `--config PATH` — state beside the named config. Existed before this module; a
   sandboxed run must not touch the real `~/.keyhac`.
2. **Portable mode (Windows)** — a `config.py` next to `Keyhac.exe` makes the
   bundle directory the data directory. Straight port of keyhac-win 1.x
   (`keyhac_main.py`: *"exeと同じ位置にある設定ファイルを優先する"*), including
   the opt-in: the file's presence is the whole switch, deleting it reverts to
   `~/.keyhac`. No macOS counterpart — an `.app` is signed and Gatekeeper
   re-validates it, so writing state inside the bundle is not an option.
3. `~/.keyhac` — the default.

- **Bundle detection is by layout, not by name.** `bundle_dir()` calls it a bundle
  when `<dir-of-sys.executable>\app\keyhac\` exists (what `windows_app/build.ps1`
  step 4 assembles). Matching on `Keyhac.exe` instead would break a renamed
  launcher, and — worse — matching on nothing at all would let a stray `config.py`
  beside a venv's `python.exe` switch a source run (`python -m keyhac`) into
  portable mode.
- **A portable data directory can be read-only** (a write-protected stick, or an
  install under Program Files whose `config.py` an admin placed). `Settings` and
  `ClipboardHistory` already log-and-continue on `OSError`; `configure()`'s
  `extensions/` creation now does too, so a read-only directory costs that
  directory and not the config load.
- **Not portable yet:** PuiKit stores the console window's frame under
  `HKCU\Software\PuiKit\FrameAutosave` (`frame_autosave_name="KeyhacConsole"`), and
  the Windows launcher writes `keyhac-error.log` to `~/.keyhac` on a bootstrap
  crash — deliberately, since the bundle directory may be read-only. Both leave a
  trace outside a portable install.

### First-run migration from keyhac-win 1.x

`keyhac/platform/win/migrate.py`. On Windows, on a first run only (no
`~/.keyhac/config.py`, a `%APPDATA%\Keyhac\config.py` present), a `MessageBoxW`
offers to copy the 1.x config across — the move
[migration-from-keyhac-win.md](../migration-from-keyhac-win.md) already prescribes
before translating. It is a prompt rather than a silent copy because the two APIs
are not interchangeable: the copied file will not load until it is translated, and
declining leaves the working stock template. Skipped in `--no-ui` runs (no message
box) and in portable mode (which has a `config.py` by definition). A message box
rather than a PuiKit dialog because this runs before the console window exists —
the same fallback `instance.py`'s already-running notice uses.
