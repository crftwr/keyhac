# Platform layer

What is genuinely OS-specific, how the two OSes differ, and the interfaces that hide it.
Everything here is grounded in how keyhac-win (pyauto/`WH_KEYBOARD_LL`) and keyhac-mac
(`CGEventTap`) actually behave in production.

## The honest sync-vs-async comparison

Both hooks give Keyhac a **synchronous consume decision** — the callback's return value
decides whether the physical event is swallowed. The real differences are around the
callback:

| Aspect | Windows (`WH_KEYBOARD_LL`) | macOS (`CGEventTap`, session tap, defaultTap) |
|---|---|---|
| Decision | Return nonzero from hook proc → consumed | Return `None` (or null the event) → consumed |
| Delivery thread | Thread that installed the hook, during its message retrieval | Thread whose run loop holds the tap source (main) |
| Deadline | ~300 ms (`LowLevelHooksTimeout`); exceeded → **silent permanent unhook** | WindowServer timeout → tap **disabled**, but you get `kCGEventTapDisabledByTimeout` and can re-enable |
| Recovery | Nothing tells you. Detect: poll `GetAsyncKeyState` for modifier changes with no callback (keyhac-win `checkSanity`, 4 strikes → reinstall) | Timer polls `CGEvent.tapIsEnabled` + handle the disabled-tap event types → `tapEnable(true)`, reset modifier state |
| Modifier keys | Arrive as normal key down/up (VK_LSHIFT etc.) | Arrive as **`flagsChanged`** — platform layer must synthesize down/up by diffing flag state |
| Injection | `SendInput(batch)` — array injected atomically at the tail of the input queue; ordering vs. physical input is the queue order | `CGEventPost(kCGHIDEventTap)` — posted events re-enter your own tap; **ordering vs. concurrent real events is not guaranteed** |
| Injection bookkeeping | `LLKHF_INJECTED` flag + own `dwExtraInfo` signature to recognize self-injected events | Compare `eventSourceStateID` against private `CGEventSource`s (one for output, one for replay) |
| Ordering fix | keyhac-win trick: `hookCall` — inject a sentinel `vk=0` key-down so a follow-up action runs *inside* the input stream, serialized | keyhac-mac machinery: count pending virtual events; **defer real events** that arrive while virtual ones are in flight; flush deferred events when drained or after a 0.2 s watchdog |
| Repeats | Auto-repeat arrives as repeated key-downs | Same (keyDown with autorepeat flag) |
| Permissions | None (but cannot hook elevated processes' input unless Keyhac runs elevated — document, don't solve) | **Accessibility permission required** (`AXIsProcessTrustedWithOptions` with prompt); granted per app bundle |
| Keyboard layout | `GetKeyboardType(0) == 7` → JIS table | `KBGetLayoutType(LMGetKbdType())` → ANSI / JIS / ISO |

Consequences for the shared engine:

1. The engine is written against *synchronous dispatch with a deadline*: user callables
   run inline only if trivially fast; anything else must be a `ThreadedAction`.
2. Modifier state is **tracked by Keyhac, not read from the OS** (both predecessors do
   this), with periodic reconciliation against reality (`GetAsyncKeyState` /
   post-restore reset) to fix stuck modifiers.
3. Self-injected events are tagged by the platform (`KeyEvent.kind` below) so the engine
   ignores its own output but *does* re-process replayed macros (keyhac-mac's
   replay-source design — adopted for both OSes; on Windows, distinguish via two
   `dwExtraInfo` signatures).

## Interfaces (`keyhac/platform/base.py`)

`keyhac/platform/base.py` is the authoritative definition (docstrings included);
summary of the surface:

- **`KeyEvent`** — `vk` (OS-native virtual key code), `down`,
  `kind` (`"real" | "own" | "replay"`).
- **`Focus`** — portable focus snapshot: `app_name`, `pid`, `window_title`,
  `class_name` (Windows), `path`, `element`, `native`. Unknown attributes forward to
  `.native` (keyhac-mac config compatibility).
