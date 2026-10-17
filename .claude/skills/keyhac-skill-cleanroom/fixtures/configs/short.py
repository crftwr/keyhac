# Keyhac - the few keys I actually use.
from keyhac import *


def configure(keymap):
    if keymap.platform == "mac":
        primary = "Cmd"
    else:
        primary = "Ctrl"

    keys = keymap.define_keytable(focus_path_pattern="*")
    keys["Ctrl-Semicolon"] = InputText("dana@acme.test")  # keep: my address
    keys[f"{primary}-Shift-Left"] = SnapWindow("left")    # keep: tile left
    keys[f"{primary}-Shift-Right"] = SnapWindow("right")  # keep: tile right

    chat = keymap.define_keytable(app="Slack")
    chat["Ctrl-K"] = f"{primary}-K"  # keep: jump to a channel

# keep: a note of mine at the very end
