# API reference

Every class and function a `config.py` can reach, generated from the
docstrings. It answers "what are the arguments of X"; for "how do I do Y",
read [Configuration](configuration.md) first — it introduces these APIs in the
order you meet them, with worked examples.

**Contents:** [Keymap](#class-keymap) · [KeyTable](#class-keytable) · [KeyCondition](#class-keycondition) · [FocusCondition](#class-focuscondition) · [InputContext](#class-inputcontext) · [Focus](#class-focus) · [KeyEvent](#class-keyevent) · [Window](#class-window) · [ThreadedAction](#class-threadedaction) · [InputText](#class-inputtext) · [LaunchApplication](#class-launchapplication) · [ActivateWindow](#class-activatewindow) · [MoveWindow](#class-movewindow) · [SnapWindow](#class-snapwindow) · [MouseMove](#class-mousemove) · [MouseButtonDown](#class-mousebuttondown) · [MouseButtonUp](#class-mousebuttonup) · [MouseButtonClick](#class-mousebuttonclick) · [MouseWheel](#class-mousewheel) · [MouseHorizontalWheel](#class-mousehorizontalwheel) · [StartRecordingKeys](#class-startrecordingkeys) · [StopRecordingKeys](#class-stoprecordingkeys) · [ToggleRecordingKeys](#class-togglerecordingkeys) · [PlaybackRecordedKeys](#class-playbackrecordedkeys) · [ClipboardHistory](#class-clipboardhistory) · [ChooserAction](#class-chooseraction) · [ShowCandidates](#class-showcandidates) · [Candidate](#class-candidate) · [CandidateSource](#class-candidatesource) · [CallableSource](#class-callablesource) · [Scope](#class-scope) · [ActionsSource](#class-actionssource) · [ClipboardHistorySource](#class-clipboardhistorysource) · [KeyBindingsSource](#class-keybindingssource) · [MenuItemsSource](#class-menuitemssource) · [SnippetsSource](#class-snippetssource) · [ClipboardToolsSource](#class-clipboardtoolssource) · [ShowClipboardHistory](#class-showclipboardhistory) · [ShowClipboardSnippets](#class-showclipboardsnippets) · [ShowClipboardTools](#class-showclipboardtools) · [DateTimeSnippet](#class-datetimesnippet) · [getLogger](#function-getlogger) · [Console](#class-console)


## <kbd>class</kbd> `Keymap`
Manages key tables and executes key action translations. 

One Keymap exists per Keyhac process.  The configuration file receives it as ``configure(keymap)``'s argument; code outside ``configure()`` reaches the same object through ``Keymap.get_instance()``. 



**Attributes:**
 
 - <b>`platform`</b>:  "windows" or "mac" - branch on this where the two OSes  genuinely differ. 
 - <b>`editor`</b>:  The editor edit_config() opens the configuration file with:  an application name or path the OS can resolve, or a callable  receiving the path.  Empty (the default) picks a platform default. 
 - <b>`replay_buffer`</b>:  The keystroke buffer behind the keyboard macro actions. 


---

#### <kbd>property</kbd> Keymap.clipboard

The OS clipboard - get_text() / set_text(), or None if unwired. 

The history's provider, exposed directly because actions that paste need to read and restore the clipboard around what they do, which is not a history operation. 

---

#### <kbd>property</kbd> Keymap.clipboard_history

The ClipboardHistory object (None while running without one, e.g. under --no-ui). 

---

#### <kbd>property</kbd> Keymap.config_path

The configuration script this run loads. 

lazydocs: ignore 

---

#### <kbd>property</kbd> Keymap.extensions_dir

``extensions/`` beside config.py: on sys.path, and re-imported on every reload. 

lazydocs: ignore 

---

#### <kbd>property</kbd> Keymap.focus

Portable snapshot of the current keyboard focus (a Focus), or None before the first key event. 

---

#### <kbd>property</kbd> Keymap.mcp_server_running

Whether the endpoint is currently listening. 

lazydocs: ignore 

---

#### <kbd>property</kbd> Keymap.ui

The action-facing UI API - see doc/action-api.md. 

Reading and driving another application's elements: finding windows, searching trees, waiting for the screen to change, filling fields. Deliberately a separate namespace from the configuration API, and deliberately method-style, so `from keyhac import *` does not acquire a dozen generic verbs that only mean something inside an action. 



---

### <kbd>method</kbd> `Keymap.call_on_main_thread`

```python
call_on_main_thread(callback) → None
```

Run a callback on the thread that owns the event loop. 

Thread-safe, and the supported way for a worker thread to reach anything main-thread-only: UI, window moves, AX writes. ThreadedAction.run() is the usual caller; finished() already arrives here, so it needs this only for work it defers further. 



**Args:**
 
 - <b>`callback`</b>:  Called with no arguments. 



**Note:**

> With no loop wired - Keyhac used as a library, or under test - the callback runs inline on the calling thread, which is what the code did everywhere before a dispatcher existed. 

---

### <kbd>method</kbd> `Keymap.configure`

```python
configure() → None
```

Load (or reload) the configuration file and rebuild the keymap. 

A configuration that fails to load leaves the previous keymap active and reports the traceback to the console. 

---

### <kbd>method</kbd> `Keymap.define_keytable`

```python
define_keytable(
    name: str = None,
    focus_path_pattern: str = None,
    custom_condition_func: Callable[[keyhac.platform.base.Focus], bool] = None,
    app: str = None,
    title: str = None,
    class_name: str = None
) → KeyTable
```

Define a key table. 

With any focus condition (focus_path_pattern / app / title / class_name / custom_condition_func) the table is added to the keymap and activates automatically whenever the condition is met.  Every matching table is active at once, merged in definition order, so a table defined later overrides exactly the keys it binds. 

With no condition the table is not added to the keymap: assign it to a key to make that key a multi-stroke prefix. 



**Args:**
 
 - <b>`name`</b>:  Name of the key table.  A multi-stroke table shows it in the  balloon while armed. 
 - <b>`focus_path_pattern`</b>:  Focus path pattern with wildcards, e.g.  "*/AXTextArea(*)".  Watch the console's "Focus path" field for  the live value. 
 - <b>`custom_condition_func`</b>:  A function receiving the current Focus and  returning whether the table applies. 
 - <b>`app`</b>:  Application name pattern - process/exe base name on Windows  (the ".exe" is optional), localized application name on macOS. 
 - <b>`title`</b>:  Window title pattern. 
 - <b>`class_name`</b>:  Win32 window class name pattern (Windows only). 



**Returns:**
 The KeyTable created. 



**Note:**

> app, title and class_name patterns are case-insensitive, take fnmatch wildcards (*, ?, []) and "|" alternation, and all the conditions given must match. 

---

### <kbd>method</kbd> `Keymap.define_modifier`

```python
define_modifier(key: str | int, mod: str | int) → None
```

Define a user modifier key. 

While defined, the key loses its original meaning entirely: a User0..User3 modifier is never emitted, so assignments hanging off it cannot collide with anything an application understands. 

A Windows key cannot be one, and the call is refused with an error in the log. Defining it does not take the key away from the OS: Keyhac consumes the key-down, so no application ever receives it and the Start menu stays shut, but anything watching the keyboard ahead of Keyhac still sees the physical key held - the Xbox Game Bar opens on Win+G either way, and it swallows that keystroke, including one Keyhac itself injected. A modifier that is invisible to applications but not to the shell is not what this promises, so it is not offered. 

Any other key may be redefined, including one that already is a modifier - ``define_modifier("RAlt", "RUser0")`` works - but prefer a key that is not one: the key stops being Alt (or Ctrl, or Shift) for everything, everywhere, and that is a large thing to give up by accident. Redefining a modifier is noted in the log. 



**Args:**
 
 - <b>`key`</b>:  Key to use as the modifier, as a key name or a virtual key  code. 
 - <b>`mod`</b>:  Modifier the key produces - "User0".."User3", or a standard  modifier such as "LCtrl" to give that modifier a second key. 

---

### <kbd>method</kbd> `Keymap.edit_config`

```python
edit_config() → None
```

Open the configuration file in a text editor. 

``keymap.editor`` chooses the editor: an application name or path the OS can resolve, or a callable receiving the config path.  Left empty, a platform default is used (Visual Studio Code / Xcode / TextEdit on macOS, Notepad on Windows).  The tray menu's "Edit Config" item calls this. 

---

### <kbd>method</kbd> `Keymap.find_window`

```python
find_window(app: str = None, title: str = None, class_name: str = None)
```

Find the first visible window matching the given patterns. 

Matching is exactly define_keytable's: case-insensitive, fnmatch wildcards, "|" alternation, ".exe" optional, and all the conditions given must match. 



**Args:**
 
 - <b>`app`</b>:  Application name pattern. 
 - <b>`title`</b>:  Window title pattern. 
 - <b>`class_name`</b>:  Win32 window class name pattern (Windows only). 



**Returns:**
 A Window, or None when nothing matches. 



**Note:**

> UI-thread only. 

---

### <kbd>method</kbd> `Keymap.get_active_window`

```python
get_active_window()
```

Get the frontmost window. 



**Returns:**
  A Window, or None when there is none (or the platform has no  window support). 



**Note:**

> UI-thread only, like everything on Window - never call it from a ThreadedAction.run(). 

---

### <kbd>method</kbd> `Keymap.get_ime_status`

```python
get_ime_status() → bool | None
```

Get whether the IME is on for whatever holds the input focus. 

There is no window argument on purpose: macOS can only ever address the current input source, so naming a window would mean two different contracts on the two OSes. 



**Returns:**
  True when the IME is on, False when it is off, or None when the  state cannot be determined - no IME is installed or reachable, or  (Windows) a TSF-only IME does not answer the IMM32 query. 



**Note:**

> UI-thread only.  "Off" is the same answer for two different situations, on both OSes: an IME that is installed and closed, and no IME in the picture at all - a plain keyboard layout on Windows, a plain layout or an input method's Roman mode on macOS. 

---

### <kbd>method</kbd> `Keymap.get_input_context`

```python
get_input_context(replay: bool = False) → InputContext
```

Get a key input context to send a batch of virtual key events. 

```python
with keymap.get_input_context() as ctx:
     ctx.send_key("Ctrl-C")
``` 



**Args:**
 
 - <b>`replay`</b>:  Re-evaluate the injected events through the keymap  (what the keyboard macro playback uses). 



**Returns:**
 An InputContext, to be used as a context manager. 

---

### <kbd>method</kbd> `Keymap.get_instance`

```python
get_instance() → Keymap
```

Get the Keymap singleton. 



**Returns:**
  The Keymap instance, or None before one has been created. 

---

### <kbd>method</kbd> `Keymap.list_windows`

```python
list_windows() → list
```

List the visible top-level windows. 



**Returns:**
  Window objects, front-most first where the OS says so. 



**Note:**

> UI-thread only. 

---

### <kbd>method</kbd> `Keymap.reload_config`

```python
reload_config() → None
```

Reload the configuration file. 

The keyhac-win name for configure(), kept because configurations and documentation refer to it.  The tray menu's "Reload Config" item calls this. 

---

### <kbd>method</kbd> `Keymap.replace_key`

```python
replace_key(src: str | int, dst: str | int) → None
```

Replace a key with a different key. 

The substitution runs before everything else, so the rest of the configuration only ever sees ``dst``. 



**Args:**
 
 - <b>`src`</b>:  Key to replace, as a key name or a virtual key code. 
 - <b>`dst`</b>:  Key it is replaced with. 

---

### <kbd>method</kbd> `Keymap.screen_frames`

```python
screen_frames() → list
```

Get the frame of every screen. 



**Returns:**
  One (x, y, w, h) tuple per screen, primary first, in the shared  top-left-origin coordinate space. 



**Note:**

> Thread-safe - callable from a ThreadedAction.run(). 

---

### <kbd>method</kbd> `Keymap.screen_work_frames`

```python
screen_work_frames() → list
```

Get the work area of every screen. 



**Returns:**
  screen_frames() minus the menu bar and Dock (macOS) or the taskbar  (Windows), in the same order. 



**Note:**

> UI-thread only - the macOS implementation is an AppKit query. 

---

### <kbd>method</kbd> `Keymap.set_ime_status`

```python
set_ime_status(on: bool) → bool
```

Turn the IME on or off for whatever holds the input focus. 



**Args:**
 
 - <b>`on`</b>:  True to turn the IME on, False to turn it off. 



**Returns:**
 Whether the requested state was actually reached - the result is read back rather than assumed, so False means the IME declined or there was none to ask. 



**Note:**

> UI-thread only, and it takes effect **at once** - unlike key output, which `InputContext` only queues for the application. Wrapping a `send_key` batch in "off ... back on" therefore does not work: the restore lands before the keys do and they are composed anyway.  Use `InputContext.send_text` for literal text, which the IME does not intercept. 
>The two OSes differ in how far "on" reaches: macOS selects a Japanese input source even from a US layout, while Windows only opens an IME that the focused window is already typing under - asking for "on" while a plain layout like en-US is active returns False rather than switching the input language, which is the user's own Win+Space to give. 
>Whether a change also affects other applications is the user's OS setting ("Let me use a different input method for each app window" on Windows, "Automatically switch to a document's input source" on macOS), not something Keyhac decides. 

---

### <kbd>method</kbd> `Keymap.window_frames`

```python
window_frames() → list
```

Get the frames of all normal on-screen windows. 



**Returns:**
  One (x, y, w, h) tuple per window, in the same coordinate space as  screen_frames(). 



**Note:**

> Thread-safe - callable from a ThreadedAction.run().  It is the geometry query to use there, since Window itself is not. 

---


## <kbd>class</kbd> `KeyTable`
Dict-like table assigning input key conditions to output actions. 

Subscript it with a key expression to bind a key.  Values may be: 


- a key expression string, or a list/tuple of them (key output) 
- any callable, including the action objects (executed on key down) 
- another KeyTable (arms that table as a multi-stroke prefix) 

``keymap.define_keytable()`` creates them. 

```python
kt["Fn-J"] = "Left"                  # key -> key
kt["Fn-N"] = "Cmd-1", "Cmd-2"        # key -> sequence
kt["Fn-A"] = some_callable           # key -> function / action object
kt["Ctrl-X"] = kt_ctrlx              # key -> multi-stroke table
``` 



**Attributes:**
 
 - <b>`name`</b>:  Name given at definition time, shown in the balloon while the  table is armed as a multi-stroke prefix. 

---


## <kbd>class</kbd> `KeyCondition`
A single key stroke condition - the parsed form of a key expression. 

Assigning to a key table parses the expression for you, so configurations rarely build one directly; ``KeyCondition.from_str()`` is the way in when they do. 



**Attributes:**
 
 - <b>`vk`</b>:  Virtual key code. 
 - <b>`mod`</b>:  Modifier bit mask. 
 - <b>`down`</b>:  True for a key-down condition, False for key-up. 
 - <b>`oneshot`</b>:  True for a one-shot ("O-") condition. 




---

### <kbd>method</kbd> `KeyCondition.from_str`

```python
from_str(s: str) → KeyCondition
```

Parse a key expression. 



**Args:**
 
 - <b>`s`</b>:  A key expression such as "Ctrl-X", "O-RCmd", "U-Fn-Space" or  the short form "C-A".  Case-insensitive. 



**Returns:**
 The KeyCondition it describes. 



**Raises:**
 
 - <b>`ValueError`</b>:  The expression names an unknown modifier or key. 

---


## <kbd>class</kbd> `FocusCondition`
Condition deciding whether a key table is active for the current focus. 

``keymap.define_keytable()`` builds one from the focus arguments it is given, so configurations do not normally construct it themselves. 

All specified conditions must match (AND).  Within `app`/`title`/ `class_name` patterns, "|" separates alternatives (OR) and fnmatch wildcards (*, ?, []) are available. 

### <kbd>method</kbd> `FocusCondition.__init__`

```python
__init__(
    focus_path_pattern: str = None,
    custom_condition_func: Callable[[keyhac.platform.base.Focus], bool] = None,
    app: str = None,
    title: str = None,
    class_name: str = None
)
```

Build a focus condition. 



**Args:**
 
 - <b>`focus_path_pattern`</b>:  Focus path pattern with wildcards. 
 - <b>`custom_condition_func`</b>:  A function receiving the current Focus and  returning whether the condition holds. 
 - <b>`app`</b>:  Application name pattern (".exe" optional on Windows). 
 - <b>`title`</b>:  Window title pattern. 
 - <b>`class_name`</b>:  Win32 window class name pattern (Windows only). 

---


## <kbd>class</kbd> `InputContext`
A context manager to send virtual key strokes. 

Key events are accumulated and sent as one batch when the context exits. Physically held modifiers are released around the batch and restored afterwards, so ``ctx.send_key("Ctrl-C")`` works even while the modifiers of the binding that triggered it are still down. 

Sending is where the batch *ends*, not where it arrives: the events are queued for the application to pick up later.  So anything the action does after the context exits - changing the IME state, activating another window - takes effect while the keys are still in flight, and lands first.  ``keymap.set_ime_status(False)`` around a ``send_key`` batch is the trap this makes: the matching restore wins the race and the keys are composed by the IME after all.  Send literal text with ``send_text()``, which the IME does not intercept, and leave IME changes standing rather than undoing them in the same action. 

``keymap.get_input_context()`` creates one.  It is safe to use from a ThreadedAction worker thread. 

```python
with keymap.get_input_context() as ctx:
     ctx.send_key("Cmd-Left")
     ctx.send_key("Cmd-Shift-Right")
``` 




---

### <kbd>method</kbd> `InputContext.send_key`

```python
send_key(s: str) → None
```

Send a key stroke from a key expression. 



**Args:**
 
 - <b>`s`</b>:  A key expression, e.g. "Cmd-Left", "D-Shift" (key down only)  or "U-Shift" (key up only).  Modifiers go out as their  left-side keys. 



**Raises:**
 
 - <b>`ValueError`</b>:  Used outside the context, or the expression names an  unknown modifier or key. 

---

### <kbd>method</kbd> `InputContext.send_key_by_vk`

```python
send_key_by_vk(vk: int, down: bool = True) → None
```

Send a key stroke by virtual key code. 



**Args:**
 
 - <b>`vk`</b>:  Virtual key code. 
 - <b>`down`</b>:  True for key down, False for key up. 



**Raises:**
 
 - <b>`ValueError`</b>:  Used outside the context. 

---

### <kbd>method</kbd> `InputContext.send_mouse_button`

```python
send_mouse_button(button: str = 'left', down: bool | None = None) → None
```

Press, release or click a mouse button. 

Held modifiers are released first and restored when the context exits, so a modifier-bound click does not turn into a modified click (keyhac-win behavior). 



**Args:**
 
 - <b>`button`</b>:  "left", "right" or "middle". 
 - <b>`down`</b>:  True to press, False to release, None to click. 



**Raises:**
 
 - <b>`ValueError`</b>:  Used outside the context, or an unknown button name. 

---

### <kbd>method</kbd> `InputContext.send_mouse_horizontal_wheel`

```python
send_mouse_horizontal_wheel(notches: float) → None
```

Turn the horizontal mouse wheel. 



**Args:**
 
 - <b>`notches`</b>:  Wheel notches; positive = right, 1.0 = one notch. 



**Raises:**
 
 - <b>`ValueError`</b>:  Used outside the context. 

---

### <kbd>method</kbd> `InputContext.send_mouse_move`

```python
send_mouse_move(dx: int, dy: int) → None
```

Move the mouse cursor by a relative offset. 

Injected as an absolute position, so pointer acceleration cannot distort the distance.  Unlike buttons and wheels, held modifiers stay held (keyhac-win behavior). 



**Args:**
 
 - <b>`dx`</b>:  Horizontal offset in pixels, positive = right. 
 - <b>`dy`</b>:  Vertical offset in pixels, positive = down. 



**Raises:**
 
 - <b>`ValueError`</b>:  Used outside the context. 

---

### <kbd>method</kbd> `InputContext.send_mouse_wheel`

```python
send_mouse_wheel(notches: float) → None
```

Turn the vertical mouse wheel. 

Held modifiers are released first, like send_mouse_button. 



**Args:**
 
 - <b>`notches`</b>:  Wheel notches; positive = away from you, 1.0 = one notch. 



**Raises:**
 
 - <b>`ValueError`</b>:  Used outside the context. 

---

### <kbd>method</kbd> `InputContext.send_text`

```python
send_text(s: str) → None
```

Type a literal string, whatever characters it holds. 

Like an unmodified send_key, held modifiers are released first (and restored when the context exits) - otherwise e.g. a physically held Fn turns the injected keystrokes into macOS system shortcuts (Fn/Globe-A opens the Dock). 



**Args:**
 
 - <b>`s`</b>:  The text to type. 



**Raises:**
 
 - <b>`ValueError`</b>:  Used outside the context. 

---


## <kbd>class</kbd> `Focus`
Portable snapshot of the current keyboard focus. 

Available as ``keymap.focus``, and passed to every ``custom_condition_func``. 



**Attributes:**
 
 - <b>`app_name`</b>:  Process/exe base name without extension (Windows), or the  localized application name (macOS). 
 - <b>`pid`</b>:  Process id of the focused application. 
 - <b>`window_title`</b>:  Title of the focused window.  On macOS it is captured  during the focus-path walk and carries the path's transliteration  of fnmatch special characters ("(" and "[" become "<", ")" and 
 - <b>`"]" become ">", and "/", "*", "?", "`</b>: " each become "-"); on Windows it is the raw title.  A ``title=`` pattern containing one of those characters must match the escaped spelling on macOS - or use a "*" wildcard across it, which works on both. 
 - <b>`class_name`</b>:  Win32 window class name (Windows only; None on macOS). 
 - <b>`path`</b>:  Focus path string - on macOS the AX focus path  ("/AXApplication(Xcode)/AXWindow(...)..."), on Windows a  synthesized "/{app_name}/{class_name}({title})" (provisional  format). 
 - <b>`element`</b>:  The focused *semantic* element - an AX UIElement (macOS) or a  UI Automation UIElement (Windows).  Same shape on both  (get_attribute_names(), get_attribute_value(), get_action_names(),  perform_action(), parent()), but each uses its own OS's  vocabulary of attribute names, "AXRole" versus "ControlType".  Portable code uses app_name / window_title / class_name and the  focus path instead. 
 - <b>`native`</b>:  The platform power object - a UIElement (macOS) or  NativeWindow, an HWND wrapper (Windows). 

---


## <kbd>class</kbd> `KeyEvent`
A normalized key event delivered by the OS hook. 



**Attributes:**
 
 - <b>`vk`</b>:  Virtual key code. 
 - <b>`down`</b>:  True for key down, False for key up. 
 - <b>`kind`</b>:  "real" for physical input (or input injected by other apps),  "replay" for input Keyhac injected in replay mode, which the  keymap re-evaluates.  Events Keyhac injects in normal (translated)  mode are filtered out by the platform layer and never arrive here. 

---


## <kbd>class</kbd> `Window`
A top-level OS window. 

The portable half of keyhac-win's pyauto.Window and keyhac-mac's AXWindow element: window *operations* unify cleanly across both OSes (find, activate, move, restore, title, process), unlike element introspection, whose attribute vocabularies do not - see Focus.element. 

``keymap.get_active_window()``, ``list_windows()`` and ``find_window()`` hand these out; configurations never construct one. 



**Note:**

> Everything on this class is UI-thread only.  On macOS these are Accessibility calls, and AX into our own process off the main thread crashes with SIGTRAP.  A ThreadedAction therefore reads windows in starting(), computes in run(), and writes back in finished(); the thread-safe queries a run() may call are keymap.screen_frames() and keymap.window_frames(). 


---

#### <kbd>property</kbd> Window.app_name

Process base name without extension (Windows) / localized application name (macOS). 

---

#### <kbd>property</kbd> Window.class_name

Win32 window class. None on macOS, which has no such concept. 

---

#### <kbd>property</kbd> Window.element

This window as an element, for searching inside it. 

The bridge from window operations to element introspection: an action finds a window portably (``keymap.find_window``) and then has to look *into* it, which until now meant reaching for a platform-specific entry point. macOS already holds the AX element; Windows resolves the HWND through UI Automation. 

---

#### <kbd>property</kbd> Window.native

The underlying platform object (HWND wrapper / AX UIElement). 

---

#### <kbd>property</kbd> Window.pid

Process id of the application owning the window. 

---

#### <kbd>property</kbd> Window.title

The window's title. 



---

### <kbd>method</kbd> `Window.activate`

```python
activate() → bool
```

Bring this window and its application to the front. 



**Returns:**
  Whether the activation succeeded. 

---

### <kbd>method</kbd> `Window.get_frame`

```python
get_frame() → tuple[float, float, float, float] | None
```

Get the window's frame. 



**Returns:**
  (x, y, w, h) in global top-left-origin screen coordinates, or None  when the window has no readable frame. 

---

### <kbd>method</kbd> `Window.is_minimized`

```python
is_minimized() → bool
```

Whether the window is currently minimized. 

---

### <kbd>method</kbd> `Window.minimize`

```python
minimize() → bool
```

Minimize the window. 



**Returns:**
  Whether the window was minimized. 

---

### <kbd>method</kbd> `Window.restore`

```python
restore() → bool
```

Un-minimize the window. 



**Returns:**
  Whether the window was restored. 

---

### <kbd>method</kbd> `Window.set_frame`

```python
set_frame(x: float, y: float, w: float = None, h: float = None) → bool
```

Move the window, and optionally resize it. 



**Args:**
 
 - <b>`x`</b>:  New left edge. 
 - <b>`y`</b>:  New top edge. 
 - <b>`w`</b>:  New width; None keeps the current one. 
 - <b>`h`</b>:  New height; None keeps the current one. 



**Returns:**
 Whether the window accepted the change. 

---


## <kbd>class</kbd> `ThreadedAction`
Base class for time-consuming key actions. 

Anything slow - network, subprocess, sleeping, heavy computation - must not run inline, because a bound function executes inside the keyboard hook's deadline.  Derive from this and implement starting(), run() and finished() instead. 

Three threads, and which one you are on decides what you may touch. starting() and finished() run on the event-loop thread under the engine lock: main-thread-only APIs (UI, windows, AX) are allowed there, and they should stay light-weight because they hold the lock the keyboard hook needs.  run() executes on a worker, where input contexts are allowed but windows and AX elements are not. 

Actions run concurrently, so a run() that takes minutes no longer holds up every other one.  What is still serialized is what has to be: injected keystrokes (one `with ctx:` batch at a time) and the clipboard save and restore around a paste. 

**The user can stop a running action with Esc**, and an action needs to write nothing for that: `wait_for` raises `ActionCancelled`, and a long action spends nearly all its time waiting.  Use `check_cancelled()` in a stretch of work that has no wait in it. 

```python
class Fetch(ThreadedAction):
     def starting(self):          # main thread, before run
         logger.info("fetching...")
     def run(self):               # worker thread - the slow part
         return do_network_call()
     def finished(self, result):  # main thread, after run
         logger.info(f"got {result}")
``` 


---

#### <kbd>property</kbd> ThreadedAction.keymap

The running Keymap, so an action need not import and look it up. 

---

#### <kbd>property</kbd> ThreadedAction.ui

The action-facing UI API (`keymap.ui`) - see doc/action-api.md. 

An action's most-used object, so it is one attribute away rather than two lines of lookup at the top of every run(). 



---

### <kbd>method</kbd> `ThreadedAction.cancelled`

```python
cancelled() → bool
```

True once the user has asked this action to stop. 

Check it in a loop that does not wait - `wait_for` already raises `ActionCancelled` on its own, and a loop built out of waits needs nothing else. 

---

### <kbd>method</kbd> `ThreadedAction.check_cancelled`

```python
check_cancelled() → None
```

Raise `ActionCancelled` if the user has asked this action to stop. 

For a stretch of work with no wait in it - a long parse, a big write - where cancellation would otherwise not be noticed until the next wait. 

---

### <kbd>method</kbd> `ThreadedAction.finished`

```python
finished(result: Any) → None
```

Called on the event-loop thread once run() has returned. 

Main-thread-only APIs are allowed here too. 



**Args:**
 
 - <b>`result`</b>:  Whatever run() returned. 

---

### <kbd>method</kbd> `ThreadedAction.run`

```python
run() → Any
```

Called in the thread pool; may block. 



**Returns:**
  Anything; it is handed to finished(). 

---

### <kbd>method</kbd> `ThreadedAction.starting`

```python
starting() → None
```

Called on the event-loop thread the moment the action triggers. 

Main-thread-only APIs (UI, windows, AX) are allowed here; it runs under the engine lock, so keep it light. 

---


## <kbd>class</kbd> `InputText`
Type a literal string into the focused application. 

### <kbd>method</kbd> `InputText.__init__`

```python
__init__(text: str)
```

Build the action. 



**Args:**
 
 - <b>`text`</b>:  The text to type; any characters, not just ones the keyboard  can produce. 

---


## <kbd>class</kbd> `LaunchApplication`
Launch (or activate) an application by name. 

### <kbd>method</kbd> `LaunchApplication.__init__`

```python
__init__(app_name: str)
```

Build the action. 



**Args:**
 
 - <b>`app_name`</b>:  Application to launch, named the way the OS resolves 
 - <b>`it`</b>:  "Terminal.app" on macOS, an executable name or path on Windows. 


---

#### <kbd>property</kbd> LaunchApplication.keymap

The running Keymap, so an action need not import and look it up. 

---

#### <kbd>property</kbd> LaunchApplication.ui

The action-facing UI API (`keymap.ui`) - see doc/action-api.md. 

An action's most-used object, so it is one attribute away rather than two lines of lookup at the top of every run(). 

---


## <kbd>class</kbd> `ActivateWindow`
Bring an application's window to the front, by name pattern. 

Where the platform enumerates windows (Windows), this raises an actual window, so it can restore a minimized one and pick the front-most match. Otherwise (macOS today) it activates the matching *application* by pid. 

```python
ActivateWindow(app="code|Visual Studio Code")
``` 

### <kbd>method</kbd> `ActivateWindow.__init__`

```python
__init__(app: str)
```

Build the action. 



**Args:**
 
 - <b>`app`</b>:  Application name pattern, matched like define_keytable's  app= - case-insensitive, fnmatch wildcards, "|" alternation,  ".exe" optional. 


---

#### <kbd>property</kbd> ActivateWindow.keymap

The running Keymap, so an action need not import and look it up. 

---

#### <kbd>property</kbd> ActivateWindow.ui

The action-facing UI API (`keymap.ui`) - see doc/action-api.md. 

An action's most-used object, so it is one attribute away rather than two lines of lookup at the top of every run(). 

---


## <kbd>class</kbd> `MoveWindow`
Move the focused window. 

It nudges the window by `distance` pixels, or - with `window_edge` / `screen_edge` - travels until it meets another window's edge or the edge of the screen.  A window already at the screen edge hops to the adjacent monitor instead. 

```python
MoveWindow(direction="left", distance=20)
MoveWindow(direction="left", distance=9999, window_edge=True)
``` 

### <kbd>method</kbd> `MoveWindow.__init__`

```python
__init__(
    x: int = None,
    y: int = None,
    direction: str = '',
    distance: float = 10,
    window_edge: bool = False,
    screen_edge: bool = True
)
```

Build the action. 



**Args:**
 
 - <b>`x`</b>:  Deprecated since keyhac-mac v1.64; use direction and distance. 
 - <b>`y`</b>:  Deprecated since keyhac-mac v1.64; use direction and distance. 
 - <b>`direction`</b>:  "left", "right", "up" or "down". 
 - <b>`distance`</b>:  How far to move, in pixels (default 10).  Pass a large  value together with window_edge / screen_edge to travel until  something stops it. 
 - <b>`window_edge`</b>:  Stop at the edges of other windows (default False). 
 - <b>`screen_edge`</b>:  Stop at the edge of the screen (default True). 


---

#### <kbd>property</kbd> MoveWindow.keymap

The running Keymap, so an action need not import and look it up. 

---

#### <kbd>property</kbd> MoveWindow.ui

The action-facing UI API (`keymap.ui`) - see doc/action-api.md. 

An action's most-used object, so it is one attribute away rather than two lines of lookup at the top of every run(). 

---


## <kbd>class</kbd> `SnapWindow`
Snap the focused window to a region of its screen (tiling). 

The region is the screen's *work area*, so the menu bar and Dock (macOS) and the taskbar (Windows) stay uncovered.  "Its screen" is the one the window overlaps most, so repeated snaps keep a window on the monitor it is already on. 

```python
SnapWindow("left")               # left half
SnapWindow("left", ratio=2/3)    # left two thirds
SnapWindow("full")
``` 

### <kbd>method</kbd> `SnapWindow.__init__`

```python
__init__(position: str, ratio: float = 0.5)
```

Build the action. 



**Args:**
 
 - <b>`position`</b>:  "left", "right", "top", "bottom" or "full". 
 - <b>`ratio`</b>:  Fraction of the work area the window covers along the snap  axis, between 0.1 and 1.0 (default 0.5 = half the screen).  Ignored for "full". 



**Raises:**
 
 - <b>`ValueError`</b>:  Unknown position, or a ratio outside [0.1, 1.0] -  reported when the configuration loads, not when the key is  pressed. 

---


## <kbd>class</kbd> `MouseMove`
Move the mouse cursor by a relative offset. 

Held modifiers stay held, unlike the button and wheel actions.  The move is injected acceleration-proof, so the distance is exactly what you ask for (keyhac-win MouseMoveCommand). 

### <kbd>method</kbd> `MouseMove.__init__`

```python
__init__(dx: int, dy: int)
```

Build the action. 



**Args:**
 
 - <b>`dx`</b>:  Horizontal offset in pixels, positive = right. 
 - <b>`dy`</b>:  Vertical offset in pixels, positive = down. 

---


## <kbd>class</kbd> `MouseButtonDown`
Press a mouse button and hold it. 

Held modifiers are released first, so a modifier-bound press does not become a modified one (keyhac-win MouseButtonDownCommand). 

### <kbd>method</kbd> `MouseButtonDown.__init__`

```python
__init__(button: str = 'left')
```

Build the action. 



**Args:**
 
 - <b>`button`</b>:  "left", "right" or "middle". 



**Raises:**
 
 - <b>`ValueError`</b>:  Unknown button name - reported when the configuration  loads, not when the key is pressed. 

---


## <kbd>class</kbd> `MouseButtonUp`
Release a held mouse button (keyhac-win MouseButtonUpCommand). 

### <kbd>method</kbd> `MouseButtonUp.__init__`

```python
__init__(button: str = 'left')
```

Build the action. 



**Args:**
 
 - <b>`button`</b>:  "left", "right" or "middle". 



**Raises:**
 
 - <b>`ValueError`</b>:  Unknown button name - reported when the configuration  loads, not when the key is pressed. 

---


## <kbd>class</kbd> `MouseButtonClick`
Click a mouse button. 

Held modifiers are released first, and rapid synthetic clicks register as double-clicks (keyhac-win MouseButtonClickCommand). 

### <kbd>method</kbd> `MouseButtonClick.__init__`

```python
__init__(button: str = 'left')
```

Build the action. 



**Args:**
 
 - <b>`button`</b>:  "left", "right" or "middle". 



**Raises:**
 
 - <b>`ValueError`</b>:  Unknown button name - reported when the configuration  loads, not when the key is pressed. 

---


## <kbd>class</kbd> `MouseWheel`
Turn the vertical mouse wheel (keyhac-win MouseWheelCommand). 

### <kbd>method</kbd> `MouseWheel.__init__`

```python
__init__(wheel: float)
```

Build the action. 



**Args:**
 
 - <b>`wheel`</b>:  Wheel notches; positive = away from you, 1.0 = one notch. 

---


## <kbd>class</kbd> `MouseHorizontalWheel`
Turn the horizontal mouse wheel (keyhac-win MouseHorizontalWheelCommand). 

### <kbd>method</kbd> `MouseHorizontalWheel.__init__`

```python
__init__(wheel: float)
```

Build the action. 



**Args:**
 
 - <b>`wheel`</b>:  Wheel notches; positive = right, 1.0 = one notch. 

---


## <kbd>class</kbd> `StartRecordingKeys`
Start recording keystrokes into the replay buffer. 

Bind it to a key; the recording is played back by PlaybackRecordedKeys. 

---


## <kbd>class</kbd> `StopRecordingKeys`
Stop recording and normalize the buffer. 

---


## <kbd>class</kbd> `ToggleRecordingKeys`
Toggle keystroke recording. 

---


## <kbd>class</kbd> `PlaybackRecordedKeys`
Play back the recorded keystrokes. 

The replayed keys run back through the keymap, so recorded bindings expand again on playback. 

---


## <kbd>class</kbd> `ClipboardHistory`
Automatically captures historical clipboard text. 

Reached from a configuration as ``keymap.clipboard_history``, and shown by the ShowClipboardHistory action. 



**Attributes:**
 
 - <b>`max_items`</b>:  Maximum entries kept (default 1000). 
 - <b>`max_label_length`</b>:  Maximum length of item labels (default 4096). 
 - <b>`max_data_size`</b>:  Maximum size of a single captured item (default 10 MB). 
 - <b>`max_persist_data_size`</b>:  Maximum size of an item written to disk  (default 64 KB). 
 - <b>`persist`</b>:  Whether the history is saved across restarts (default True;  set False to keep it in memory only). 
 - <b>`filename`</b>:  Where it is saved (default ~/.keyhac/clipboard.json). 




---

### <kbd>method</kbd> `ClipboardHistory.add_item`

```python
add_item(s: str) → None
```

Add text to the history without touching the OS clipboard. 



**Args:**
 
 - <b>`s`</b>:  The text to add.  A duplicate moves to the front; anything  larger than max_data_size is dropped. 

---

### <kbd>method</kbd> `ClipboardHistory.get_current`

```python
get_current() → str | None
```

Get the most recent clipboard text. 



**Returns:**
  The newest history entry, or None when the history is empty. 

---

### <kbd>method</kbd> `ClipboardHistory.items`

```python
items()
```

Iterate the history. 



**Yields:**
  (text, label) pairs, latest first.  The label is the text  collapsed onto one line for display. 

---

### <kbd>method</kbd> `ClipboardHistory.set_current`

```python
set_current(s: str) → None
```

Set text to the OS clipboard and the front of the history. 



**Args:**
 
 - <b>`s`</b>:  The text to put on the clipboard. 

---


## <kbd>class</kbd> `ChooserAction`
Base class for actions that open the chooser window. 

Derive from it to build your own popup: implement list_items() and on_chosen(), and inherit the whole open / filter / refocus flow.  Only one chooser is open at a time - pressing the same action's key again closes it, and a different chooser action replaces it. 

```python
class PickBranch(ChooserAction):
     def list_items(self):
         return [("🌱", name) for name in git_branches()]
     def on_chosen(self, item, modifier_flags):
         checkout(item[1])
``` 




---

### <kbd>method</kbd> `ChooserAction.list_items`

```python
list_items()
```

Build the list the chooser shows.  Override this. 



**Returns:**
  A list of (icon, label) or (icon, label, ...) tuples.  Anything  after the label is yours; on_chosen() receives the whole tuple. 

---

### <kbd>method</kbd> `ChooserAction.on_chosen`

```python
on_chosen(item, modifier_flags: int) → None
```

Handle the chosen item.  Override this. 



**Args:**
 
 - <b>`item`</b>:  The tuple list_items() produced for the chosen row. 
 - <b>`modifier_flags`</b>:  Modifiers held at selection time, as a bit mask -  the clipboard choosers read it to tell Enter from Shift-Enter. 

---


## <kbd>class</kbd> `ShowCandidates`
Open the candidate window over one or more sources. 

The hotkey is the scarce resource, not the code: an action class per kind of row means a key per kind of row, and there are only so many a person can hold.  This takes sources as *values*, so several kinds share one key and one incremental search - and each row is labelled with where it came from, so a mixed list stays readable. 

```python
kt["Fn-V"] = ShowCandidates([ClipboardHistorySource(), SnippetsSource(mine)])
kt["Fn-B"] = ShowCandidates(git_branches, on_chosen=checkout)
kt["Fn-P"] = ShowCandidates([Scope("All", every), Scope("Clipboard", clip)])
``` 

Enter runs whatever the chosen row's source says to do, so rows from different sources can mean different things in the same window - paste this, activate that, press the other. 

### <kbd>method</kbd> `ShowCandidates.__init__`

```python
__init__(sources, on_chosen=None, matcher=None, activates=None)
```

Build the action. 



**Args:**
 
 - <b>`sources`</b>:  A `CandidateSource`, a plain callable returning candidates, or a  list of either.  A callable is wrapped, so anything that can  produce a list can be a source without subclassing.  A list  of `Scope` objects instead gives the window a cycle Tab and  Shift-Tab move along, keeping the query as they go. 
 - <b>`on_chosen`</b>:  Called as `on_chosen(candidate, modifier_flags)` for  rows whose source does not say what to do itself - which is  every row when the source is a bare callable. 
 - <b>`matcher`</b>:  How the filter text is matched; the default is  case-insensitive substring unioned with Migemo. 
 - <b>`activates`</b>:  Whether the window takes OS keyboard focus.  Leave it  alone unless the filter field genuinely needs an input method 
        - see `ChooserAction.activates`. 

---


## <kbd>class</kbd> `Candidate`
One row a source offers a view. 



**Attributes:**
 
 - <b>`match_text`</b>:  What the matcher runs against.  Defaults to `display`. 
 - <b>`display`</b>:  What the user sees, which may differ from the match text -  a file candidate can match on its full path and display its  basename. 
 - <b>`payload`</b>:  What the consumer wants back: a string to paste, a `UINode`,  a callable, a window handle. 
 - <b>`identity`</b>:  Stable across invocations where the source can manage it,  so a view assigning short labels can keep giving the same  candidate the same label.  None when the source has nothing  stable to offer. 
 - <b>`icon`</b>:  A short glyph shown before the display text. 
 - <b>`rect`</b>:  Screen rectangle `(x, y, w, h)` in puikit's portable top-left  coordinates, for views that draw over the real element. 
 - <b>`provenance`</b>:  Where `display` came from, when that is not simply the  element's name - `"description"`, `"help"`, `"identifier"`,  `"position"`.  `UINode.name_source` is where an accessibility  source gets this. 
 - <b>`action`</b>:  What choosing this row does, as `action(modifier_flags)`. 
 - <b>`Usually left None`</b>:  a source declares one `on_chosen` for everything it yields, since candidates from one source almost always do the same kind of thing.  Set it per candidate for a source whose rows genuinely differ - and for the unified window, where rows from several sources sit in one list and Enter has to mean whatever *that* row means. 
 - <b>`extras`</b>:  Anything else the source and its view agree on (a key  expression, a role hint). 


---

#### <kbd>property</kbd> Candidate.label

Icon and display text as one line, the way a list view draws it. 



---

### <kbd>method</kbd> `Candidate.from_item`

```python
from_item(item) → Candidate
```

Adapt the `(icon, label, *payload)` tuple `ChooserAction.list_items` returns.  The whole tuple becomes the payload, so `on_chosen` still receives exactly what it received before. 

---


## <kbd>class</kbd> `CandidateSource`
A named set of candidates, and what choosing one does. 

Named for what it is a source *of*: `keyhac import *` is flat, and a config writes `class Branches(CandidateSource)` with no surrounding call to say which kind of source is meant.  `Scope` keeps the shorter name because it is only ever written inside `ShowCandidates([...])`, where the context is right there. 




---

### <kbd>method</kbd> `CandidateSource.badge`

```python
badge(candidate: keyhac.core.candidate.Candidate) → str
```

What to show quietly at the right of this row, when the window is showing only this source. 

With several sources the window shows which one a row came from, because that is the thing a mixed list hides.  With one there is no such question, and the slot is free for whatever *this* source thinks annotates a row - the menu source puts the keyboard shortcut there, so choosing a command from the list twice teaches the key the third time. 

---

### <kbd>method</kbd> `CandidateSource.candidates`

```python
candidates()
```

The rows this source offers *right now*.  Override this. 

Called on every invocation rather than cached: a source reading the screen - the windows that exist, the controls in the front window - is describing something that has already moved on by the time it is asked again. 

Return a list, or - for a source with real work to do - **yield**. A generator is drained a slice at a time between renders, so its first rows are on screen while it is still finding the rest, and abandoning it (the window closed, the scope changed) simply stops pulling. 

A generator runs on the **main thread**, in slices, and not on a worker.  That is not a simplification: on macOS an accessibility call off the main thread crashes the process, and accessibility is what the sources needing this are made of.  Yield often, and do not block - a slice that does not return holds the keyboard as surely as any other main-thread work would. 

---

### <kbd>method</kbd> `CandidateSource.on_chosen`

```python
on_chosen(
    candidate: keyhac.core.candidate.Candidate,
    modifier_flags: int
) → None
```

Act on the chosen row.  Override this. 



**Args:**
 
 - <b>`candidate`</b>:  The row the user picked. 
 - <b>`modifier_flags`</b>:  Modifiers held at the moment of choosing, as a  bit mask - Shift-Enter is how the clipboard sources tell  "copy this" from "paste it". 

---


## <kbd>class</kbd> `CallableSource`
A source built from a plain callable, so anything that can produce a list can be one without subclassing - SSH hosts, git branches, records out of a line-of-business system. 

```python
branches = CallableSource(git_branches, "Branches", on_chosen=checkout)
``` 

The callable returns `Candidate` objects, or the `(icon, label, *rest)` tuples `ChooserAction.list_items` has always returned - those are adapted, and `on_chosen` then receives a candidate whose payload is the tuple. 

---


## <kbd>class</kbd> `Scope`
A named set of sources the candidate window can switch between. 

One key opens the window; Tab and Shift-Tab move along the cycle, and the query survives the move - type `kensaku`, then look for it somewhere else without retyping it. That is the thing a typed prefix (`>`, `@`) cannot do, and the reason the switch is a key rather than a sigil. The other reason is that with Migemo the query alphabet is exactly ASCII, so a sigil sits in the middle of what the user is trying to type. 

Scopes are also how an *expensive* source stays affordable. A source that walks the accessibility tree costs a real traversal every time the window opens; put it in its own scope and it is paid for only when the user asks for it, instead of on every invocation of a merged everything-scope. 

```python
keymap_global["Fn-P"] = ShowCandidates([
     Scope("All", [clipboard, snippets, windows]),
     Scope("Clipboard", [clipboard, snippets]),
     Scope("Windows", [windows]),
])
``` 

### <kbd>method</kbd> `Scope.__init__`

```python
__init__(name: str, sources)
```

Build a scope. 



**Args:**
 
 - <b>`name`</b>:  Shown in the window while this scope is the current one. 
 - <b>`sources`</b>:  The sources it draws from - `CandidateSource` objects,  plain callables, or a mix. 

---


## <kbd>class</kbd> `ActionsSource`
Every action in `~/.keyhac/extensions/`, startable without a key. 

The half of the authoring loop a key binding never covered: a class lands in `extensions/`, works, and is runnable from here - the `config.py` edit that binds it to a key comes later, or never, for something used once a month.  That is also the answer to running out of keys, from the other side to `KeyBindingsSource`: one asks what the keys do, this asks what there is to run. 

**Listing does not import.** The catalogue is an AST parse (`keyhac.mcp.extensions.discover`), so a file is read and never executed to find out what is in it, and a module no `config.py` imports stays inert on disk. A class here runs at exactly one moment: when the operator picks it. 

What is offered is a `ThreadedAction` subclass, transitively and across files - not every callable class. The main thread services the keyboard hook and every window, so a list whose rows might block it is a list that can freeze the keyboard. 

A class needing constructor arguments is listed and says so rather than being hidden: an action missing from the list reads as Keyhac not seeing the file, which is a much worse thing to debug than a row that explains itself. 

### <kbd>method</kbd> `ActionsSource.__init__`

```python
__init__(name: str = None)
```

Build the source. 



**Args:**
 
 - <b>`name`</b>:  What a shared window shows beside these rows. 

---


## <kbd>class</kbd> `ClipboardHistorySource`
Everything the clipboard has held, most recent first. 

---


## <kbd>class</kbd> `KeyBindingsSource`
Every key binding in effect right now, and a way to run one. 

The one source nothing outside Keyhac can offer: it is the engine's own tables, resolved the way the hook resolves them - the tables whose focus condition matches where the user is standing, merged in definition order, or the armed multi-stroke table when there is one.  Re-deriving that from the configuration would be a second implementation of the rule, and the two would drift. 

It is also the cheap one.  There is no traversal and no other process to ask; the answer is a dict the engine already keeps up to date. 

A multi-stroke prefix is **expanded to its leaves**, the way the menu source expands submenus - `Fn-X › A` is the sequence you would type, and those are exactly the bindings nobody remembers.  Rows show what the binding does, with the keys themselves right-aligned, so the list reads as a reference: what can I press here, and what would it do. 

Choosing a row **runs it**, which is the point rather than a bonus - a binding you can run from a list is one that does not need a key of its own, and running out of keys is what the candidate window exists to fix. 

### <kbd>method</kbd> `KeyBindingsSource.__init__`

```python
__init__(name: str = None)
```

Build the source. 



**Args:**
 
 - <b>`name`</b>:  What a shared window shows beside these rows. 

---


## <kbd>class</kbd> `MenuItemsSource`
Every command in the front application's menus, as one flat list. 

This is the long tail the candidate window is for: the commands that have no keyboard shortcut, in an application whose menus you do not know by heart.  Rows read as the path to them - `File › Export › As PDF…` - and carry the shortcut where there is one, so choosing from here twice teaches the key the third time. 

Only leaves are offered.  A row that merely opens another menu is not a command, and a list of them would be a worse menu bar rather than a better one.  Disabled items are skipped: they are visible in the menu for the shape of it, and unchoosable here. 

**It costs a real traversal.**  Measured on macOS: 79 ms for a small application, 396 ms for Chrome, for 161 and 331 items - so this belongs in a `Scope` of its own, where it is paid for when asked for, rather than in a merged scope opened on every keystroke. 

### <kbd>method</kbd> `MenuItemsSource.__init__`

```python
__init__(name: str = None)
```

Build the source. 



**Args:**
 
 - <b>`name`</b>:  What a shared window shows beside these rows. 

---


## <kbd>class</kbd> `SnippetsSource`
Fixed text you paste often. 

```python
SnippetsSource([("📧", "me@example.com"), ("🕒", "Date", DateTimeSnippet("%Y-%m-%d"))])
``` 

### <kbd>method</kbd> `SnippetsSource.__init__`

```python
__init__(snippets, name: str = None)
```

Build the source. 



**Args:**
 
 - <b>`snippets`</b>:  Sequence of (icon, text), (icon, label, text) or  (icon, label, callable) tuples.  A callable is invoked when  the snippet is chosen and its return value is pasted;  returning None pastes nothing. 
 - <b>`name`</b>:  What the unified window shows beside these rows. 

---


## <kbd>class</kbd> `ClipboardToolsSource`
Transformations applied to whatever the clipboard holds now. 

### <kbd>method</kbd> `ClipboardToolsSource.__init__`

```python
__init__(tools, name: str = None)
```

Build the source. 



**Args:**
 
 - <b>`tools`</b>:  Sequence of (icon, label, callable) tuples; the callable  takes the current clipboard text and returns the replacement. 
 - <b>`name`</b>:  What the unified window shows beside these rows. 

---


## <kbd>class</kbd> `ShowClipboardHistory`
Show the clipboard history in the chooser window. 

Type to filter, Enter pastes into the application you came from, Shift-Enter only sets the clipboard, Escape cancels. 

A preset: `ShowCandidates(ClipboardHistorySource())`.  Reach for `ShowCandidates` directly to put the history in one window alongside other sources rather than on a hotkey of its own. 

---


## <kbd>class</kbd> `ShowClipboardSnippets`
Show fixed snippets in the chooser window. 

Choosing one pastes it, exactly like the clipboard history. 

```python
ShowClipboardSnippets([
     ("📧", "me@example.com"),                          # (icon, text)
     ("📮", "Mailing address", "400 Broad St, ..."),    # (icon, label, text)
     ("🕒", "Date", DateTimeSnippet("%Y-%m-%d")),       # (icon, label, callable)
])
``` 

A preset over `SnippetsSource`. 

### <kbd>method</kbd> `ShowClipboardSnippets.__init__`

```python
__init__(snippets)
```

Build the action. 



**Args:**
 
 - <b>`snippets`</b>:  Sequence of (icon, text), (icon, label, text) or  (icon, label, callable) tuples.  A callable is invoked when  the snippet is chosen and its return value is pasted;  returning None pastes nothing. 

---


## <kbd>class</kbd> `ShowClipboardTools`
Show clipboard conversion tools in the chooser window. 

Each tool takes the current clipboard text and returns its replacement. 

```python
ShowClipboardTools([
     ("🔄", "Quote", ShowClipboardTools.quote),
     ("🔄", "Upper case", str.upper),
])
``` 

A preset over `ClipboardToolsSource`. 

### <kbd>method</kbd> `ShowClipboardTools.__init__`

```python
__init__(tools)
```

Build the action. 



**Args:**
 
 - <b>`tools`</b>:  Sequence of (icon, label, callable) tuples; the callable  takes the current clipboard text and returns the replacement. 




---

### <kbd>method</kbd> `ShowClipboardTools.quote`

```python
quote(s)
```

Prefix every line with quote_mark. 



**Args:**
 
 - <b>`s`</b>:  Current clipboard text. 



**Returns:**
 The quoted text. 

---

### <kbd>method</kbd> `ShowClipboardTools.to_full_width`

```python
to_full_width(s)
```

Convert half-width characters to their full-width forms. 



**Args:**
 
 - <b>`s`</b>:  Current clipboard text. 



**Returns:**
 The converted text. 

---

### <kbd>method</kbd> `ShowClipboardTools.to_half_width`

```python
to_half_width(s)
```

Convert full-width characters to their half-width forms. 



**Args:**
 
 - <b>`s`</b>:  Current clipboard text. 



**Returns:**
 The converted text. 

---

### <kbd>method</kbd> `ShowClipboardTools.to_plain`

```python
to_plain(s)
```

Return the text unchanged (the identity converter). 



**Args:**
 
 - <b>`s`</b>:  Current clipboard text. 



**Returns:**
 The same text. 

---

### <kbd>method</kbd> `ShowClipboardTools.unindent`

```python
unindent(s)
```

Remove the common leading whitespace from every line. 



**Args:**
 
 - <b>`s`</b>:  Current clipboard text. 



**Returns:**
 The dedented text. 

---


## <kbd>class</kbd> `DateTimeSnippet`
A ShowClipboardSnippets value that produces the current date and time. 

```python
ShowClipboardSnippets([("🕒", "Date", DateTimeSnippet("%Y-%m-%d"))])
``` 

### <kbd>method</kbd> `DateTimeSnippet.__init__`

```python
__init__(fmt: str)
```

Build the snippet. 



**Args:**
 
 - <b>`fmt`</b>:  A strftime format string, e.g. "%Y-%m-%d". 

---


## <kbd>function</kbd> `getLogger`

```python
getLogger(name: str) → Logger
```

Get a logger wired to the Keyhac console. 

```python
logger = getLogger("Config")
logger.info("loaded")
``` 



**Args:**
 
 - <b>`name`</b>:  Logger name, shown in brackets on each line. 



**Returns:**
 A standard logging.Logger whose output lands in the console window (and on stderr). 

---


## <kbd>class</kbd> `Console`
The console window's backing store: a ring buffer of log lines plus the named text slots it displays ("lastKey", "focusPath"). 

A configuration reaches the console through print() and getLogger(), not through this object. 



**Attributes:**
 
 - <b>`max_lines`</b>:  How many lines the ring buffer keeps (default 1000). 




---

### <kbd>classmethod</kbd> `Console.get_instance`

```python
get_instance() → Console
```

Get the Console singleton. 



**Returns:**
  The Console instance, creating it on first use. 

---

### <kbd>method</kbd> `Console.lines`

```python
lines() → list[tuple[str, int]]
```

Get the buffered console lines. 



**Returns:**
  (text, log level) pairs, oldest first, up to max_lines of them. 

---

Generated from the docstrings by `make api-reference`. Edit the
docstrings, not this file.
