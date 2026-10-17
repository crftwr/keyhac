# keyhac config - started on the Mac in 2023, carried to the Windows box later.
# No constants: every binding says what it does on each machine.

from keyhac import *


def configure(keymap):

    keymap.clipboard_history.max_items = 2000  # keep: I paste a lot

    # ------------------------------------------------------------
    # everywhere
    keytable_global = keymap.define_keytable(focus_path_pattern="*")

    if keymap.platform == "mac":
        keytable_global["Fn-H"] = "Left"
        keytable_global["Fn-L"] = "Right"
        keytable_global["Fn-Q"] = "Cmd-Q"  # keep: quit with Fn too
    else:
        keytable_global["Alt-H"] = "Left"
        keytable_global["Alt-L"] = "Right"

    keytable_global["Ctrl-Comma"] = InputText("Best,\nDana")  # keep: sign-off

    # ------------------------------------------------------------
    # terminals - by name; the list grows when I install one
    keytable_terminal = keymap.define_keytable(
        app="Terminal|iTerm2|WindowsTerminal|cmd|powershell|pwsh")
    keytable_terminal["Ctrl-L"] = "Ctrl-L"

    # ------------------------------------------------------------
    # browsers
    keytable_browser = keymap.define_keytable(app="chrome|Google Chrome|Safari|msedge")
    if keymap.platform == "mac":
        keytable_browser["Ctrl-Tab"] = "Cmd-Shift-CloseBracket"
    else:
        keytable_browser["Ctrl-W"] = "Ctrl-F4"  # keep: close the tab my way

    # ------------------------------------------------------------
    # (new tables go here)