- **`InputHook`** — `install(on_key, on_restored, on_mouse=None)` / `uninstall` /
  `installed`; `send(events, replay=False)` (batch of `(vk, down)`; the platform
  handles atomicity, ordering and self-event tagging); `send_text(s)`;
  `send_mouse(events, replay=False)` / `cursor_pos()`; `keyboard_layout()`
  (`"ansi" | "jis" | "iso"`); `char_for_key(vk, mod)` — the character a key
  produces on the *active* layout, asked of the OS rather than tabled
  (`NSEvent.eventWithCGEvent_(...).characters` / `ToUnicodeEx`), for the one
  caller that has to reconstruct typed text from hook events: the candidate
  window's filter field; `check_health()` (periodic-timer driven recovery).
- **`FocusProvider`** — `get_focus() -> Focus | None`.
- **`EventLoop`** — `run` / `stop` / `call_later(delay_seconds, func)`; in UI mode
  the PuiKit backend fills this role, in `--no-ui` mode a per-OS minimal loop does.
- **`ClipboardProvider`** — `get_text` / `set_text` / `poll()` (change detection;
  sequence number on Windows, changeCount on macOS).
- **`AppControl`** — `activate_pid`, `launch`, `edit_file(path, editor=None)`.
- **`Window` / `WindowProvider`** — portable window objects and
  find/enumerate/activate/geometry queries (`screen_frames`, `screen_work_frames`,
  `window_frames`); thread contract documented in
  [../configuration.md](../configuration.md#windows-screens-and-applications).
- **`ImeProvider`** — `get_status()` (tri-state: on / off / `None` = could not
  ask) and `set_status(on)`. Deliberately window-less: Windows reaches the state
  through a window handle, macOS has only "the current input source", so a window
  argument would split the contract between the two OSes. See
  [../configuration.md](../configuration.md#ime).

## Windows implementation notes (`keyhac/platform/win/`, ctypes)

Reimplements what pyauto provided, in ctypes (PuiKit's `_win32_native.py` shows the
house style for raw-ctypes Win32/COM):

- **Hook**: `SetWindowsHookExW(WH_KEYBOARD_LL, proc, hInstance, 0)`. The `HOOKPROC`
  ctypes callback object must be kept referenced for the process lifetime. Read
  `KBDLLHOOKSTRUCT` (vkCode, flags, dwExtraInfo). `CallNextHookEx` on pass-through;
  return 1 to consume. Keep the callback *minimal*: normalize → engine → return.
- **Recovery**: 100 ms timer runs `checkSanity` (port from keyhac-win
  `keyhac_keymap.py:1427`): snapshot `GetAsyncKeyState` for all modifier vks; 4
  consecutive changes without an intervening hook callback ⇒ unhook + rehook.
- **Injection**: `SendInput` with `INPUT[]` batches; `KEYEVENTF_KEYUP`,
  `KEYEVENTF_EXTENDEDKEY` where required; `dwExtraInfo` = signature A (own) /
  signature B (replay). Port keyhac-win's rules:
  - output uses **left-hand** physical modifiers (`force_LR`),
  - user modifiers are never physically emitted,
  - lone Win/Alt cancellation: inject a benign `VK_LCONTROL` tap ("poison pill") before
    modifier release / before running a callable (prevents Start menu / menu-bar focus),
  - `hook_call(func)`: inject sentinel `vk=0` down; run `func` when the sentinel arrives
    in the hook (serializes paste-after-focus-change with real input).
- **Focus/windows**: `GetForegroundWindow`/`GetFocus`-equivalent via
  `GetGUIThreadInfo`, `GetWindowTextW`, class name, exe name via
  `QueryFullProcessImageNameW`; enumeration `EnumWindows`; actions
  `SetForegroundWindow` (+ the attach-thread-input force fallback keyhac-win uses),
  `GetWindowRect`/`SetWindowPos`, `ShowWindow`, `PostMessage(WM_SYSCOMMAND, SC_CLOSE)`.
- **Clipboard**: `AddClipboardFormatListener` on a message-only window →
  `WM_CLIPBOARDUPDATE` (event-driven; keyhac-win moved to this in 1.75). Text via
  `CF_UNICODETEXT`; optionally capture `CF_HTML`/`CF_DIB` payloads for history fidelity.
- **IME**: `ImmGetDefaultIMEWnd(focused window)` → `WM_IME_CONTROL` with
  `IMC_GETOPENSTATUS`/`IMC_SETOPENSTATUS` — the route pyauto used, and the only one
  that crosses a process boundary (an `HIMC` is process-local). The window is the
  one `GetGUIThreadInfo(foreground thread).hwndFocus` names, **not** the foreground
  window: a frame and the control focused inside it hand back *different* default
  IME windows, and only the focused one carries the live state — the frame's stays
  frozen, so asking it reads a flag nothing consumes and writing to it changes
  nothing the user can see. Measured on Windows 11 with Notepad; it is what the
  first Windows pass corrected (`doc/dev/testing.md`). Both calls are further gated
  on `ImmGetProperty(hkl, IGP_CONVERSION)` being non-zero, i.e. the layout the
  focused window types under actually has an IME: IMM32 stores an open status even
  under en-US, where it composes nothing and is dropped at the next layout switch.
  `ImmIsIME()` cannot make that distinction — on that machine it answers true for
  the US layout too — and `ImmGetDescription()`/`ImmGetIMEFileName()` are empty even
  for Microsoft IME, which is a TSF text service with no IMM32 IME file. Sent with
  `SendMessageTimeout(SMTO_NORMAL | SMTO_ABORTIFHUNG, 100 ms)`, **not**
  `SendMessage`: this runs on the main thread inside the `WH_KEYBOARD_LL` callback,
  and a hung target would stall the hook past `LowLevelHooksTimeout` (300 ms) and
  get it silently unhooked. A TSF-only IME may not answer IMM32 at all, which is
  what `None` reports.
- **Caret**: `GetGUIThreadInfo().rcCaret` + `ClientToScreen` (balloon placement).
- **Mouse**: `WH_MOUSE_LL` for one-shot cancellation on click
  (observation-only; own output recognized by dwExtraInfo); `SendInput` mouse
  events for output commands, relative moves injected as absolute
  virtual-desktop positions so pointer acceleration cannot distort them.

## macOS implementation notes (`keyhac/platform/mac/`, PyObjC)

Reimplements keyhac-mac's Swift `KeyhacCore_Hook/UIElement/Clipboard` in Python.
`pyobjc-framework-Quartz` exposes `CGEventTapCreate`/`CGEventPost`;
`pyobjc-framework-ApplicationServices` exposes `AXUIElement*` (both proven live —
a few AX calls need `objc.loadBundleFunctions`-style care).

- **Tap**: `CGEventTapCreate(kCGSessionEventTap, kCGHeadInsertEventTap,
  kCGEventTapOptionDefault, mask(keyDown|keyUp|flagsChanged), callback, None)`; wrap in
  `CFMachPortCreateRunLoopSource`, add to main run loop, common modes. In the callback:
  handle `kCGEventTapDisabledByTimeout/ByUserInput` (re-enable, notify engine); map
  `flagsChanged` to synthetic down/up by diffing `CGEventFlags` per modifier.
- **Source filtering**: two private `CGEventSource`s (own / replay); compare each
  incoming event's `kCGEventSourceStateID` to classify `KeyEvent.kind` (port of
  keyhac-mac `KeyhacCore_Hook.swift:278`).
- **Ordering**: port the pending/deferred machinery verbatim — count in-flight posted
  events; buffer real events arriving meanwhile; flush on drain or 0.2 s watchdog.
  This is subtle, battle-tested code; treat keyhac-mac's Swift as the spec.
  Also port: rewriting modifier flags on passed-through events from tracked virtual
  modifier state, and the `hookRestored` → modifier reset path.
- **Permissions**: `AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})`
  gate before install; recheck when the user toggles the hook in the console.
- **Focus**: `AXUIElementCreateSystemWide()` → focused app → `AXFocusedUIElement`;
  build the focus-path string exactly as keyhac-mac (`AXRole(AXTitle)/...`, transliterate
  unsafe chars) for `focus_path_pattern` compat; expose portable `app_name` (running-app
  localized name), `pid`, `window_title` (walk to `AXWindow`, read `AXTitle`).
  Set `AXUIElementSetMessagingTimeout` small — a hung app must not stall key dispatch.
- **UIElement**: port the attribute get/set/action API (marshalling AXValue types) —
  users script window moves and app automation with it (keyhac-mac's `MoveWindow` uses
  `AXPosition`).
- **Clipboard**: poll `NSPasteboard.general.changeCount` on the ~1 s health timer tick
  (keyhac-mac polls at 30 Hz — reduce; clipboard history does not need 33 ms latency).
- **Injection**: `CGEventCreateKeyboardEvent` + `CGEventPost(kCGHIDEventTap)` with the
  proper source; set flags from tracked virtual modifiers.
- **Mouse**: output via `CGEventCreateMouseEvent` /
  `CGEventCreateScrollWheelEvent` posted from the same private sources. CG
  mouse events are inherently absolute, so relative moves accumulate onto
  `cursor_pos()` (the Windows relative-as-absolute scheme for free); motion
  while a button is held — ours, or physical via
  `CGEventSourceButtonState` — posts the button's *dragged* type;
  `kCGMouseEventClickState` escalates for rapid same-button downs so
  synthetic double-clicks register (the OS click timer only serves hardware
  events); wheels scroll 3 lines per notch (Windows default feel), exact
  value in the fixed-point delta fields. One-shot cancellation: button-down
  + scrollWheel types join the tap mask (motion deliberately not — Python
  would sit in the path of every pointer movement); mouse events are never
  consumed and never enter the key deferral queue, and own output is
  recognized by event source, mirroring the WH_MOUSE_LL rules.

- **IME**: Text Input Sources (Carbon TIS), which PyObjC does not wrap — reached
  by ctypes against `Carbon.framework`, as `keyboard_layout()` already does.
  On/off is read off `kTISPropertyInputModeID` of the current source rather than
  its bundle id, which is what makes it IME-agnostic: Kotoeri, Google IME and ATOK
  all report `com.apple.inputmethod.Japanese*` / `.Roman`. Turning it *off* prefers
  the current method's Roman mode but falls back to
  `TISCopyCurrentASCIICapableKeyboardLayoutInputSource()`, because that Roman mode
  is disabled on a default Japanese setup and `TISSelectInputSource` refuses a
  disabled source with OSStatus −50 (measured, not assumed). CoreFoundation is
  called through ctypes too, so ownership of the `+1` references the Copy/Create
  functions return stays explicit.

## Keycode & layout strategy

- Engine-level key names are **portable strings** (`"A"`, `"Semicolon"`, `"F13"`,
  `"Cmd"`, `"Win"`, …). `core/vk.py` owns two per-OS tables mapping name ↔ native vk,
  with US/JIS variants chosen via `InputHook.keyboard_layout()` (both predecessors
  already ship these tables; merge them: keyhac-win `str_vk_table_*`, keyhac-mac
  `keyhac_const.py` + `keyhac_key.py`).
- `KeyCondition` stores native vks at parse time (fast hook-path comparisons stay int).
- Raw codes remain expressible as `"(123)"` for unmapped keys.
- ISO layout on macOS: unsupported (as upstream keyhac-mac); a warning is logged.
  Tracked in the issue tracker.

## What is deliberately NOT platform-abstracted

- `UIElement` (macOS AX automation) and the Win32 `Window` wrapper are exposed to
  configs as **platform-native objects** (via `Focus.native`). Pretending a common
  automation API exists would be false; configs that use them are platform-branched by
  nature.
- `shell_execute(verb=...)` semantics are Windows-flavored; on macOS it degrades to
  `open` / `NSWorkspace` equivalents with documented behavior.
