"""Keyhac 2 configuration file.

This file is copied to ~/.keyhac/config.py on first run.  Edit that copy.
The same file runs on both Windows and macOS; use keymap.platform for
OS-specific parts.
"""

from keyhac import *


def configure(keymap):

    # ------------------------------------------------------------------
    # Global key table (active everywhere)

    kt_global = keymap.define_keytable(focus_path_pattern="*")

    # Sample: Fn/User0 + IJKL as arrow keys
    if keymap.platform == "mac":
        kt_global["Fn-J"] = "Left"
        kt_global["Fn-L"] = "Right"
        kt_global["Fn-I"] = "Up"
        kt_global["Fn-K"] = "Down"

    # Sample: a user modifier on the right Alt/Option key
    # keymap.define_modifier("RAlt", "User0")
    # kt_global["U0-J"] = "Left"
    # kt_global["U0-L"] = "Right"
    # kt_global["U0-I"] = "Up"
    # kt_global["U0-K"] = "Down"

    # Sample: replace a key entirely
    # keymap.replace_key("CapsLock", "F13")

    # Sample: one-shot modifier - tap produces a key, hold works as modifier
    # kt_global["O-RShift"] = "Escape"

    # ------------------------------------------------------------------
    # Application-specific key tables

    if keymap.platform == "mac":
        # Match by application name
        kt_terminal = keymap.define_keytable(app="Terminal|iTerm2")
        # kt_terminal["Ctrl-A"] = "Home"

        # Match by AX focus path (macOS only) - see the "Focus path" of the
        # console window (M2) or run with -d to log focus paths
        # kt_text = keymap.define_keytable(focus_path_pattern="*/AXTextArea()")

    if keymap.platform == "windows":
        # Match by exe / window class / title (class_name is Windows only)
        kt_notepad = keymap.define_keytable(app="notepad", class_name="Edit")
        # kt_notepad["C-A"] = "Home"    # short forms C-/A-/S-/W- also work

    # ------------------------------------------------------------------
    # Multi-stroke key table sample

    # kt_ctrlx = keymap.define_keytable(name="Ctrl-X")
    # kt_global["Ctrl-X"] = kt_ctrlx
    # kt_ctrlx["Ctrl-O"] = "Cmd-O" if keymap.platform == "mac" else "C-O"

    # ------------------------------------------------------------------
    # Function actions

    # def hello():
    #     print("Hello from config.py")
    # kt_global["Ctrl-F12"] = hello
