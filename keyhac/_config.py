"""Keyhac 2 configuration file.

This file is copied to ~/.keyhac/config.py on first run.  Edit that copy —
Keyhac reloads it from the tray menu or the console's hook toggle.

The same file runs on Windows and macOS.  Where the OSes genuinely differ,
branch on `keymap.platform`; the two constants set up at the top (LEADER and
MOD) absorb most of it.

Everything here is a working example, not pseudo-code.  Delete what you do
not want.  Every section has a fuller explanation — including the traps — in
doc/configuration.md.
"""

from keyhac import *

logger = getLogger("Config")


def configure(keymap):

    mac = keymap.platform == "mac"

    # ---- Setup -------------------------------------------------------

    # --- user modifier -------------------------------------------------
    # User0-User3 are modifiers no application sees; a key used this way is
    # never emitted.  macOS has Fn.  Windows has no spare key, so the Windows
    # keys are retired: renamed to codes Windows has no meaning for, and the
    # left one becomes User0.  Win+L and Win+G still fire - the OS handles
    # those before any hook runs.  ("Remapping and user modifiers" in
    # doc/configuration.md.)
    if not mac:
        keymap.replace_key("LWin", 235)     # 235, 255: unassigned key codes
        keymap.replace_key("RWin", 255)
        keymap.define_modifier(235, "User0")

    # --- the two portability constants ---------------------------------
    # LEADER: the modifier most samples below hang off.  MOD: the OS's
    # primary shortcut modifier, so one binding means Cmd-C on macOS and
    # Ctrl-C on Windows.
    LEADER = "Fn" if mac else "User0"
    MOD = "Cmd" if mac else "Ctrl"

    # --- swap a key entirely (uncomment to try) ------------------------
    # replace_key runs before any key table, so the rest of the config only
    # ever sees the replacement.
    # keymap.replace_key("CapsLock", "LCtrl")

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
    # IJKL as arrow keys while LEADER is held.
    kt[f"{LEADER}-I"] = "Up"
    kt[f"{LEADER}-J"] = "Left"
    kt[f"{LEADER}-K"] = "Down"
    kt[f"{LEADER}-L"] = "Right"
    kt[f"{LEADER}-U"] = "Home"
    kt[f"{LEADER}-O"] = "End"

    # --- key -> sequence of keys ---------------------------------------
    kt[f"{LEADER}-A"] = "Home", "Shift-End"        # select the whole line

    # --- one-shot: tap for one key, hold to modify ---------------------
    # Held, the key is still its modifier; only a lone tap-and-release
    # fires.  Pick one where a stray tap is harmless.
    if mac:
        # Tap either Cmd alone for Eisu/Kana (IME off/on).  Both names work
        # on Windows too, so this pair is portable if you want it there.
        kt["O-LCmd"] = "Eisu"
        kt["O-RCmd"] = "Kana"
    else:
        kt["O-RCtrl"] = "Win-S"                    # Start-menu search

    # --- key -> your own function --------------------------------------
    def hello():
        # print() and the logger both reach the console window.
        print("Hello from config.py")
        logger.info(f"platform={keymap.platform}")

    kt[f"{LEADER}-H"] = hello

    # --- typing literal text -------------------------------------------
    # InputText injects the characters themselves, so they land whatever the
    # IME is doing.
    kt[f"{LEADER}-Semicolon"] = InputText("me@example.com")

    # --- sending keys from your own function ---------------------------
    # One context batches the whole burst.  It is safe from a worker thread
    # as well (see ThreadedAction below).
    def duplicate_line():
        with keymap.get_input_context() as ctx:
            for key in ("Home", "Shift-End", f"{MOD}-C",
                        "End", "Enter", f"{MOD}-V"):
                ctx.send_key(key)

    kt[f"{LEADER}-Q"] = duplicate_line

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

    kt[f"{LEADER}-Space"] = toggle_ime

    # ---- Clipboard ---------------------------------------------------

    # --- history, in a chooser window ----------------------------------
    # Enter pastes; Shift-Enter only sets the clipboard.  Type to filter.
    kt[f"{LEADER}-V"] = ShowClipboardHistory()

    # --- fixed snippets -------------------------------------------------
    # (icon, label) | (icon, label, text) | (icon, label, callable)
    snippets = [
        ("📧", "me@example.com"),
        ("📮", "Mailing address", "400 Broad St, Seattle, WA 98109"),
        ("🕒", "Date", DateTimeSnippet("%Y-%m-%d")),
        ("🕒", "For filenames", DateTimeSnippet("%Y%m%d_%H%M%S")),
    ]
    kt[f"{LEADER}-Shift-V"] = ShowClipboardSnippets(snippets)

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
    kt[f"{LEADER}-Ctrl-V"] = ShowClipboardTools(tools)

    # ---- One window over several sources -----------------------------

    # A key is the scarce thing here, so a *source* is a value you hand to
    # one window instead, and one incremental search runs across the lot.
    # Tab and Shift-Tab move along the scopes, and the query comes with you.
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

    # One instance each, shared between the scopes below: a source is read
    # once per window and remembered against the *object*, so sharing means
    # it is not read twice.
    clipboard = ClipboardHistorySource()
    snippet_source = SnippetsSource(snippets)
    tool_source = ClipboardToolsSource(tools)
    menus = MenuItemsSource()          # macOS only; yields nothing elsewhere
    keys = KeyBindingsSource()
    actions = ActionsSource()
    controls = WindowControlsSource()

    # A source that reads the front application costs that work every time
    # the scope holding it is opened, so the expensive ones get a scope of
    # their own.  They stream: the first rows are on screen while the rest
    # are still being read.
    kt[f"{LEADER}-P"] = ShowCandidates([            # P for palette
        Scope("All", [clipboard, snippet_source, keys, actions]
              + ([menus] if mac else [])),
        Scope("Clipboard", [clipboard, snippet_source]),
        *([Scope("Menus", [menus])] if mac else []),   # every menu command
        Scope("Keys", [keys]),            # every binding here; Enter runs it
        Scope("Actions", [actions]),      # everything in extensions/
        Scope("Controls", [controls]),    # everything clickable, by name
        Scope("Windows", [OpenWindows()]),
        Scope("Tools", [tool_source]),
    ])

    # A source does not have to be a class - a plain callable works when
    # there is one thing to do with every row:
    #
    #     kt[f"{LEADER}-G"] = ShowCandidates(
    #         lambda: [Candidate(display=b) for b in git_branches()],
    #         on_chosen=lambda c, mod: checkout(c.display))

    # ---- Windows and applications ------------------------------------

    # Apple keyboards translate Fn-Arrow into Home/End/PageUp/PageDown in
    # hardware, so with LEADER = Fn the "...-Left" spellings never fire on
    # macOS - bind the keys that actually arrive.  Ctrl/Alt rather than
    # Shift, because Fn-Shift-Arrow is how you select text on a Mac laptop.
    LEFT, RIGHT, UP, DOWN = (("Home", "End", "PageUp", "PageDown") if mac
                             else ("Left", "Right", "Up", "Down"))

    # Ctrl nudges by 20 px; Alt sends it as far as it goes, stopping at
    # other windows' edges and screen edges (and hopping to the next monitor
    # when already there).  Delete a line to drop that direction.
    for key, direction in ((LEFT, "left"), (RIGHT, "right"),
                           (UP, "up"), (DOWN, "down")):
        kt[f"{LEADER}-Ctrl-{key}"] = MoveWindow(direction=direction,
                                                distance=20)
        kt[f"{LEADER}-Alt-{key}"] = MoveWindow(direction=direction,
                                               distance=9999,
                                               window_edge=True,
                                               screen_edge=True)

    # --- snap to screen regions (tiling) --------------------------------
    # Inside the work area, so the menu bar, Dock and taskbar stay
    # uncovered.  ratio= picks a different split, e.g. ratio=2/3.
    kt[f"{LEADER}-Ctrl-J"] = SnapWindow("left")
    kt[f"{LEADER}-Ctrl-L"] = SnapWindow("right")
    kt[f"{LEADER}-Ctrl-I"] = SnapWindow("top")
    kt[f"{LEADER}-Ctrl-K"] = SnapWindow("bottom")
    kt[f"{LEADER}-F"] = SnapWindow("full")

    # --- bring an application forward, or launch one --------------------
    # Matches like the focus conditions below: wildcards, "|" alternation,
    # case-insensitive, ".exe" optional.
    kt[f"{LEADER}-1"] = ActivateWindow(app="code|Visual Studio Code")
    kt[f"{LEADER}-2"] = ActivateWindow(app="chrome|Google Chrome")
    kt[f"{LEADER}-T"] = LaunchApplication("Terminal.app" if mac else "wt.exe")

    # --- inspect windows yourself ---------------------------------------
    # get_active_window() / find_window() / list_windows() return portable
    # Window objects (title, app_name, get_frame(), activate(), ...).  They
    # are UI-thread only - never touch one from a ThreadedAction.run(); the
    # thread-safe pair there is keymap.screen_frames() / window_frames().
    def minimize_window():
        window = keymap.get_active_window()
        if window is not None:
            window.minimize()

    kt[f"{LEADER}-M"] = minimize_window

    # ---- Keyboard macros ---------------------------------------------

    kt[f"{LEADER}-OpenBracket"] = ToggleRecordingKeys()    # record on/off
    kt[f"{LEADER}-CloseBracket"] = PlaybackRecordedKeys()  # replay
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

    kt[f"{LEADER}-Y"] = TypeSlowly("keyhac")

    # --- an action written for you by an agent -------------------------
    # It goes in ~/.keyhac/extensions/ as its own module, and while the MCP
    # endpoint is on (AI Integration > MCP Server in the tray menu) an agent
    # writes and runs it there without touching this file.  Two lines put it
    # on a key of your own:
    #
    #     import open_issues                            # from extensions/
    #     kt[f"{LEADER}-N"] = open_issues.OpenIssues()

    # ---- Multi-stroke key tables -------------------------------------

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

    # ---- Application-specific key tables -----------------------------
    # Merged in definition order, later ones win, so anything below
    # overrides the global table for the apps it matches.

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
    # The control hierarchy down to the focused element - watch the console's
    # "Focus path" field for the live value, and use "*" freely.  Note the
    # trailing "(*)": a component is "Role(Name)", and many controls do carry
    # a name, so "*/Edit()" would only match unnamed ones.
    kt_textarea = keymap.define_keytable(
        focus_path_pattern="*/AXTextArea(*)" if mac else "*/Edit(*)")
    kt_textarea[f"{LEADER}-Slash"] = InputText("# ")

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
    kt_terminal[f"{LEADER}-K"] = "Ctrl-K"     # clear, rather than "Down"

    # ---- More to explore ---------------------------------------------
    # keymap.focus            - the current Focus (app_name, window_title,
    #                           class_name, path, element, native)
    # keymap.replay_buffer    - the macro buffer behind the record actions
    # keymap.clipboard_history.items() / set_current(text)
    #
    # Full reference: doc/configuration.md in the Keyhac 2 source tree.
