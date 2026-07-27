"""Keyhac 2 configuration file.

This file is copied to ~/.keyhac/config.py on first run.  Edit that copy —
Keyhac reloads it from the tray menu or the console's hook toggle.

The same file runs on Windows and macOS.  Where the OSes genuinely differ,
branch on `keymap.platform`; the two constants set up at the top (LEADER and
MOD) absorb most of it, so the samples below rarely have to.

Everything here is a working example, not pseudo-code.  Delete what you do
not want.
"""

import json

from keyhac import *

logger = getLogger("Config")


def configure(keymap):

    mac = keymap.platform == "mac"

    # ==================================================================
    # Setup
    # ==================================================================

    # --- user modifier -------------------------------------------------
    # Turn the right Alt/Option key into User0, a modifier of your own that
    # no application sees.  User0-User3 are available; a key used this way
    # is never emitted, so RAlt stops acting as Alt.
    keymap.define_modifier("RAlt", "RUser0")

    # --- the two portability constants ---------------------------------
    # LEADER: the modifier most samples below hang off.
    #   macOS   - the Fn key, which Windows does not expose to software
    #   Windows - User0, i.e. the right Alt key defined just above
    LEADER = "Fn" if mac else "User0"

    # MOD: the OS's primary shortcut modifier, so one binding can mean
    # "Cmd-C" on macOS and "Ctrl-C" on Windows.
    MOD = "Cmd" if mac else "Ctrl"

    # --- swap a key entirely (uncomment to try) ------------------------
    # replace_key runs before any key table, so the rest of the config
    # only ever sees the replacement.
    # keymap.replace_key("CapsLock", "LCtrl")

    # --- clipboard history ---------------------------------------------
    keymap.clipboard_history.max_items = 500
    keymap.clipboard_history.max_data_size = 10 * 1024 * 1024

    # ==================================================================
    # Global key table (active everywhere)
    # ==================================================================

    kt = keymap.define_keytable(focus_path_pattern="*")

    # --- key -> key ----------------------------------------------------
    # IJKL as arrow keys while LEADER is held.
    kt[f"{LEADER}-I"] = "Up"
    kt[f"{LEADER}-J"] = "Left"
    kt[f"{LEADER}-K"] = "Down"
    kt[f"{LEADER}-L"] = "Right"
    kt[f"{LEADER}-U"] = "Home"
    kt[f"{LEADER}-O"] = "End"

    # --- key -> sequence of keys ---------------------------------------
    # Select the whole line: Home, then Shift-End.
    kt[f"{LEADER}-A"] = "Home", "Shift-End"

    # --- one-shot: tap for one key, hold to modify ---------------------
    # Tapping right Shift alone types Escape; holding it still shifts.
    kt["O-RShift"] = "Escape"

    # --- key -> your own function --------------------------------------
    def hello():
        # print() and the logger both reach the console window.
        print("Hello from config.py")
        logger.info(f"platform={keymap.platform}")

    kt[f"{LEADER}-H"] = hello

    # --- typing literal text -------------------------------------------
    kt[f"{LEADER}-Semicolon"] = InputText("me@example.com")

    # --- sending keys from your own function ---------------------------
    # get_input_context() batches virtual key input; it is safe to use from
    # a worker thread as well (see the ThreadedAction sample below).
    def wrap_in_quotes():
        with keymap.get_input_context() as ctx:
            ctx.send_key(f"{MOD}-C")

    kt[f"{LEADER}-Q"] = wrap_in_quotes

    # ==================================================================
    # Clipboard
    # ==================================================================

    # --- history, in a chooser window ----------------------------------
    # Enter pastes; Shift-Enter only sets the clipboard.  Type to filter.
    kt[f"{LEADER}-V"] = ShowClipboardHistory()

    # --- fixed snippets -------------------------------------------------
    # (icon, label) | (icon, label, text) | (icon, label, callable)
    kt[f"{LEADER}-Shift-V"] = ShowClipboardSnippets([
        ("📧", "me@example.com"),
        ("📮", "Mailing address", "400 Broad St, Seattle, WA 98109"),
        ("🕒", "Date", DateTimeSnippet("%Y-%m-%d")),
        ("🕒", "Timestamp", DateTimeSnippet("%Y-%m-%d %H:%M:%S")),
        ("🕒", "For filenames", DateTimeSnippet("%Y%m%d_%H%M%S")),
    ])

    # --- transform whatever is on the clipboard -------------------------
    # A tool takes the clipboard text and returns the replacement.
    def pretty_json(s):
        try:
            return json.dumps(json.loads(s), indent=4, ensure_ascii=False)
        except json.JSONDecodeError:
            logger.error("Clipboard content is not valid JSON.")
            return s

    kt[f"{LEADER}-Ctrl-V"] = ShowClipboardTools([
        ("🔄", "Quote", ShowClipboardTools.quote),
        ("🔄", "Unindent", ShowClipboardTools.unindent),
        ("🔄", "Upper case", str.upper),
        ("🔄", "Lower case", str.lower),
        ("🔄", "Half width", ShowClipboardTools.to_half_width),
        ("🔄", "Full width", ShowClipboardTools.to_full_width),
        ("🔄", "Pretty JSON", pretty_json),
    ])

    # ==================================================================
    # Windows and applications
    # ==================================================================

    # --- move the focused window ---------------------------------------
    # Nudge by 20 px...
    kt[f"{LEADER}-Shift-Left"] = MoveWindow(direction="left", distance=20)
    kt[f"{LEADER}-Shift-Right"] = MoveWindow(direction="right", distance=20)
    kt[f"{LEADER}-Shift-Up"] = MoveWindow(direction="up", distance=20)
    kt[f"{LEADER}-Shift-Down"] = MoveWindow(direction="down", distance=20)

    # ...or send it as far as it goes, stopping at other windows' edges
    # and screen edges (and hopping to the next monitor when already there).
    kt[f"{LEADER}-Ctrl-Left"] = MoveWindow(direction="left", distance=9999,
                                           window_edge=True, screen_edge=True)
    kt[f"{LEADER}-Ctrl-Right"] = MoveWindow(direction="right", distance=9999,
                                            window_edge=True, screen_edge=True)

    # --- bring an application forward -----------------------------------
    # Matches like the focus conditions below: wildcards, "|" alternation,
    # case-insensitive, ".exe" optional.
    kt[f"{LEADER}-1"] = ActivateWindow(app="code|Visual Studio Code")
    kt[f"{LEADER}-2"] = ActivateWindow(app="chrome|Google Chrome")

    # --- launch an application ------------------------------------------
    if mac:
        kt[f"{LEADER}-T"] = LaunchApplication("Terminal.app")
    else:
        kt[f"{LEADER}-T"] = LaunchApplication("wt.exe")   # Windows Terminal

    # --- inspect windows yourself ---------------------------------------
    # keymap.get_active_window() / find_window() / list_windows() return
    # portable Window objects: title, app_name, pid, class_name (Windows),
    # get_frame(), set_frame(), activate(), is_minimized(), restore().
    # They are UI-thread only - never touch one from a ThreadedAction.run().
    def describe_window():
        window = keymap.get_active_window()
        if window is None:
            logger.warning("No active window.")
            return
        x, y, w, h = window.get_frame()
        logger.info(f"{window.app_name}: \"{window.title}\" "
                    f"at ({x:.0f},{y:.0f}) {w:.0f}x{h:.0f}")
        logger.info(f"{len(keymap.list_windows())} windows open")

    kt[f"{LEADER}-W"] = describe_window

    # --- activate, or launch if it is not running ------------------------
    def activate_or_launch_editor():
        window = keymap.find_window(app="code|Visual Studio Code")
        if window:
            window.activate()
        else:
            LaunchApplication("Visual Studio Code.app" if mac else "code")()

    kt[f"{LEADER}-E"] = activate_or_launch_editor

    # ==================================================================
    # Keyboard macros
    # ==================================================================

    kt[f"{LEADER}-OpenBracket"] = ToggleRecordingKeys()    # record on/off
    kt[f"{LEADER}-CloseBracket"] = PlaybackRecordedKeys()  # replay
    # StartRecordingKeys() / StopRecordingKeys() exist too, if you would
    # rather have separate keys than a toggle.

    # ==================================================================
    # Background work: ThreadedAction
    # ==================================================================

    # Anything slow (network, subprocess, sleeping) must not run inline -
    # it would block the keyboard hook.  ThreadedAction gives you a worker
    # thread; starting() and finished() run under the engine lock, run()
    # does not.
    class TypeSlowly(ThreadedAction):
        def __init__(self, text):
            self.text = text

        def starting(self):
            logger.info(f"Typing {self.text!r}...")

        def run(self):
            import time
            for char in self.text:
                time.sleep(0.05)
                with keymap.get_input_context() as ctx:
                    ctx.send_key(f"Shift-{char}" if char.isupper() else char)
            return len(self.text)

        def finished(self, result):
            logger.info(f"Typed {result} characters.")

        def __repr__(self):
            return f"TypeSlowly({self.text!r})"

    kt[f"{LEADER}-Y"] = TypeSlowly("keyhac")

    # ==================================================================
    # Multi-stroke key tables
    # ==================================================================

    # Press LEADER-X, then a second key.  A balloon shows the table's name
    # while it is armed.
    kt_x = keymap.define_keytable(name="LEADER-X")
    kt[f"{LEADER}-X"] = kt_x
    kt_x["C"] = f"{MOD}-C"
    kt_x["V"] = f"{MOD}-V"
    kt_x["S"] = f"{MOD}-S"

    # --- balloon messages of your own ------------------------------------
    def show_balloon():
        # Absent when running with --no-ui, so ask before using it.
        pop = getattr(keymap, "pop_balloon", None)
        if pop:
            pop("hello", "Keyhac is running", 2.0)

    kt[f"{LEADER}-B"] = show_balloon

    # ==================================================================
    # Application-specific key tables
    # ==================================================================
    # Tables are merged in definition order and later ones win, so anything
    # below overrides the global table for the apps it matches.

    # --- by application name (portable) ----------------------------------
    kt_browser = keymap.define_keytable(app="chrome|Google Chrome|firefox|Safari")
    kt_browser[f"{LEADER}-R"] = f"{MOD}-R"          # reload

    # --- by window title -------------------------------------------------
    # kt_docs = keymap.define_keytable(title="*Google Docs*")

    # --- by Win32 window class (Windows only) -----------------------------
    if not mac:
        kt_notepad = keymap.define_keytable(app="notepad", class_name="Edit")
        kt_notepad[f"{LEADER}-D"] = "Home", "Shift-Down", "Shift-End", "Delete"

    # --- by focus path ----------------------------------------------------
    # The focus path is the control hierarchy down to the focused element -
    # the AX tree on macOS, the UI Automation tree on Windows.  Watch the
    # console's "Focus path" field to see the live value, and use "*" freely
    # to skip levels.
    #   macOS   : /AXApplication(Xcode)/AXWindow(...)/.../AXTextArea()
    #   Windows : /Application(Code)/Window(...)/.../Edit(Message input)
    # Note the trailing "(*)": a component is "Role(Name)", and many controls
    # do carry a name, so "*/Edit()" would only match unnamed ones.
    kt_textarea = keymap.define_keytable(
        focus_path_pattern="*/AXTextArea(*)" if mac else "*/Edit(*)")
    kt_textarea[f"{LEADER}-Slash"] = InputText("# ")

    # --- by your own test -------------------------------------------------
    # custom_condition_func receives the portable Focus object: app_name,
    # window_title, class_name (Windows), path, and element - the focused
    # element in the OS's own vocabulary.
    def is_terminal(focus):
        if focus.app_name in ("Terminal", "iTerm2", "WindowsTerminal", "cmd",
                              "powershell", "pwsh"):
            return True
        # Element attributes differ per OS: AX names on macOS, UI Automation
        # names on Windows.  An unknown name simply reads back as None.
        element = focus.element
        if element is None:
            return False
        role = element.get_attribute_value("AXRole" if mac else "ControlType")
        return role in ("AXTextArea", "Document")

    kt_terminal = keymap.define_keytable(custom_condition_func=is_terminal)
    kt_terminal[f"{LEADER}-K"] = "Ctrl-K"     # clear, rather than "Down"

    # ==================================================================
    # More to explore
    # ==================================================================
    # keymap.focus            - the current Focus (app_name, window_title,
    #                           class_name, path, element, native)
    # keymap.replay_buffer    - the macro buffer behind the record actions
    # keymap.clipboard_history.items() / set_current(text)
    # Opening a URL or a file: import subprocess and run
    #     subprocess.Popen(["open", url] if mac else ["cmd", "/c", "start", url])
    #
    # Full reference: doc/03-config-api.md in the Keyhac 2 source tree.
