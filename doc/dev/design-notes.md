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

Both places ask "is a *lone* Win/Alt held?" of the emitted modifiers only — the user
bits are masked out first, because a user modifier is never sent (except in replay,
where the original key is reproduced and does count). `U0-Alt-V` on a chooser is the
case that showed why: the OS saw nothing but Alt going down and coming back up, so
without the mask the menu bar took the focus at the moment the popup appeared.

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
  Down steps into the list, Up off its first row steps back out, and so does
  anything addressed to the *query*: typing a character, editing it (Backspace,
  Delete) or moving its caret (Left/Right, and their modifier forms — Ctrl for
  a word, Shift to select, Cmd for the line start on macOS — which arrive under
  the same key names, so naming the bare keys covers the derivatives). The key
  is then dispatched as usual and lands in the field, so the keystroke that got
  you there is not spent on getting there. Backspace used to do *nothing* with
  the focus in the list, which reads as the window ignoring you when the query
  is on screen with a caret in it. Home and End are the exception, and belong
  to the list: a list long enough to want a first and last row wants them for
  that. They are routed through the window's own `_navigate` rather than
  answered by the `ListView`, because every move of the selection has to update
  the screen mark too — the ListView answering them itself moved the highlight
  and left the outline on the row the selection had just left. Enter
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
  window PuiKit builds for `overlay_input="mouse"` (puikit PR #126): clicks reach
  it, the application is never activated, and the target keeps its focus, caret
  and selection. `WS_EX_NOACTIVATE` already refuses both on Windows, so the flags
  are inert there. `frameless` goes with them: the panel's mask forces a title
  bar (a borderless panel cannot become key), and `frameless` is what hides it
  again and puts the content rect back to the frame rect. All of these travel
  with `activates` rather than being separately settable, so no caller can ask
  for a combination that does not work.
- **IME composition in the filter field is given up, deliberately** *(decided
  2026-08-26)*. Composition follows OS keyboard focus, so a window that does not
  take it cannot host an input method — no hook can substitute, because a hook
  sees physical keys and not what an IME would make of them. Migemo is the
  answer instead: romaji reaches Japanese candidates, and for a filter field it
  is arguably the faster route anyway (`gijiroku` against ぎじろく plus a
  conversion). That is why `pymigemo` is a hard dependency and not an extra.
  The alternative was live and was declined: PuiKit's `overlay_input="keyboard"`
  gives a window that *is* key while the app stays inactive — the Spotlight
  shape, in which an input method does work — but it is macOS-only, it means
  dropping the hook route there (a key window gets the keystrokes itself, and
  both paths at once double every character), it takes key status away from the
  window being pasted into, and it would leave the two OSes with different
  input paths. Not worth it for what Migemo already covers.
- The keystrokes arrive through the key hook instead — `Keymap.push_modal_input`
  plus `keyhac/ui/keyroute.py`. That route carries letters, digits, space and the
  named keys, and — through `InputHook.char_for_key` — every digit and
  punctuation mark the active layout produces. That last part is not a table of
  our own: a vk does not say which glyph it makes, and Keyhac's per-layout tables
  map names to codes rather than codes to glyphs, so the OS is asked instead
  (`NSEvent.eventWithCGEvent_(...).characters` on macOS, `ToUnicodeEx` on
  Windows) — the same translation it performs for a real keystroke, so it follows
  whatever layout is selected. Without it the filter field could not type `.` `/`
  `-` `_` `@` at all, which for clipboard history full of paths and URLs is most
  of what one would filter on. What the route still cannot carry is an input
  method, ever. Composition follows OS
  keyboard focus: IMM32 delivers `WM_IME_*` only to the focused HWND and
  `NSTextInputClient` serves only the key window. That is why Migemo is part of the
  default matcher rather than an option — for a localised list it is what makes the
  filter reach the rows.
- A key grab and a multi-stroke prefix are the same kind of state and are kept
  mutually exclusive: pushing a grab disarms any armed prefix.
- **Esc precedence.** `on_key_event` normally offers Esc to a running `ThreadedAction`
  before the key tables. While a grab is up it does not: Esc there means "close this
  window", and the window is what the user is looking at.

## Candidate sources

- The base class is `CandidateSource`, not `Source`. `from keyhac import *` is
  flat, and a config writes `class Branches(CandidateSource)` with no
  surrounding call to say which kind of source is meant. `ChooserPage` keeps the
  short name because it is only ever written inside `ShowCandidates([...])`,
  where the context is right there — the test is whether the name appears
  somewhere that supplies its own context, not whether the word is generic.
- **A source is a value, not a subclass** (`keyhac/core/source.py`). While the
  only way to offer a new kind of row was to override `list_items`, every new
  capability cost an action class *and a hotkey to reach it* — and the hotkey
  is the scarce resource, not the code. As values, several sources go into one
  window and one key reaches all of them, with one incremental search across
  the lot.
- Two things get named separately: **what the rows are** (`candidates()`,
  rebuilt on every invocation, because anything read from the screen is stale
  the moment it is cached) and **what choosing one does** (`on_chosen()`,
  declared once per source because rows from one source almost always do the
  same kind of thing). `Candidate.action` overrides it for a row that differs —
  which is what a unified window needs, since Enter there has to mean whatever
  *that* row means.
- **`name` is on the source, not the candidate.** The window shows each row's
  source beside it, and it already knows which source produced which row, so
  copying the name onto every candidate would store the same fact twice.
  `ChooserAction._collect` keeps that mapping for the life of one window.
- The badge is drawn only when there is more than one source; with one, every
  row would carry the same word.
- **The badge is a `row_factory` widget** (`keyhac/ui/candidate_row.py`), not a
  PuiKit addition. A trailing label is a reasonable thing for a list widget to
  offer in general, but *this* badge belongs to the unified window, and
  building it app-side is also the honest test of whether `ListView`'s
  `row_factory` is flexible enough. What the toolkit does not hand over with it
  is the eliding it applies to plain string rows, so the widget does its own —
  and elides the two texts independently, the label yielding only the width the
  badge needs, since clipping a long clipboard entry to fit a short source name
  would lose the part that makes the row legible.
- **The window always hands back a `Candidate`.** It used to unwrap a
  tuple-derived row back to its tuple, which meant the *window* decided how a
  row would be routed and a source that legitimately yields tuples had its own
  `on_chosen` skipped. Unwrapping for the pre-source `ChooserAction.on_chosen`
  is the action's business (`_chosen_legacy`).
- `ShowClipboardHistory` and its siblings are presets over
  `keyhac/core/sources.py`. Porting them was the point as much as the result:
  they are the sources that already existed, so if they had not fitted, the
  shape would have been wrong. The `CandidateSource` suffix on the source classes is not
  decoration — `ClipboardHistory` is already the name of the history *store*,
  and two public things with one name in a flat `from keyhac import *` is a
  trap.

## Pointing at what a row stands for

- Moving the selection through a source that carries screen rectangles draws
  an outline over the real control (`ChooserWindow._point_at_selection`, PuiKit
  `mark_screen`). This is discussion #112's argument for `node.highlight()`
  made concrete: a row that cannot describe itself — an icon-only control
  listed by its tooltip, or by role and position — is a row whose text does not
  settle *which* one it is, and lighting the real one up is the only
  confirmation there is.
- It is also the first consumer of `Candidate.rect`, which the controls source
  fills on every row and nothing read until now.
- Rows without a rectangle clear the mark rather than leaving a stale one: a
  clipboard entry is not anywhere on screen.
- An unchanged selection redraws nothing, the mark goes on every exit the
  window has, and a platform that cannot mark logs at debug and carries on.

## A chooser is never built inside the hook callback

- `ChooserAction.__call__` runs on the hook's stack, and the engine **passes
  the key through when handling it raises** — a deliberate rule (a broken
  config must not swallow the keyboard) that turns any failure while building
  the window into the key that opened it arriving in the user's document. When
  the failure came *after* the window existed (the dismissal watch, the
  activation) the chooser was on screen **and** the "P" of `User0-P` had
  leaked, which is exactly what was reported.
- So the callback does only the cheap, certain part — the one-at-a-time
  bookkeeping, dismissing whatever is open, reading the frame to centre on —
  and hands the rest to `keymap.call_on_main_thread`. Both backends queue that
  rather than calling back on the spot, so the window is built a millisecond
  later with nothing left on the hook's stack to fail into. With no dispatcher
  wired (a library use, or a test) it runs inline, which is what every caller
  there already had.
- A press while an open is still queued is that same press twice, and there is
  no window yet to toggle: the queued open is dropped, and for the same action
  that *is* the toggle. A different action's press supersedes it — the token in
  `_pending` is what the queued callback checks before building anything.
- The failure is now the action's to report, so the deferred body logs it. The
  loop's queue drain does not catch, and the engine's catch — the one that
  would have logged it — is no longer on the stack.
- **The other reason: the callback has a deadline.** `LowLevelKeyboardProc`'s
  documentation: the callback must finish inside `LowLevelHooksTimeout`
  (`HKCU\Control Panel\Desktop`, in ms; capped at 1000 ms since Windows 10
  1709), and past it "the hook is silently removed without being called. There
  is no way for the application to know whether the hook is removed" — the
  event that overran reaches the application regardless. Its own advice is the
  shape used here: return immediately and hand the work off. Building a
  chooser is past that budget — measured: 390 ms for this module's import of
  the chooser alone on a first press, before a source is read (84 ms for a
  small menu bar, 590 ms for a heavy window's controls). macOS disables a slow
  tap in the same spirit, but *says so* (`kCGEventTapDisabledByTimeout`), which
  is why the asymmetry below is Windows-only.
- **Not reproducible on this machine, though.** A/B against a disposable
  Notepad, hook installed and consuming: stalls of 0.4, 1.5, 2, 4 and 6
  seconds, repeated, all had the consume honoured and left the hook working
  (the control key, not consumed, typed as expected each time). `LowLevelHooks
  Timeout` is unset here. The caveat is that the keys were *injected*
  (`SendInput`); a physical key may be judged differently, and the symptom was
  reported for physical ones. So: the documented hazard, not the demonstrated
  cause.
- **Neither did Keyhac 1.x customise it.** No `LowLevelHooksTimeout`, and no
  registry write of any kind, anywhere in keyhac-win; `pyauto`'s `kbhook.cpp`
  has none either and calls the Python hook synchronously inside the hook proc
  exactly as this port does. Its answer was recovery, not prevention — 1.x's
  changelog: 「キーフックが強制解除されたことを検出し、フックを再設定するように
  した」 — which is `checkSanity`, ported here as `InputHook.check_health()`.
- **So the callback times itself** (`WinInputHook.SLOW_CALLBACK_SECONDS`, 200
  ms) and warns. The sanity check recovers *after* a silent unhook; the
  warning is the only evidence available that a key which leaked was a key
  that overran, and the only notice at all *before* the recovery. It is
  Windows-only because macOS's tap reports its own timeout.

## An element source reads `window.element`, never `window.native`

- Both element-reading sources (`MenuItemsSource`, `WindowControlsSource`) go
  from the front window to an element. `native` is *the platform's own
  object*, and that is an AX element on macOS but the **HWND wrapper** on
  Windows — which has no `children()`, no `role()` and no `menu_bar()`. Both
  sources caught the resulting `AttributeError`, logged at debug and returned
  nothing, so on Windows the Control and Menu pages were silently empty while
  the identical code worked on macOS.
- `Window.element` is the documented bridge for exactly this ("macOS already
  holds the AX element; Windows resolves the HWND through UI Automation") and
  answers an element on both. The tests missed it because the fake window
  offered `native`, i.e. the macOS shape; they now offer the Windows one
  (`native` *is* the window) and would fail on the old code.
- Same trap, same shape as reading the menu bar from `keymap.focus`: a source
  that reaches for a platform object gets a *different* platform object on the
  other OS, and every one of these has surfaced as an empty list rather than
  an error.

## Pressing what a row stands for

- One `_press(element)` for both sources, and it asks the element which
  actions it *has* before trying any. "AXPress" is not a Windows action name
  that happens to be unavailable — it is one UIA has never heard of, and the
  Windows element logs a warning for each unknown name, so probing the pair
  blind put `Unknown UI Automation action: 'AXPress'` in the console on every
  single press. An element that cannot say is still probed in order.
- **macOS says "press" in one word; UIA needs four.** `AXPress` activates a
  button, a tab, a checkbox and a disclosure triangle alike, while UIA gives
  each its own pattern — so the list is `Invoke, Select, Toggle, Expand`, in
  the order of what a click would do: run it, else select it, else flip it,
  else open it. Measured in VS Code, which has all of them: 279 rows offer
  only Invoke, 19 only Select, 26 only Expand/Collapse, 5 only Toggle. A list
  of Invoke alone therefore refused every tab in the activity bar — the
  reported symptom was `Extensions (Ctrl+Shift+X) - 1 requires update: the
  control refused to be pressed`.
- **Focus is the last resort.** Some rows offer no press pattern at all: a
  text field, and Chromium's list items (26 in VS Code). Clicking a text field
  is how the caret gets into it, so focusing one *is* pressing it; for the
  rest it is the closest honest thing, and it beats telling the user the row
  they chose does nothing. Both platforms have `set_focus()`, so the fallback
  is portable rather than a Windows special case.

## There is no Menu page on Windows

- **A menu bar is a macOS concept.** There it is an OS-level part: one per
  application, always at the top of the screen, always present, and readable
  in full *while it is closed*. That is what makes `MenuItemsSource` possible
  — every command in the application, flattened, without opening anything.
- Windows has none of those properties. A menu belongs to a window, sits at
  the top of it or nowhere at all, and is **populated when it opens**: an
  unopened `MenuItem` reports no children (measured, Notepad and tk alike).
  Reading the leaves would mean expanding each item, finding the popup (hosted
  outside the item, not under it), reading it and collapsing again — menus
  visibly flashing open, a cost per top-level item, and a modal menu loop in
  the target application while it happens. Attempting it against a tk window
  wedged the UIA call until the process was killed.
- So Keyhac does not offer a menu page there, and the shipped config builds
  the "Menus" page on macOS only. **Nothing is lost:** a menu's top-level
  items are UI elements of the window like any other, and
  `WindowControlsSource` already lists them (role `MenuItem`) — measured on a
  tk window: File, Edit, Shell, Debug, Options, Window, Help. Choosing one
  opens that menu, which is what clicking it does.
- The mechanism carrying the decision is `UIElement.menu_bar()` returning
  **None** on Windows. `MenuItemsSource` asks the platform whether there is a
  menu bar, and there the honest answer to that question is no — so the
  decision lives in the platform layer and `keyhac/core/` stays free of
  OS knowledge, rather than the source branching on the platform name.
- Discarded on the way: preferring MenuBar "Application" over the title-bar
  MenuBar "System" (a window with a menu bar has both, and the system one
  comes first in the tree, so searching for a bar found the wrong one). It
  worked, but it only bought a list of top-level names that Controls already
  had.

## Candidate pages

- **Two axes, and they are different questions.** A *page* is which set of
  sources the window is over; `@Name` is which of the sources on that page you
  meant. Conflating them is what the first two attempts did, and it is what
  made them confusing.
- **Left and Right move between pages.** Not a key shared with the query, so
  there is no second reading of what was typed and nothing to disclose. Three
  pages with the common one in the middle puts every page one keystroke away,
  which answers "how many more presses" by making the answer always one.
- **The window opens on the middle page**, not the first — otherwise the whole
  point of putting the common one in the middle is given away, since it would
  cost a press and one side of the row would be two away. It rounds *left* on
  an even count, which is what makes two pages open on the first: there is no
  middle of two, and the alternative reads as opening on the wrong one. Always
  the same page, whatever the last press left behind, or the key would mean
  different things on different presses.
- **They clamp, they do not wrap.** An edge that stops is what makes three
  pages a place you point at rather than a ring you count around, and "which
  way is shorter" is the question this arrangement retires.
- **The chevron goes where the pages stop.** An arrow at the last page offers
  a move that does not happen, and saying which end you are on is the row's
  job. It is blanked rather than removed: dropping the glyph narrows the
  widget, the field beside it grows into what it gave up, and the name jumps
  sideways every time you reach an edge. A row that moves as you page is worse
  than one with a gap in it. A click on the blank half is swallowed - paging
  would clamp anyway, but a click that visibly does nothing beats one that
  silently does nothing somewhere else.
- **Bare arrows only.** `Ctrl-Left` is a word, `Shift-Left` a selection and
  `Cmd-Left` the line start, and all three arrive under the same key name;
  taking them too would leave the field unable to do any of it. Bare Left and
  Right therefore left `_FIELD_KEYS`, and their modifier forms are let through
  beside it.
- **`@Name` narrows to one source, inside the current page.** It filters by
  the name the badge beside every row *already shows*, which is the whole
  reason it does not need explaining: the thing the sigil names is on screen
  before the sigil is typed. `@Action` on a page without that source empties
  the list, and the empty list explains itself.
- **A declaration, not an inference.** This is what the two rejected attempts
  got wrong. Completing a bare token on Tab meant the same text had two
  readings — a filter, which every keystroke displayed, and a page prefix,
  which nothing displayed — and Tab acted on the invisible one. Typing `act`
  to filter and landing in a different page is the symptom. A sigil cannot do
  that: the user typed it, so it is already on screen.
- **The old objection to a sigil no longer holds, and was half wrong anyway.**
  It was rejected because a typed prefix could not let the query survive the
  move, and because "with Migemo the query alphabet is exactly ASCII". The
  first is void — pages moved to the arrows, so nothing about `@` touches the
  query's survival. The second was overstated: the wildcard alphabet is `*`
  and `?`, and Migemo's input is romaji, so `@` is reserved by neither. What
  is true is narrower — `@` at the start of a token can be a thing someone
  wants to search for — and that collision is *visible*, unlike the one it
  replaced.
- **A lone `@` narrows to nothing and is not searched for.** Treating it as
  text empties the list for one keystroke, which reads as "no results" at
  exactly the moment the user is starting to say which source they mean.
- **`@` stays in the field as ordinary text**, rather than becoming a chip:
  Backspace then edits it like anything else, and nothing has to be built to
  draw it.
- **Tab lengthens the `@` token and opens no list.** The names are on screen
  already, and the chooser's only list is the rows — borrowing it to show
  sources would cover the badges that are the answer. So Tab commits nothing,
  opens no mode, and has nothing to dismiss; where it cannot lengthen, the
  badges are the list. It does nothing at all to a bare word, which is the
  surprise it replaced.
- **`source_of` is not `badge_of`.** With one source the badge slot belongs to
  the source itself — the menu source puts a keyboard shortcut there — so the
  two questions have different answers, and only one of them is always a
  source's name.
- **Three pages is the recommendation, and `@` is what makes it enough.** The
  config shipped eight, and it shipped eight because a page was the only way
  to narrow to one source. Give `@` that job and the pages collapse to three:
  Run, Paste, Click. More than three is a key of your own bound to another
  `ShowCandidates` — a key is the scarce thing, but it is yours to spend.
- **A page is a verb, the query is the noun, `@` is which of the things there
  you meant.** That is what picked those three words. The first names were Do,
  Paste, Screen — two verbs and a place, because page 3's rows share no single
  verb (a menu item is invoked, a control pressed, a window raised), and
  "Screen" was the only word covering all three. But the query *carries across
  pages*, and naming them for what Enter does is what gives that carrying a
  meaning: type `mail`, and Left/Right asks run it, paste it, click it. Under
  place-names the same gesture only says "look somewhere else". "Do" also
  excluded nothing — every row in the window does something — and sat directly
  above its own near-synonym, the `Action` badge. The one thing paid for is
  `OpenWindows` on the Click page: raising a window is the weakest fit of the
  three, and worth it. Both sources already describe themselves in the word —
  `WindowControlsSource` is "everything you could click in the front window",
  and `MenuItemsSource` ends "with everything else clickable".
- **`ChooserPage`, not `Scope`.** Renamed while it was free: `Scope` had never
  shipped (v2.2.3 predates the candidate window), and once `@` does the
  narrowing, "scope" actively misleads — it sounds like a filter. Long name
  for the same reason `CandidateSource` has one: `keyhac import *` is flat.

## The key-bindings source

- **The one source nothing outside Keyhac can offer.** macOS 26's Spotlight now
  does menu commands and clipboard history natively, so those two sources are
  where Keyhac competes worst on that OS; this one it cannot compete with at
  all, because the data is the engine's own.
- That comparison is **macOS-only, and half the audience is not on it.** Windows
  has no Spotlight, so the menu and clipboard sources are unambiguously worth
  having there — "Apple already does it" is a reason not to *out-engineer*
  Apple at ranking clipboard entries, not a reason to leave a Windows user
  without the feature. The same config runs on both.
- It reads `Keymap.effective_keytable()` — *the table the hook resolves
  against*, not a re-derivation. Answering "what can I press here" by walking
  the configuration again would be a second implementation of the
  merge-in-definition-order rule, and the two would drift. It also means the
  armed multi-stroke table is what shows when one is armed, which is the right
  answer to the question.
- The cheap one, too: no traversal, no other process, just a dict the engine
  keeps current. Contrast the menu source's 84–298 ms.
- **Multi-stroke prefixes are expanded to their leaves**, the same idiom the
  menu source uses for submenus: `Fn-X › A` is the sequence you would type,
  and those are exactly the bindings nobody remembers. The prefix itself is
  not a row — it is not a command.
- `D-` is stripped from a key expression because almost everything in the list
  is a key down; `U-` and `O-` stay, because those *are* the unusual thing
  about the binding.
- **Choosing a row runs it**, which is the point rather than a bonus: a
  binding you can run from a list does not need a key of its own, and running
  out of keys is what this whole window exists to fix.

## Making a slow source feel fast

- Measured first, and it moved the target: the first control is on screen in
  **16 ms and the thirtieth in 39**, out of ~370 for the lot. Raw speed was
  not the problem by then; not being able to tell an unfinished list from a
  finished one was. A query that has not matched *yet* reads as one that never
  will.
- So the search row carried a **"… n" note while a source was still
  producing** — no milliseconds saved, but the difference between "there is
  nothing" and "not yet". **It has since been removed**: the walk moved to a
  worker and a fill went from ~11.7 s to ~0.25 s, and a note describing a
  state nobody is left looking at is a thing appearing and vanishing beside
  the query for no reason. The reasoning was right for the speed it was
  written at; the speed changed. See "Streaming a source".
- **Breadth-first was tried and rejected.** It reaches the first rows sooner
  (3 ms against 16) and is worse at everything else: within the same node
  budget it found 96 controls against 152, because breadth spends the budget
  on wide shallow layers and never reaches the deep ones — and its first rows
  were status-bar noise where depth-first gives the toolbar.
- **A source is read once per window — keyed on the source object, not on the
  page.** Tabbing between pages used to re-walk, and keying on the page
  would still have walked the same menu bar twice for a `MenuItemsSource` that
  sits both in an everything-page and in a page of its own. Sharing the
  instance is how a config says "this is the same source"; building two says
  they are two, which is the right answer when they differ — two
  `SnippetsSource` with different snippets are not interchangeable.
- A **half-read source keeps its generator**, and a row read for one page
  lands in the source's own list as well as the window's, so a different page
  sharing it starts from where the first got to rather than from nothing.
- What makes keeping any of it safe is not a judgement about staleness: the
  dismissal watch closes the window the moment the front window changes, so
  nothing a source read can have gone stale while the window is up.
- The cache is the **window's, not the process's**. A reopened window is a new
  question about a screen that has had time to move.
- Background prefetching is available after all, and was not while this was
  written. It was ruled out on a rule one level too broad: the contract in
  `platform/base.py` says AX **into our own process** off the main thread
  crashes with SIGTRAP, and that had been read as "accessibility calls must
  stay on the main thread". The sources that are slow read *other* processes
  by definition. See "Streaming a source" below for what was measured. What is
  still true is the other half of the objection: walking on every focus change
  spends the cost for windows the user never asks about, so prefetching is a
  question about *when*, not about whether it is possible.

## Ranking a merged list

- **A hit has a quality, not only a yes.** Concatenating sources buries the
  small ones: a thousand clipboard entries in front of every menu command, and
  typing `save` listing every clipboard entry containing "save" before
  `File › Save`. `Match.rank(text)` is the sort key, lower first.
- **A tuple, not a number.** Weighing "starts with" against "is shorter" as
  floats means inventing constants nobody can defend; ordered fields say the
  same thing without them — bucket, then position, then length. Buckets: 0 the
  text starts with the match, 1 the match starts a word, 2 it is inside a word,
  3 it hit but cannot say where.
- **Derived from `spans()`**, so a matcher earns ranking by being able to
  localise its hit and needs no separate implementation. The empty query ranks
  everything equal, so an unfiltered window keeps the order its sources
  produced — clipboard history newest first.
- **Sorted stably**, so rows the query cannot tell apart keep source order.
- **Ranking and "appending never reorders" only appear to conflict.** What is
  held still is the *candidate*, not its index: it is found again wherever the
  new order puts it. And the window in which rows arrive is exactly the window
  in which nothing is selected — the list proposes no row while the filter
  field holds the focus — so during streaming there is usually nothing to
  hold still at all.
- `set_items` drops the viewport, so `_append` restores the offset *before*
  moving the selection. The other order leaves the selection off-screen
  whenever ranking moved it.
- The compiled `Match` is **kept on the window**. Recompiling per arriving
  slice would pay Migemo's whole cost — the regex build — dozens of times
  while one source streams.

## Streaming a source

- A source may **yield** instead of returning a list. The window drains the
  generator as it goes, so its first rows are on screen while it is still
  finding the rest.
- **Where it is drained is the source's own declaration**, `background`. False
  by default, which keeps a generator on the main thread in 2 ms slices —
  right for anything touching the UI or the keymap, and the only safe default
  for a source someone else wrote. The accessibility sources set it True.
- **The main-thread path is slow in a way that is not the walk's fault.** The
  drain rides puikit's animation tick, and a tick callback with no animation
  behind it runs at the *idle* rate — measured on the real backend, 10.0 Hz.
  So a 2 ms slice every 100 ms is a 2% duty cycle, and the cost of a source is
  not what it reads but `work / 2 ms × 100 ms`. Measured end to end, Chrome's
  menu bar: **11.7 s** to fill. This is why the earlier note here — "first row
  at 16 ms, all 89 over 344 ms" — did not match live use: it timed the
  generator, not the delivery.
- **A worker simply runs**, so the fill costs what the walk costs. Same window,
  same source, real backend: Chrome's menus **11,723 ms → 257 ms**, VS Code
  4,724 → 213, iTerm2 7,420 → 311, Chrome's controls 9,544 → 342. First row
  halves too (130 ms → ~60), because nothing waits for the first tick.
- **What makes the worker safe was measured, not assumed.** An AX read into
  another process releases the GIL while it waits: against a deliberately
  frozen application, a call sat in the worker for 1.51 s (returning
  `kAXErrorCannotComplete` on AX's own ~1.5 s messaging timeout) while the main
  thread's Python throughput did not move — 4.91M vs 5.0M iterations per half
  second. The contention also runs the right way round: a main thread busy with
  Python starves the worker (149 AX calls/s against 24,400), not the reverse,
  so a keystroke never queues behind the walk. The cost to a hook-sized piece
  of main-thread work, with the walk running flat out, is 0.19 ms median
  against 0.06 idle.
- **One worker, not several.** Six applications walked on six threads took
  948 ms against 935 ms for the same six in a row — 1.0×. The cost is the
  PyObjC marshalling in *our* process, not the wait in the other one, so the
  GIL is the ceiling and more threads only divide it. It also means the only
  way to make a walk itself faster is to ask fewer times — which is what the
  menu walk's batched read does.
- **`candidates()` is always called on the main thread**, whatever `background`
  says; only the generator it returns moves. That is where a source resolves
  what it needs from the UI — which application is frontmost, which window —
  and `WindowControlsSource` was reshaped from a generator into a function
  returning one so that its `get_active_window()` stays there.
- **Delivered in batches**, 64 rows or 30 ms, whichever comes first. Per row
  would pay `_append`'s re-rank and redraw once per row; per completion would
  give up the streaming the whole shape exists for.
- **Nothing announces the filling any more.** The "… n" note earned its place
  against an 11.7 s fill; against a quarter of a second it describes a state
  the user never sees, and appears and vanishes beside the query to do it.
  Removed with the slowness that justified it.
- **Two streams, not one.** `_collect` splits the unfinished sources by
  `background` and hands the window two generators. Merging them would put
  every row of both on whichever thread drained first.
- **A still backend drains inline.** `MemoryBackend` has no
  `call_on_main_thread` — the base raises — so there is nowhere for a worker to
  land rows. The tick drain already had this shape for the same reason.
- **Time-boxed, not counted** (the main-thread path). An accessibility call's
  cost varies by orders of magnitude between a menu item and a node inside a
  web area, so "twenty rows per tick" is a different amount of frozen keyboard
  every time and "two milliseconds" is not.
- **A list source does not stream at all.** There is nothing to gain by
  deferring rows already in hand and much to lose in making every caller wait
  for them, so `ChooserAction._collect` splits at the source level: lists go
  straight into the window, generators are left for it to drain.
- **Appending never reorders.** Rows already passing the filter keep their
  indices, so the selection and scroll position carry over — unlike a changed
  query, which resets both deliberately. Without that asymmetry a list that is
  still filling moves under the hand choosing from it. Ported from XeFM's
  `add_items`, which is the one part of its shape that transfers unchanged.
- **Abandoning is dropping the iterator.** A page switch or a close simply
  stops pulling; nothing has to be told to stop, and there is no thread to
  join. The cancellation question discussion #112 raises for the iterator
  interface answers itself once the producer is a generator.
- Opening a window **registers** the drain, it does not run it. Tests turn the
  handle themselves (`MemoryBackend.run_animation_ticks`), which is also what
  proves the rows arrive through the pump rather than from the constructor.

## The actions source

- **Listing does not import**, which is the whole reason this can exist:
  `keyhac/mcp/extensions.py`'s AST scan reads a file without executing it, so
  a module no `config.py` imports stays inert on disk and a class runs at one
  moment — when the operator picks it. Auto-registration via
  `__init_subclass__` was considered and cannot work here: registration
  happens at class-definition time, so it would see exactly the actions
  already bound to a key (which `KeyBindingsSource` already lists) and miss
  every unbound one, unless every half-written file were imported to
  enumerate it.
- **`ThreadedAction` subclasses only** — not every callable class. A class
  defining `__call__` binds to a key perfectly well and is deliberately not
  offered: the main thread services the keyboard hook *and* every PuiKit
  window, so a list whose rows might block it is a list that can freeze the
  keyboard. The vocabulary makes this confusing (there is no `Action` class,
  and "action" is otherwise a role) — recorded in
  [next-major.md](next-major.md).
- **One needing constructor arguments is listed and says so**, rather than
  hidden. An action missing from the list reads as Keyhac not seeing the file,
  which is a much worse thing to debug than a row that explains itself.
- It is the other side of `KeyBindingsSource`: one asks what the keys do, this
  asks what there is to run.

## The window-controls source

- Discussion #112's original target, and the reason the window had to stop
  taking the keyboard focus at all: a list of "what is actionable here" that
  changes what is actionable by opening is no use to anybody.
- **It streams because there is no cheap version.** Measured: a heavy
  application's tree is 3000 nodes and 460 ms, `roles=` does not reduce that
  (495 ms — the walk is the cost, filtering only changes what is reported),
  and depth trades everything away (VS Code yields 4 controls at depth 6
  against 117 at depth 40). So the walk yields, and the first controls appear
  in ~16 ms while the rest arrive over the rest of it.
- **Where the time actually goes**, over VS Code's 4000 nodes:

  | | ms |
  |---|---|
  | `AXChildren` traversal alone — the floor | 224 |
  | + one `AXRole` read per node | 332 |
  | + `describe()` on every node | 587 |

  So the walk reads the **role first** and describes only the elements the
  role says are worth reporting — 376 ms live, against 590 before, for the
  same 157 controls. `describe()` is a batched read of nine attributes and
  most of a window is groups and static text, so describing everything paid
  eight reads per node to learn one thing.
- What is left is **~150 ms above the traversal floor**, and closing it would
  mean asking for the role and the children in *one* batched call instead of
  two — a new platform primitive, for a third of what is already spent.
  `AXVisibleChildren` was tried as a cheaper traversal and is not one: the
  window answers it with nothing.
- **The `seen` set is load-bearing, not cycle paranoia.** A table's cells are
  children of their row *and* of their column, so without it every cell of
  every table is reported twice — the same finding `uitree.py` records.
  Windows returns no identity (its control view is a real tree), and two
  distinct controls must not collapse into one, so "no key" means "do not
  dedupe" rather than "the same".
- **Only named controls are offered.** An icon-only button with no label, no
  description and no tooltip cannot be typed for, so listing it adds a row
  nobody can reach. The overlay view is where those become reachable, by
  label rather than by name — which is the argument the discussion makes for
  keeping a label path alongside filtering.
- **This is what `provenance` was for, and the numbers justify it.** Of VS
  Code's 131 controls, **113 are named only by `AXDescription` and 18 by a
  real title**; Claude's 134 split 80/53/1. Without the name fallback chain
  the source would list 18 controls instead of 131. Recording *which*
  attribute answered matters because it decides what else can find the
  element: one reachable only through its tooltip cannot be found by
  `find(name=...)` in an action either.
- **The chain is not worth widening, measured.** A status-bar control reading
  `UTF-8` whose tooltip says *Select Encoding* is the obvious argument for
  preferring the tooltip, or for searching both. Neither survives the data.
  That particular tooltip is **not in the accessibility tree at all** — the
  button carries `AXDescription = "UTF-8"` and an empty `AXHelp`; VS Code
  draws its own, so nothing reachable holds the better name. And across every
  running application, the controls whose names *do* disagree fall into three
  shapes and none of them is a better name: identical text, one a truncation
  or continuation of the other (`Copy code` against `Copy code to clipboard`),
  or boilerplate that belongs to the page rather than the control — GitHub
  puts *"This path skips through empty directories"* on a path segment, and
  making it searchable would match that link on a query about empty
  directories. In a ranked merged list a false positive costs more than a
  missing alternative name. The sample is Chromium-heavy, which is not an
  accident: there `AXHelp` is the HTML `title` attribute, so duplication and
  boilerplate are what it is *for*.
- A `help`-derived name is the weakest of the three and it shows — macOS's
  window zoom button lists as *"this button also has an action to zoom"*,
  which is a tooltip sentence rather than a label. Kept, because a bad name
  beats no row, and `provenance` is what a future view can weigh it by.
- **`rect` is exercised for the first time here**, on every row. It is what
  the overlay view will draw against.

## The menu-items source

- **Measured before it was designed.** Walking a window's whole accessibility
  tree costs 460 ms on VS Code and is still truncated at 3000 nodes, of which
  only 303 have a name; `roles=` does *not* help (495 ms — the walk is the
  cost, and filtering only changes what is reported), nor does pruning content
  subtrees (433 ms). Depth is the only lever, and at depth 6 VS Code yields 4
  elements instead of 117. There is no cheap way to enumerate a heavy app's
  controls. The menu bar, by contrast, is bounded and almost entirely named:
  77–202 items in 84–298 ms. That is why menus came first.
- **It belongs in a `ChooserPage` of its own** for the same reason — a merged page
  opened on every keystroke cannot afford a quarter of a second.
- Only **leaves** are offered. A row that merely opens another menu is not a
  command, and a list of those would be a worse menu bar rather than a better
  one. Disabled items are skipped; "cannot tell" counts as enabled, so a
  platform that does not answer never silently empties the list.
- **The modifier mask was read off real menus, not a header**, because two
  bits are not what one would guess: `0x08` *clears* the otherwise implicit
  Command, and `0x10` is Fn. Terminal's Split Pane (Cmd-D) reports 0, Show
  Next Tab (Ctrl-Tab) reports `0x08|0x04`, Fill (Fn-Ctrl-F) reports 28.
- A key with no character — Home, Page Up, Tab — reports a private-use glyph
  that prints as a box, and also a **virtual key code**, which goes through
  Keyhac's own name table. The shortcut then reads in exactly the spelling a
  config would write.
- **A lone source owns the badge slot.** With several sources the window shows
  which one a row came from, because that is what a mixed list hides; with one
  there is no such question and the source annotates its own rows — the menu
  source puts the shortcut there, so picking a command twice teaches the key
  the third time.
- **Read from the active window, not from `keymap.focus`** — the same trap
  that made the dismissal watch close on a click. A `Focus` mixes its sources:
  its pid is the frontmost application, which the popup never becomes, while
  its window title and element come from the *AX-focused* application, which
  the popup **does** become. Reading the menu bar from there gets Keyhac's,
  and Keyhac is an accessory app with no menu bar — so the page came up
  empty. `get_active_window()` reads the frontmost application throughout.
- Worth knowing: **a custom-drawn application exposes almost nothing.** XeFM,
  a PuiKit app, offers 4 elements and 7 nodes — and Keyhac's own windows are
  the same. This source is for other people's applications.

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

- **A screen mark, not a window** (PuiKit `mark_screen`). It was a frameless
  topmost non-activating window with a `Label` in it — five window-style fields
  spelling out "a tooltip" — and it could be *clicked*, which for a tooltip is
  simply wrong. A mark says the intent once, comes with click-through, and
  schedules its own timeout.
- The old window sized itself with `min(70, max(14, len(text) + 4))`. That was
  a wrap width with no name, and no way for a long balloon to do anything but
  be cut short; it is `max_width` now and the text wraps.
- Placement: top-right of the main screen's work area, at the widest the mark
  could be rather than at its actual left edge — a mark sized to its text does
  not know that edge until it exists, and this keeps a short balloon a little
  further in rather than letting a long one run off.
- Used for multi-stroke help (restores a keyhac-mac FIXME) and macro record
  state.

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
  about page rather than about what is possible.
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
