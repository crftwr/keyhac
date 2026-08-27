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
    # Turn a key into User0, a modifier of your own that no application
    # sees.  User0-User3 are available; a key used this way is never
    # emitted, so it loses its original meaning while defined.
    #
    # Pick a key that is not a modifier already.  Naming one here does work,
    # but that key then stops being Alt (or Ctrl, or Shift) for everything,
    # everywhere - a large thing to give up by accident.
    #
    # Windows has no spare key on every keyboard, so the Windows keys are
    # retired instead: replace_key renames them to codes Windows has no
    # meaning for, and the left one becomes User0.  This is what Keyhac for
    # Windows always did, and define_modifier("LWin", ...) is refused
    # directly, because retiring a Win key does not retire it everywhere:
    #
    #   Win+L still locks the screen.  Windows reserves that combination
    #     (like Ctrl+Alt+Del) and handles it before any keyboard hook runs.
    #     Nothing running as a program can stop it.
    #   Win+G still opens the Game Bar, and swallows the keystroke that
    #     opened it - including one Keyhac injected.  So an action bound
    #     under User0 should not start by typing "g".
    #
    # The rest is gone as promised: Win+I, Win+T and their kind do not fire,
    # and no application receives the key.
    #
    # macOS needs none of this - it has Fn, which no application uses as a
    # shortcut modifier - so the samples below hang off Fn there.  To define
    # one anyway, name a key you can spare: keymap.define_modifier("F13",
    # "User0").  Not CapsLock: it reports its own release immediately, so
    # there is no "held" state to hang a modifier on.
    if not mac:
        keymap.replace_key("LWin", 235)     # 235, 255: unassigned key codes
        keymap.replace_key("RWin", 255)
        keymap.define_modifier(235, "User0")

    # --- the two portability constants ---------------------------------
    # LEADER: the modifier most samples below hang off.
    #   macOS   - the Fn key, which Windows does not expose to software
    #   Windows - User0, i.e. the left Windows key retired just above
    LEADER = "Fn" if mac else "User0"

    # MOD: the OS's primary shortcut modifier, so one binding can mean
    # "Cmd-C" on macOS and "Ctrl-C" on Windows.
    MOD = "Cmd" if mac else "Ctrl"

    # --- swap a key entirely (uncomment to try) ------------------------
    # replace_key runs before any key table, so the rest of the config
    # only ever sees the replacement - the Windows keys above are retired
    # this way.
    # keymap.replace_key("CapsLock", "LCtrl")

    # --- text editor for "Edit Config" ---------------------------------
    # The tray menu's "Edit Config" opens this file.  Unset, a default is
    # picked (VS Code / Xcode / TextEdit on macOS, Notepad on Windows).
    # Name an editor application, or set a callable taking the path:
    # keymap.editor = "CotEditor" if mac else "notepad.exe"

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
    # A one-shot key held down still works as its modifier; only a lone
    # tap-and-release fires the assignment.  Picked so a stray tap is
    # harmless (an Escape here, say, would cancel dialogs mid-typing).
    if mac:
        # The classic macOS setup: tap Left/Right Cmd alone for Eisu/Kana
        # (IME off/on) - a no-op unless a Japanese input source is
        # installed.  Held, they are still plain Cmd.  Both names work on
        # Windows too (they reach VK_IME_OFF / VK_IME_ON there), so this
        # pair is portable if you would rather bind it on both.
        kt["O-LCmd"] = "Eisu"
        kt["O-RCmd"] = "Kana"
    else:
        # Tap right Ctrl alone to open the Start-menu search; held, it is
        # still plain Ctrl.
        kt["O-RCtrl"] = "Win-S"

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
    # IME
    # ==================================================================

    # Both calls act on whatever holds the input focus, and neither takes
    # a window: macOS can only ever address the current input source, so a
    # window argument would mean two different APIs wearing one name.
    #
    # get_ime_status() is tri-state - True, False, or None for "could not
    # tell" (no IME installed, or on Windows a TSF-only IME that does not
    # answer).  Treating None as False would silently claim the IME is off.
    #
    # For plain toggling, the Eisu / Kana key names above cost nothing and
    # need no function at all; reach for these when you want the state.

    def toggle_ime():
        status = keymap.get_ime_status()
        if status is None:
            logger.warning("No IME to toggle.")
            return
        # set_ime_status() reads the state back, so False here means the
        # IME declined - not that the call failed to go out.
        if not keymap.set_ime_status(not status):
            logger.warning("The IME did not take the change.")

    kt[f"{LEADER}-Space"] = toggle_ime

    # What *not* to do with these: turn the IME off, send keys, turn it back
    # on in a finally.  It reads well and it does not work.  set_ime_status()
    # takes effect at once, while send_key() only queues its events for the
    # application to pick up later - so the restore wins the race and the
    # keys compose after all ("git status" arrives as "gいt", measured).
    # Waiting it out is worse than it looks: a key-triggered action runs on
    # the main thread inside the keyboard hook, and sleeping there past the
    # hook timeout is exactly what gets the hook silently unhooked.
    #
    # Literal text needs none of this - InputText() and ctx.send_text()
    # inject the characters themselves, so they land whatever the IME is
    # doing.  And if keys really must go out with the IME closed, close it
    # and leave it closed: the Kana / 半角全角 key is how the user puts it
    # back.

    # ==================================================================
    # Clipboard
    # ==================================================================

    # --- history, in a chooser window ----------------------------------
    # Enter pastes; Shift-Enter only sets the clipboard.  Type to filter.
    kt[f"{LEADER}-V"] = ShowClipboardHistory()

    # --- fixed snippets -------------------------------------------------
    # (icon, label) | (icon, label, text) | (icon, label, callable)
    snippets = [
        ("📧", "me@example.com"),
        ("📮", "Mailing address", "400 Broad St, Seattle, WA 98109"),
        ("🕒", "Date", DateTimeSnippet("%Y-%m-%d")),
        ("🕒", "Timestamp", DateTimeSnippet("%Y-%m-%d %H:%M:%S")),
        ("🕒", "For filenames", DateTimeSnippet("%Y%m%d_%H%M%S")),
    ]
    kt[f"{LEADER}-Shift-V"] = ShowClipboardSnippets(snippets)

    # --- transform whatever is on the clipboard -------------------------
    # A tool takes the clipboard text and returns the replacement.
    def pretty_json(s):
        try:
            return json.dumps(json.loads(s), indent=4, ensure_ascii=False)
        except json.JSONDecodeError:
            logger.error("Clipboard content is not valid JSON.")
            return s

    tools = [
        ("🔄", "Quote", ShowClipboardTools.quote),
        ("🔄", "Unindent", ShowClipboardTools.unindent),
        ("🔄", "Upper case", str.upper),
        ("🔄", "Lower case", str.lower),
        ("🔄", "Half width", ShowClipboardTools.to_half_width),
        ("🔄", "Full width", ShowClipboardTools.to_full_width),
        ("🔄", "Pretty JSON", pretty_json),
    ]
    kt[f"{LEADER}-Ctrl-V"] = ShowClipboardTools(tools)

    # ==================================================================
    # One window over several sources
    # ==================================================================

    # The three keys above are three keys.  A key is the scarce thing here —
    # there are only so many a person can hold — so a *source* is a value you
    # can hand to one window instead, and one incremental search then runs
    # across the lot.  Each row is labelled on the right with where it came
    # from, so a mixed list stays readable.

    # --- your own source ------------------------------------------------
    # Anything that returns a list can be one; no class to subclass.  This
    # lists the open windows and brings the chosen one forward.

    class OpenWindows(CandidateSource):
        name = "Window"

        def candidates(self):
            return [Candidate(icon="🪟",
                              display=f"{w.app_name} — {w.title}", payload=w)
                    for w in keymap.list_windows() if w.title]

        def on_chosen(self, candidate, modifier_flags):
            candidate.payload.activate()

    # --- scopes: one key, several sets ----------------------------------
    # Tab and Shift-Tab move along the cycle, and the query comes with you —
    # type what you are looking for, then look for it somewhere else without
    # retyping it.  The current scope is named at the right of the field.
    #
    # Scopes are also how an expensive source stays affordable: put one that
    # has real work to do in a scope of its own and it is paid for only when
    # you ask for it, not every time the window opens.

    kt[f"{LEADER}-P"] = ShowCandidates([            # P for palette
        Scope("All", [ClipboardHistorySource(), SnippetsSource(snippets),
                      ClipboardToolsSource(tools), OpenWindows()]),
        Scope("Clipboard", [ClipboardHistorySource(), SnippetsSource(snippets)]),
        Scope("Windows", [OpenWindows()]),
        Scope("Tools", [ClipboardToolsSource(tools)]),
    ])

    # A source does not have to be a class - a plain callable works when
    # there is nothing to name and one thing to do with every row:
    #
    #     kt[f"{LEADER}-B"] = ShowCandidates(
    #         lambda: [Candidate(display=b) for b in git_branches()],
    #         on_chosen=lambda c, mod: checkout(c.display))

    # ==================================================================
    # Windows and applications
    # ==================================================================

    # --- move the focused window ---------------------------------------
    # Apple keyboards translate Fn-Arrow into Home/End/PageUp/PageDown in
    # hardware (the Fn modifier itself still arrives), so with LEADER = Fn
    # the "...-Left" spellings would never fire on macOS - bind the keys
    # that actually arrive there.  Ctrl/Alt rather than Shift, because
    # Fn-Shift-Arrow is how you *select text* on a Mac laptop (it arrives
    # as Shift-Home etc.) - a Shift binding here would steal it.
    LEFT, RIGHT, UP, DOWN = (("Home", "End", "PageUp", "PageDown") if mac
                             else ("Left", "Right", "Up", "Down"))

    # Nudge by 20 px...
    kt[f"{LEADER}-Ctrl-{LEFT}"] = MoveWindow(direction="left", distance=20)
    kt[f"{LEADER}-Ctrl-{RIGHT}"] = MoveWindow(direction="right", distance=20)
    kt[f"{LEADER}-Ctrl-{UP}"] = MoveWindow(direction="up", distance=20)
    kt[f"{LEADER}-Ctrl-{DOWN}"] = MoveWindow(direction="down", distance=20)

    # ...or send it as far as it goes, stopping at other windows' edges
    # and screen edges (and hopping to the next monitor when already there).
    kt[f"{LEADER}-Alt-{LEFT}"] = MoveWindow(direction="left", distance=9999,
                                            window_edge=True, screen_edge=True)
    kt[f"{LEADER}-Alt-{RIGHT}"] = MoveWindow(direction="right", distance=9999,
                                             window_edge=True, screen_edge=True)
    kt[f"{LEADER}-Alt-{UP}"] = MoveWindow(direction="up", distance=9999,
                                          window_edge=True, screen_edge=True)
    kt[f"{LEADER}-Alt-{DOWN}"] = MoveWindow(direction="down", distance=9999,
                                            window_edge=True, screen_edge=True)

    # --- snap to screen regions (tiling) --------------------------------
    # Resizes to a half of the window's current screen, inside the work
    # area (menu bar, Dock and taskbar stay uncovered).  Same IJKL layout
    # as the arrows above.  ratio= picks a different split, e.g.
    # SnapWindow("left", ratio=2/3).
    kt[f"{LEADER}-Ctrl-J"] = SnapWindow("left")
    kt[f"{LEADER}-Ctrl-L"] = SnapWindow("right")
    kt[f"{LEADER}-Ctrl-I"] = SnapWindow("top")
    kt[f"{LEADER}-Ctrl-K"] = SnapWindow("bottom")
    kt[f"{LEADER}-F"] = SnapWindow("full")

    # --- minimize the focused window -------------------------------------
    def minimize_window():
        window = keymap.get_active_window()
        if window is not None:
            window.minimize()

    kt[f"{LEADER}-M"] = minimize_window

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
    # get_frame(), set_frame(), activate(), minimize(), is_minimized(),
    # restore().  Screen geometry: keymap.screen_frames() (whole screens),
    # keymap.screen_work_frames() (minus menu bar / Dock / taskbar) and
    # keymap.window_frames().  Window objects and screen_work_frames() are
    # UI-thread only - never touch them from a ThreadedAction.run(); the
    # thread-safe pair there is screen_frames() / window_frames().
    def describe_window():
        window = keymap.get_active_window()
        if window is None:
            logger.warning("No active window.")
            return
        x, y, w, h = window.get_frame()
        logger.info(f"{window.app_name}: \"{window.title}\" "
                    f"at ({x:.0f},{y:.0f}) {w:.0f}x{h:.0f}")
        logger.info(f"{len(keymap.list_windows())} windows open on "
                    f"{len(keymap.screen_frames())} screen(s)")

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
    # thread for run(), while starting() and finished() stay on the main
    # thread under the engine lock - so UI and window access is fine in those
    # two and not in run().  Actions run concurrently, so a slow one no longer
    # holds up the others.
    #
    # Esc stops a running action.  You write nothing for that: the waiting
    # helpers raise, and your finally blocks still run - so whatever the action
    # already recorded stays recorded.
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

    slowly = TypeSlowly("keyhac")
    kt[f"{LEADER}-Y"] = slowly

    # --- an action written for you by an agent -------------------------
    # It goes in ~/.keyhac/extensions/ as its own module.  Two lines here put
    # it on a key:
    #
    #     import open_issues                            # from extensions/
    #     kt[f"{LEADER}-I"] = open_issues.OpenIssues()
    #
    # Nothing above is needed to *write* one.  While the MCP endpoint is on
    # (AI Integration > MCP Server in the tray menu; off until you tick it, and
    # off again an hour later), an agent saves the module and runs the class
    # straight out of extensions/, so a fix loop never touches this file.  These
    # two lines are what makes the result yours: a key of your choosing, working
    # whether or not anything is connected.

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
    #
    # Name the applications.  The tempting shortcut - "a terminal is whatever
    # focuses a text area" - is wrong in both directions, and was shipped here
    # for a while: on macOS an editor pane and a chat box are AXTextArea too,
    # while VS Code's own integrated terminal is an AXTextField.  So the
    # binding below took over every text box on the machine and did nothing in
    # a real terminal.  A control's role says how it behaves, never what it is
    # for; if you do reach for focus.element, check what your terminal
    # actually reports in the console's "Focus path" field first.
    def is_terminal(focus):
        return focus.app_name in ("Terminal", "iTerm2", "WezTerm", "Alacritty",
                                  "kitty", "WindowsTerminal", "cmd",
                                  "powershell", "pwsh")

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
    # Full reference: doc/configuration.md in the Keyhac 2 source tree.
