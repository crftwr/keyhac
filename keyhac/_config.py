"""Keyhac 2 configuration file.

This file is copied to ~/.keyhac/config.py on first run.  Edit that copy —
Keyhac reloads it from the tray menu or the console's hook toggle.

The same file runs on Windows and macOS.  Where the OSes genuinely differ,
branch on `keymap.platform`; the two constants set up at the top (MOD1, the
modifier this file claims for itself, and MOD2, the OS's own) absorb most of
it.

Everything here is a working example, not pseudo-code.  Delete what you do
not want.  Every section has a fuller explanation — including the traps — in
doc/configuration.md.
"""

from keyhac import *

logger = getLogger("Config")


def configure(keymap):

    mac = keymap.platform == "mac"
    win = keymap.platform == "windows"

    # ---- Setup -------------------------------------------------------

    # --- user modifier -------------------------------------------------
    # User0-User3 are modifiers no application sees; a key used this way is
    # never emitted.  macOS has Fn.  Windows has no spare key, so the Windows
    # keys are retired: renamed to codes Windows has no meaning for, and the
    # left one becomes User0.  Win+L and Win+G still fire - the OS handles
    # those before any hook runs.  ("Remapping and user modifiers" in
    # doc/configuration.md.)
    if win:
        keymap.replace_key("LWin", 235)     # 235, 255: unassigned key codes
        keymap.replace_key("RWin", 255)
        keymap.define_modifier(235, "User0")

    # --- the two portability constants ---------------------------------
    # MOD1 is yours: a modifier no application sees, which most of the
    # samples below hang off.  MOD2 is the OS's primary shortcut modifier,
    # so one binding means Cmd-C on macOS and Ctrl-C on Windows.
    MOD1 = "Fn" if mac else "User0"
    MOD2 = "Cmd" if mac else "Ctrl"

    # --- swap a key entirely -------------------------------------------
    # replace_key runs before any key table, so the rest of the config only
    # ever sees the replacement.  Right Alt is a spare key on most keyboards,
    # and a context menu key is missing from plenty of them.  Delete this if
    # yours is an ISO layout: there the right Alt is AltGr, and typing @ or
    # {} goes through it.
    keymap.replace_key("RAlt", "Apps" if win else "Menu")

    # --- text editor for "Edit Config" ---------------------------------
    # Unset, a default is picked.  An application name, or a callable taking
    # the path:
    # keymap.editor = "CotEditor" if mac else "notepad.exe"

    # --- clipboard history ---------------------------------------------
    keymap.clipboard_history.max_items = 500
    keymap.clipboard_history.max_data_size = 10 * 1024 * 1024

    # ---- Global key table (active everywhere) ------------------------

    kt = keymap.define_keytable(focus_path_pattern="*")

    # --- key -> key ----------------------------------------------------
    # IJKL as arrow keys while MOD1 is held.
    kt[f"{MOD1}-I"] = "Up"
    kt[f"{MOD1}-J"] = "Left"
    kt[f"{MOD1}-K"] = "Down"
    kt[f"{MOD1}-L"] = "Right"
    kt[f"{MOD1}-U"] = "Home"
    kt[f"{MOD1}-O"] = "End"

    # --- key -> sequence of keys ---------------------------------------
    kt[f"{MOD1}-A"] = "Home", "Shift-End"        # select the whole line

    # --- key -> your own function --------------------------------------
    def hello():
        # print() and the logger both reach the console window.
        print("Hello from config.py")
        logger.info(f"platform={keymap.platform}")

    kt[f"{MOD1}-H"] = hello

    # --- typing literal text -------------------------------------------
    # InputText injects the characters themselves, so they land whatever the
    # IME is doing.
    kt[f"{MOD1}-Semicolon"] = InputText("me@example.com")

    # --- sending keys from your own function ---------------------------
    # One context batches the whole burst.  It is safe from a worker thread
    # as well (see ThreadedAction below).
    def duplicate_line():
        with keymap.get_input_context() as ctx:
            for key in ("Home", "Shift-End", f"{MOD2}-C",
                        "End", "Enter", f"{MOD2}-V"):
                ctx.send_key(key)

    kt[f"{MOD1}-Q"] = duplicate_line

    # --- one-shot: tap for one key, hold to modify ---------------------
    # Held, the key is still its modifier; only a lone tap-and-release
    # fires.  Pick one where a stray tap is harmless: here the right Cmd
    # (right Ctrl on Windows), tapped alone, goes to the terminal - forward
    # if it is behind, on to its next window if it is already in front, and
    # launched if it is not running.  With Shift the walk runs the other way.
    # macOS reports the *localized* application name, which is why both
    # spellings are listed.
    TERMINAL = "Terminal|ターミナル|WindowsTerminal"
    TERMINAL_APP = "Terminal.app" if mac else "wt.exe"
    kt[f"O-R{MOD2}"] = ActivateApplication(app=TERMINAL, launch=TERMINAL_APP)
    kt[f"O-Shift-R{MOD2}"] = ActivateApplication(app=TERMINAL,
                                                 launch=TERMINAL_APP,
                                                 reverse=True)

    # ---- IME ---------------------------------------------------------

    # Both calls act on whatever holds the input focus.  get_ime_status() is
    # tri-state: True, False, or None for "could not tell".
    #
    # Do not turn the IME off, send keys, and turn it back on: the restore
    # wins the race and the keys compose anyway.  ("IME" in
    # doc/configuration.md explains why, and what to do instead.)

    def toggle_ime():
        status = keymap.get_ime_status()
        if status is None:
            logger.warning("No IME to toggle.")
            return
        # set_ime_status() reads the state back, so False means the IME
        # declined - not that the call failed to go out.
        if not keymap.set_ime_status(not status):
            logger.warning("The IME did not take the change.")

    kt[f"{MOD1}-Space"] = toggle_ime

    # ---- Clipboard ---------------------------------------------------

    # --- history, in a chooser window ----------------------------------
    # Enter pastes; Shift-Enter only sets the clipboard.  Type to filter.
    kt[f"{MOD1}-V"] = ShowClipboardHistory()

    # --- fixed snippets -------------------------------------------------
    # (icon, label) | (icon, label, text) | (icon, label, callable)
    snippets = [
        ("📧", "me@example.com"),
        ("📮", "Mailing address", "400 Broad St, Seattle, WA 98109"),
        ("🕒", "Date", DateTimeSnippet("%Y-%m-%d")),
        ("🕒", "For filenames", DateTimeSnippet("%Y%m%d_%H%M%S")),
    ]
    kt[f"{MOD1}-Shift-V"] = ShowClipboardSnippets(snippets)

    # --- transform whatever is on the clipboard -------------------------
    # A tool is any callable taking the clipboard text and returning the
    # replacement - your own included.
    tools = [
        ("🔄", "Quote", ShowClipboardTools.quote),
        ("🔄", "Unindent", ShowClipboardTools.unindent),
        ("🔄", "Upper case", str.upper),
        ("🔄", "Half width", ShowClipboardTools.to_half_width),
        ("🔄", "Full width", ShowClipboardTools.to_full_width),
    ]
    kt[f"{MOD1}-Ctrl-V"] = ShowClipboardTools(tools)

    # ---- One window over several sources -----------------------------

    # A key is the scarce thing here, so a *source* is a value you hand to
    # one window instead, and one incremental search runs across the lot.
    # Left and Right move between pages, and the query comes with you.
    # ("One window over several sources" in doc/configuration.md.)

    # --- your own source ------------------------------------------------
    # Anything that returns a list can be one.  This lists the open windows
    # and brings the chosen one forward.

    class OpenWindows(CandidateSource):
        name = "Window"

        def candidates(self):
            return [Candidate(icon="🪟",
                              display=f"{w.app_name} — {w.title}", payload=w)
                    for w in keymap.list_windows() if w.title]

        def on_chosen(self, candidate, modifier_flags):
            candidate.payload.activate()

    # One instance each, shared between the pages below: a source is read
    # once per window and remembered against the *object*, so sharing means
    # it is not read twice.
    clipboard = ClipboardHistorySource()
    snippet_source = SnippetsSource(snippets)
    tool_source = ClipboardToolsSource(tools)
    menus = MenuItemsSource()          # macOS only; yields nothing elsewhere
    keys = KeyBindingsSource()
    actions = ActionsSource()
    controls = WindowControlsSource()

    # Three pages, each named for what Enter does on it - so the query you
    # carry between them is the noun and the page is the verb: type `mail`,
    # and Left/Right asks run it, paste it, or click it.  The one you reach
    # for most goes in the middle, since the window opens there: it costs
    # nothing and both neighbours cost one press.  Three is about as many as
    # anyone holds without looking, which is why the list below is short
    # rather than complete - narrowing *within* a page is `@` and not
    # another page (see below).
    #
    # A source that reads the front application costs that work every time
    # the page holding it is opened, so those sit on the page you go to
    # deliberately.  They stream: the first rows are on screen while the
    # rest are still being read.
    kt[f"{MOD1}-P"] = ShowCandidates([            # P for palette
        # Run: what Keyhac itself defines - your bindings, your actions.
        ChooserPage("Run", [keys, actions]),
        ChooserPage("Paste", [clipboard, snippet_source, tool_source]),
        # Click: what you would otherwise reach for the mouse to hit - the
        # application's menu commands, its controls, and the other windows.
        ChooserPage("Click", ([menus] if mac else []) + [controls, OpenWindows()]),
    ])

    # Type `@` and a source name to narrow to one of them - `@Clip`, `@Menu`,
    # `@Key` - and Tab extends what you have typed as far as it is sure of.
    # The names are the ones already shown beside each row, so there is
    # nothing to memorise.  It is why three pages is enough: the page is
    # what you would do with it, `@` is which of the things there you meant.
    #
    # Want more pages?  Bind another key to another ShowCandidates.  A key is
    # the scarce thing, but it is yours to spend:
    #
    #   kt[f"{MOD1}-O"] = ShowCandidates([ChooserPage("Windows", [OpenWindows()])])

    # A source does not have to be a class - a plain callable works when
    # there is one thing to do with every row:
    #
    #     kt[f"{MOD1}-G"] = ShowCandidates(
    #         lambda: [Candidate(display=b) for b in git_branches()],
    #         on_chosen=lambda c, mod: checkout(c.display))

    # ---- Windows and applications ------------------------------------

    # Apple keyboards translate Fn-Arrow into Home/End/PageUp/PageDown in
    # hardware, so with MOD1 = Fn the "...-Left" spellings never fire on
    # macOS - bind the keys that actually arrive.  Ctrl/Alt rather than
    # Shift, because Fn-Shift-Arrow is how you select text on a Mac laptop.
    LEFT, RIGHT, UP, DOWN = (("Home", "End", "PageUp", "PageDown") if mac
                             else ("Left", "Right", "Up", "Down"))

    # Ctrl nudges by 20 px; Alt sends it as far as it goes, stopping at
    # other windows' edges and screen edges (and hopping to the next monitor
    # when already there).  Delete a line to drop that direction.
    for key, direction in ((LEFT, "left"), (RIGHT, "right"),
                           (UP, "up"), (DOWN, "down")):
        kt[f"{MOD1}-Ctrl-{key}"] = MoveWindow(direction=direction,
                                                distance=20)
        kt[f"{MOD1}-Alt-{key}"] = MoveWindow(direction=direction,
                                               distance=9999,
                                               window_edge=True,
                                               screen_edge=True)

    # --- snap to screen regions (tiling) --------------------------------
    # Inside the work area, so the menu bar, Dock and taskbar stay
    # uncovered.  ratio= picks a different split, e.g. ratio=2/3.
    kt[f"{MOD1}-Ctrl-J"] = SnapWindow("left")
    kt[f"{MOD1}-Ctrl-L"] = SnapWindow("right")
    kt[f"{MOD1}-Ctrl-I"] = SnapWindow("top")
    kt[f"{MOD1}-Ctrl-K"] = SnapWindow("bottom")
    kt[f"{MOD1}-F"] = SnapWindow("full")

    # --- bring an application forward -----------------------------------
    # Matches like the focus conditions below: wildcards, "|" alternation,
    # case-insensitive, ".exe" optional.  ActivateWindow does not walk an
    # application's windows and never starts one; the one-shot example above
    # is ActivateApplication, which does both.  LaunchApplication("wt.exe")
    # is the third option: start one, every press.
    kt[f"{MOD1}-1"] = ActivateWindow(app="code|Visual Studio Code")
    kt[f"{MOD1}-2"] = ActivateWindow(app="chrome|Google Chrome")

    # --- inspect windows yourself ---------------------------------------
    # get_active_window() / find_window() / list_windows() return portable
    # Window objects (title, app_name, get_frame(), activate(), ...).  They
    # are UI-thread only - never touch one from a ThreadedAction.run(); the
    # thread-safe pair there is keymap.screen_frames() / window_frames().
    def minimize_window():
        window = keymap.get_active_window()
        if window is not None:
            window.minimize()

    kt[f"{MOD1}-M"] = minimize_window

    # ---- Keyboard macros ---------------------------------------------

    kt[f"{MOD1}-OpenBracket"] = ToggleRecordingKeys()    # record on/off
    kt[f"{MOD1}-CloseBracket"] = PlaybackRecordedKeys()  # replay
    # StartRecordingKeys() / StopRecordingKeys() exist too.

    # ---- Background work: ThreadedAction -----------------------------

    # Anything slow (network, subprocess, sleeping) must not run inline - it
    # would block the keyboard hook.  run() is on a worker thread; starting()
    # and finished() stay on the main thread, so UI and window access is fine
    # in those two and not in run().  Esc stops a running action.
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

    kt[f"{MOD1}-Y"] = TypeSlowly("keyhac")

    # ---- Multi-stroke key tables -------------------------------------

    # Press MOD1-X, then a second key.  A balloon shows the table's name
    # while it is armed.
    kt_x = keymap.define_keytable(name=f"{MOD1}-X")
    kt[f"{MOD1}-X"] = kt_x
    kt_x["C"] = f"{MOD2}-C"
    kt_x["V"] = f"{MOD2}-V"
    kt_x["S"] = f"{MOD2}-S"

    # --- balloon messages of your own ------------------------------------
    def show_balloon():
        # Absent when running with --no-ui, so ask before using it.
        pop = getattr(keymap, "pop_balloon", None)
        if pop:
            pop("hello", "Keyhac is running", 2.0)

    kt[f"{MOD1}-B"] = show_balloon

    # ---- Application-specific key tables -----------------------------
    # Merged in definition order, later ones win, so anything below
    # overrides the global table for the apps it matches.

    # --- by application name (portable) ----------------------------------
    kt_browser = keymap.define_keytable(app="chrome|Google Chrome|firefox|Safari")
    kt_browser[f"{MOD1}-R"] = f"{MOD2}-R"          # reload

    # --- by window title -------------------------------------------------
    # kt_docs = keymap.define_keytable(title="*Google Docs*")

    # --- by Win32 window class (Windows only) -----------------------------
    if win:
        kt_notepad = keymap.define_keytable(app="notepad", class_name="Edit")
        kt_notepad[f"{MOD1}-D"] = "Home", "Shift-Down", "Shift-End", "Delete"

    # --- by focus path ----------------------------------------------------
    # The control hierarchy down to the focused element - watch the console's
    # "Focus path" field for the live value, and use "*" freely.  Note the
    # trailing "(*)": a component is "Role(Name)", and many controls do carry
    # a name, so "*/Edit()" would only match unnamed ones.
    kt_textarea = keymap.define_keytable(
        focus_path_pattern="*/AXTextArea(*)" if mac else "*/Edit(*)")
    kt_textarea[f"{MOD1}-Slash"] = InputText("# ")

    # --- by your own test -------------------------------------------------
    # custom_condition_func receives the portable Focus object: app_name,
    # window_title, class_name (Windows), path, and element.  Name the
    # applications: "a terminal is whatever focuses a text area" was shipped
    # here for a while and is wrong in both directions - a chat box is an
    # AXTextArea too, and VS Code's own terminal is an AXTextField.
    def is_terminal(focus):
        return focus.app_name in ("Terminal", "iTerm2", "WezTerm", "Alacritty",
                                  "kitty", "WindowsTerminal", "cmd",
                                  "powershell", "pwsh")

    kt_terminal = keymap.define_keytable(custom_condition_func=is_terminal)
    kt_terminal[f"{MOD1}-K"] = "Ctrl-K"     # clear, rather than "Down"

    # ---- An action written for you by an agent ------------------------
    # It goes in ~/.keyhac/extensions/ as its own module, and while the MCP
    # endpoint is on (AI Integration > MCP Server in the tray menu) an agent
    # writes and runs it there without touching this file.  Two lines put it
    # on a key of your own:
    #
    #     import open_issues                            # from extensions/
    #     kt[f"{MOD1}-N"] = open_issues.OpenIssues()

    # ---- More to explore ---------------------------------------------
    # keymap.focus            - the current Focus (app_name, window_title,
    #                           class_name, path, element, native)
    # keymap.replay_buffer    - the macro buffer: .clear() and .max_seq
    #                           have no action of their own
    # keymap.clipboard_history.items() / set_current(text)
    #
    # Full reference: doc/configuration.md in the Keyhac 2 source tree.
