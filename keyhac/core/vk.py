"""Portable key names and per-OS virtual key code tables.

The engine and config files use portable key name strings ("A", "Semicolon",
"F13", ...).  This module owns the per-OS translation tables:

- macOS tables ported from keyhac-mac (keyhac_const.py / keyhac_key.py),
  with ANSI and JIS layout variants.
- Windows tables ported from keyhac-win (keyhac_keymap.py), which used the
  standard Win32 VK_* values via pyauto; layout variants "std" (US) and JIS.

A process-wide `KeyNames` instance is selected with `init_key_names()`
(normally called by Keymap.configure with the platform and detected layout).
"""

from keyhac.core.const import *

# --------------------------------------------------------------------------
# macOS virtual key codes (Carbon kVK_*)

MAC_VK = dict(
    A=0x00, S=0x01, D=0x02, F=0x03, H=0x04, G=0x05, Z=0x06, X=0x07,
    C=0x08, V=0x09, B=0x0B, Q=0x0C, W=0x0D, E=0x0E, R=0x0F, Y=0x10,
    T=0x11, O=0x1F, U=0x20, I=0x22, P=0x23, L=0x25, J=0x26, K=0x28,
    N=0x2D, M=0x2E,
    KEY_1=0x12, KEY_2=0x13, KEY_3=0x14, KEY_4=0x15, KEY_6=0x16, KEY_5=0x17,
    KEY_9=0x19, KEY_7=0x1A, KEY_8=0x1C, KEY_0=0x1D,
    MINUS=0x1B, SEMICOLON=0x29, COMMA=0x2B, SLASH=0x2C, PERIOD=0x2F,
    BACKQUOTE=0x32,
    DECIMAL=0x41, MULTIPLY=0x43, ADD=0x45, NUMPAD_CLEAR=0x47, DIVIDE=0x4B,
    NUMPAD_ENTER=0x4C, SUBTRACT=0x4E, NUMPAD_EQUAL=0x51,
    NUMPAD0=0x52, NUMPAD1=0x53, NUMPAD2=0x54, NUMPAD3=0x55, NUMPAD4=0x56,
    NUMPAD5=0x57, NUMPAD6=0x58, NUMPAD7=0x59, NUMPAD8=0x5B, NUMPAD9=0x5C,
    RETURN=0x24, TAB=0x30, SPACE=0x31, BACK=0x33, ESCAPE=0x35,
    RCOMMAND=0x36, LCOMMAND=0x37, LSHIFT=0x38, CAPITAL=0x39, LALT=0x3A,
    LCONTROL=0x3B, RSHIFT=0x3C, RALT=0x3D, RCONTROL=0x3E, FUNCTION=0x3F,
    F1=0x7A, F2=0x78, F3=0x63, F4=0x76, F5=0x60, F6=0x61, F7=0x62, F8=0x64,
    F9=0x65, F10=0x6D, F11=0x67, F12=0x6F, F13=0x69, F14=0x6B, F15=0x71,
    F16=0x6A, F17=0x40, F18=0x4F, F19=0x50, F20=0x5A,
    MENU=0x6E, HELP=0x72, HOME=0x73, PRIOR=0x74, DELETE=0x75, END=0x77,
    NEXT=0x79, LEFT=0x7B, RIGHT=0x7C, DOWN=0x7D, UP=0x7E,
    ISO_SECTION=0x0A,
    ANSI_CLOSEBRACKET=0x1E, ANSI_OPENBRACKET=0x21, ANSI_QUOTE=0x27,
    ANSI_BACKSLASH=0x2A, ANSI_EQUAL=0x18,
    JIS_OPENBRACKET=0x1E, JIS_CLOSEBRACKET=0x2A, JIS_COLON=0x27,
    JIS_BACKSLASH=0x5E, JIS_YEN=0x5D, JIS_KEYPAD_COMMA=0x5F, JIS_EISU=0x66,
    JIS_KANA=0x68, JIS_ATMARK=0x21, JIS_CARET=0x18,
)

# --------------------------------------------------------------------------
# Windows virtual key codes (standard Win32 VK_* values)

WIN_VK = dict(
    LBUTTON=0x01, RBUTTON=0x02, MBUTTON=0x04,
    BACK=0x08, TAB=0x09, RETURN=0x0D,
    SHIFT=0x10, CONTROL=0x11, MENU=0x12, PAUSE=0x13, CAPITAL=0x14,
    ESCAPE=0x1B, SPACE=0x20,
    PRIOR=0x21, NEXT=0x22, END=0x23, HOME=0x24,
    LEFT=0x25, UP=0x26, RIGHT=0x27, DOWN=0x28,
    SNAPSHOT=0x2C, INSERT=0x2D, DELETE=0x2E,
    KEY_0=0x30, KEY_1=0x31, KEY_2=0x32, KEY_3=0x33, KEY_4=0x34,
    KEY_5=0x35, KEY_6=0x36, KEY_7=0x37, KEY_8=0x38, KEY_9=0x39,
    A=0x41, B=0x42, C=0x43, D=0x44, E=0x45, F=0x46, G=0x47, H=0x48,
    I=0x49, J=0x4A, K=0x4B, L=0x4C, M=0x4D, N=0x4E, O=0x4F, P=0x50,
    Q=0x51, R=0x52, S=0x53, T=0x54, U=0x55, V=0x56, W=0x57, X=0x58,
    Y=0x59, Z=0x5A,
    LWIN=0x5B, RWIN=0x5C, APPS=0x5D,
    NUMPAD0=0x60, NUMPAD1=0x61, NUMPAD2=0x62, NUMPAD3=0x63, NUMPAD4=0x64,
    NUMPAD5=0x65, NUMPAD6=0x66, NUMPAD7=0x67, NUMPAD8=0x68, NUMPAD9=0x69,
    MULTIPLY=0x6A, ADD=0x6B, SUBTRACT=0x6D, DECIMAL=0x6E, DIVIDE=0x6F,
    F1=0x70, F2=0x71, F3=0x72, F4=0x73, F5=0x74, F6=0x75, F7=0x76, F8=0x77,
    F9=0x78, F10=0x79, F11=0x7A, F12=0x7B, F13=0x7C, F14=0x7D, F15=0x7E,
    F16=0x7F, F17=0x80, F18=0x81, F19=0x82, F20=0x83,
    NUMLOCK=0x90, SCROLL=0x91,
    LSHIFT=0xA0, RSHIFT=0xA1, LCONTROL=0xA2, RCONTROL=0xA3,
    LMENU=0xA4, RMENU=0xA5,
    OEM_1=0xBA, OEM_PLUS=0xBB, OEM_COMMA=0xBC, OEM_MINUS=0xBD,
    OEM_PERIOD=0xBE, OEM_2=0xBF, OEM_3=0xC0, OEM_4=0xDB, OEM_5=0xDC,
    OEM_6=0xDD, OEM_7=0xDE, OEM_102=0xE2,
)

# --------------------------------------------------------------------------
# Modifier name -> bits (OS independent).
# Full names are the canonical form (keyhac-mac style); single-letter short
# forms are accepted as aliases for keyhac-win migration.

STR_MOD_TABLE = {
    "ALT": MODKEY_ALT, "CTRL": MODKEY_CTRL, "SHIFT": MODKEY_SHIFT,
    "WIN": MODKEY_WIN, "CMD": MODKEY_CMD, "FN": MODKEY_FN,
    "USER0": MODKEY_USER0, "USER1": MODKEY_USER1,
    "USER2": MODKEY_USER2, "USER3": MODKEY_USER3,

    "LALT": MODKEY_ALT_L, "LCTRL": MODKEY_CTRL_L, "LSHIFT": MODKEY_SHIFT_L,
    "LWIN": MODKEY_WIN_L, "LCMD": MODKEY_CMD_L, "LFN": MODKEY_FN_L,
    "LUSER0": MODKEY_USER0_L, "LUSER1": MODKEY_USER1_L,
    "LUSER2": MODKEY_USER2_L, "LUSER3": MODKEY_USER3_L,

    "RALT": MODKEY_ALT_R, "RCTRL": MODKEY_CTRL_R, "RSHIFT": MODKEY_SHIFT_R,
    "RWIN": MODKEY_WIN_R, "RCMD": MODKEY_CMD_R, "RFN": MODKEY_FN_R,
    "RUSER0": MODKEY_USER0_R, "RUSER1": MODKEY_USER1_R,
    "RUSER2": MODKEY_USER2_R, "RUSER3": MODKEY_USER3_R,

    # keyhac-win short aliases
    "A": MODKEY_ALT, "C": MODKEY_CTRL, "S": MODKEY_SHIFT, "W": MODKEY_WIN,
    "U0": MODKEY_USER0, "U1": MODKEY_USER1, "U2": MODKEY_USER2, "U3": MODKEY_USER3,
    "LA": MODKEY_ALT_L, "LC": MODKEY_CTRL_L, "LS": MODKEY_SHIFT_L, "LW": MODKEY_WIN_L,
    "LU0": MODKEY_USER0_L, "LU1": MODKEY_USER1_L, "LU2": MODKEY_USER2_L, "LU3": MODKEY_USER3_L,
    "RA": MODKEY_ALT_R, "RC": MODKEY_CTRL_R, "RS": MODKEY_SHIFT_R, "RW": MODKEY_WIN_R,
    "RU0": MODKEY_USER0_R, "RU1": MODKEY_USER1_R, "RU2": MODKEY_USER2_R, "RU3": MODKEY_USER3_R,
}

# --------------------------------------------------------------------------


def _mac_tables(layout: str):
    v = MAC_VK
    str_vk = {
        **{ch: v[ch] for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
        **{str(n): v[f"KEY_{n}"] for n in range(10)},
        "MINUS": v["MINUS"], "COMMA": v["COMMA"], "PERIOD": v["PERIOD"],
        "NUMCLEAR": v["NUMPAD_CLEAR"], "NUMENTER": v["NUMPAD_ENTER"],
        "NUMEQUAL": v["NUMPAD_EQUAL"],
        "DIVIDE": v["DIVIDE"], "MULTIPLY": v["MULTIPLY"],
        "SUBTRACT": v["SUBTRACT"], "ADD": v["ADD"], "DECIMAL": v["DECIMAL"],
        **{f"NUM{n}": v[f"NUMPAD{n}"] for n in range(10)},
        **{f"F{n}": v[f"F{n}"] for n in range(1, 21)},
        "LEFT": v["LEFT"], "RIGHT": v["RIGHT"], "UP": v["UP"], "DOWN": v["DOWN"],
        "SPACE": v["SPACE"], "TAB": v["TAB"], "BACK": v["BACK"],
        "RETURN": v["RETURN"], "ENTER": v["RETURN"],
        "ESCAPE": v["ESCAPE"], "ESC": v["ESCAPE"],
        "CAPSLOCK": v["CAPITAL"], "CAPS": v["CAPITAL"], "CAPITAL": v["CAPITAL"],
        "MENU": v["MENU"],
        "HELP": v["HELP"], "DELETE": v["DELETE"], "HOME": v["HOME"],
        "END": v["END"], "PAGEDOWN": v["NEXT"], "PAGEUP": v["PRIOR"],
        "EISU": v["JIS_EISU"], "KANA": v["JIS_KANA"],
        "ALT": v["LALT"], "LALT": v["LALT"], "RALT": v["RALT"],
        "CTRL": v["LCONTROL"], "LCTRL": v["LCONTROL"], "RCTRL": v["RCONTROL"],
        "SHIFT": v["LSHIFT"], "LSHIFT": v["LSHIFT"], "RSHIFT": v["RSHIFT"],
        "CMD": v["LCOMMAND"], "LCMD": v["LCOMMAND"], "RCMD": v["RCOMMAND"],
        "FN": v["FUNCTION"],
    }
    vk_str = {
        **{v[ch]: ch for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
        **{v[f"KEY_{n}"]: str(n) for n in range(10)},
        v["MINUS"]: "Minus", v["COMMA"]: "Comma", v["PERIOD"]: "Period",
        v["NUMPAD_CLEAR"]: "NumClear", v["NUMPAD_ENTER"]: "NumEnter",
        v["NUMPAD_EQUAL"]: "NumEqual",
        v["DIVIDE"]: "Divide", v["MULTIPLY"]: "Multiply",
        v["SUBTRACT"]: "Subtract", v["ADD"]: "Add", v["DECIMAL"]: "Decimal",
        **{v[f"NUMPAD{n}"]: f"Num{n}" for n in range(10)},
        **{v[f"F{n}"]: f"F{n}" for n in range(1, 21)},
        v["LEFT"]: "Left", v["RIGHT"]: "Right", v["UP"]: "Up", v["DOWN"]: "Down",
        v["SPACE"]: "Space", v["TAB"]: "Tab", v["BACK"]: "Back",
        v["RETURN"]: "Return", v["ESCAPE"]: "Escape", v["CAPITAL"]: "CapsLock",
        v["MENU"]: "Menu",
        v["HELP"]: "Help", v["DELETE"]: "Delete", v["HOME"]: "Home",
        v["END"]: "End", v["NEXT"]: "PageDown", v["PRIOR"]: "PageUp",
        v["JIS_EISU"]: "Eisu", v["JIS_KANA"]: "Kana",
        v["LALT"]: "LAlt", v["RALT"]: "RAlt",
        v["LCONTROL"]: "LCtrl", v["RCONTROL"]: "RCtrl",
        v["LSHIFT"]: "LShift", v["RSHIFT"]: "RShift",
        v["LCOMMAND"]: "LCmd", v["RCOMMAND"]: "RCmd",
        v["FUNCTION"]: "Fn",
    }

    if layout == "jis":
        str_vk.update({
            "SEMICOLON": v["SEMICOLON"], "COLON": v["JIS_COLON"],
            "SLASH": v["SLASH"], "BACKQUOTE": v["BACKQUOTE"],
            "TILDE": v["JIS_CARET"],
            "OPENBRACKET": v["JIS_OPENBRACKET"], "YEN": v["JIS_YEN"],
            "CLOSEBRACKET": v["JIS_CLOSEBRACKET"], "CARET": v["JIS_CARET"],
            "BACKSLASH": v["JIS_BACKSLASH"],
            "QUOTE": v["KEY_7"], "DOUBLEQUOTE": v["KEY_2"],
            "UNDERSCORE": v["JIS_BACKSLASH"], "ASTERISK": v["JIS_COLON"],
            "ATMARK": v["JIS_ATMARK"],
            "EQUAL": v["MINUS"], "PLUS": v["SEMICOLON"],
        })
        vk_str.update({
            v["SEMICOLON"]: "Semicolon", v["JIS_COLON"]: "Colon",
            v["SLASH"]: "Slash", v["BACKQUOTE"]: "BackQuote",
            v["JIS_ATMARK"]: "Atmark", v["JIS_OPENBRACKET"]: "OpenBracket",
            v["JIS_YEN"]: "Yen", v["JIS_CLOSEBRACKET"]: "CloseBracket",
            v["JIS_CARET"]: "Caret", v["JIS_BACKSLASH"]: "BackSlash",
        })
    else:  # ansi (and fallback for iso: unsupported, common keys only)
        str_vk.update({
            "SEMICOLON": v["SEMICOLON"], "COLON": v["SEMICOLON"],
            "SLASH": v["SLASH"], "BACKQUOTE": v["BACKQUOTE"],
            "TILDE": v["BACKQUOTE"],
            "OPENBRACKET": v["ANSI_OPENBRACKET"],
            "CLOSEBRACKET": v["ANSI_CLOSEBRACKET"],
            "BACKSLASH": v["ANSI_BACKSLASH"], "YEN": v["ANSI_BACKSLASH"],
            "QUOTE": v["ANSI_QUOTE"], "DOUBLEQUOTE": v["ANSI_QUOTE"],
            "UNDERSCORE": v["MINUS"], "ASTERISK": v["KEY_8"],
            "ATMARK": v["KEY_2"], "CARET": v["KEY_6"],
            "EQUAL": v["ANSI_EQUAL"], "PLUS": v["ANSI_EQUAL"],
        })
        vk_str.update({
            v["SEMICOLON"]: "Semicolon", v["SLASH"]: "Slash",
            v["BACKQUOTE"]: "BackQuote",
            v["ANSI_OPENBRACKET"]: "OpenBracket",
            v["ANSI_CLOSEBRACKET"]: "CloseBracket",
            v["ANSI_BACKSLASH"]: "BackSlash", v["ANSI_QUOTE"]: "Quote",
            v["ANSI_EQUAL"]: "Equal",
        })

    modifier_vk_map = {
        v["LSHIFT"]: MODKEY_SHIFT_L, v["RSHIFT"]: MODKEY_SHIFT_R,
        v["LCONTROL"]: MODKEY_CTRL_L, v["RCONTROL"]: MODKEY_CTRL_R,
        v["LALT"]: MODKEY_ALT_L, v["RALT"]: MODKEY_ALT_R,
        v["LCOMMAND"]: MODKEY_CMD_L, v["RCOMMAND"]: MODKEY_CMD_R,
        v["FUNCTION"]: MODKEY_FN_L,
    }
    return str_vk, vk_str, modifier_vk_map


def _win_tables(layout: str):
    v = WIN_VK
    str_vk = {
        **{ch: v[ch] for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
        **{str(n): v[f"KEY_{n}"] for n in range(10)},
        "MINUS": v["OEM_MINUS"], "COMMA": v["OEM_COMMA"], "PERIOD": v["OEM_PERIOD"],
        "NUMLOCK": v["NUMLOCK"],
        "DIVIDE": v["DIVIDE"], "MULTIPLY": v["MULTIPLY"],
        "SUBTRACT": v["SUBTRACT"], "ADD": v["ADD"], "DECIMAL": v["DECIMAL"],
        **{f"NUM{n}": v[f"NUMPAD{n}"] for n in range(10)},
        **{f"F{n}": v[f"F{n}"] for n in range(1, 21)},
        "LEFT": v["LEFT"], "RIGHT": v["RIGHT"], "UP": v["UP"], "DOWN": v["DOWN"],
        "SPACE": v["SPACE"], "TAB": v["TAB"], "BACK": v["BACK"],
        "RETURN": v["RETURN"], "ENTER": v["RETURN"],
        "ESCAPE": v["ESCAPE"], "ESC": v["ESCAPE"],
        "CAPSLOCK": v["CAPITAL"], "CAPS": v["CAPITAL"], "CAPITAL": v["CAPITAL"],
        "APPS": v["APPS"],
        "INSERT": v["INSERT"], "DELETE": v["DELETE"], "HOME": v["HOME"],
        "END": v["END"], "PAGEDOWN": v["NEXT"], "PAGEUP": v["PRIOR"],
        "ALT": v["MENU"], "LALT": v["LMENU"], "RALT": v["RMENU"],
        "CTRL": v["CONTROL"], "LCTRL": v["LCONTROL"], "RCTRL": v["RCONTROL"],
        "SHIFT": v["SHIFT"], "LSHIFT": v["LSHIFT"], "RSHIFT": v["RSHIFT"],
        "LWIN": v["LWIN"], "RWIN": v["RWIN"],
        "PRINTSCREEN": v["SNAPSHOT"], "SCROLLLOCK": v["SCROLL"],
        "PAUSE": v["PAUSE"],
        "LBUTTON": v["LBUTTON"], "RBUTTON": v["RBUTTON"], "MBUTTON": v["MBUTTON"],
    }
    vk_str = {
        **{v[ch]: ch for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
        **{v[f"KEY_{n}"]: str(n) for n in range(10)},
        v["OEM_MINUS"]: "Minus", v["OEM_PLUS"]: "Plus",
        v["OEM_COMMA"]: "Comma", v["OEM_PERIOD"]: "Period",
        v["NUMLOCK"]: "NumLock",
        v["DIVIDE"]: "Divide", v["MULTIPLY"]: "Multiply",
        v["SUBTRACT"]: "Subtract", v["ADD"]: "Add", v["DECIMAL"]: "Decimal",
        **{v[f"NUMPAD{n}"]: f"Num{n}" for n in range(10)},
        **{v[f"F{n}"]: f"F{n}" for n in range(1, 21)},
        v["LEFT"]: "Left", v["RIGHT"]: "Right", v["UP"]: "Up", v["DOWN"]: "Down",
        v["SPACE"]: "Space", v["TAB"]: "Tab", v["BACK"]: "Back",
        v["RETURN"]: "Return", v["ESCAPE"]: "Escape", v["CAPITAL"]: "CapsLock",
        v["APPS"]: "Apps",
        v["INSERT"]: "Insert", v["DELETE"]: "Delete", v["HOME"]: "Home",
        v["END"]: "End", v["NEXT"]: "PageDown", v["PRIOR"]: "PageUp",
        v["MENU"]: "Alt", v["LMENU"]: "LAlt", v["RMENU"]: "RAlt",
        v["CONTROL"]: "Ctrl", v["LCONTROL"]: "LCtrl", v["RCONTROL"]: "RCtrl",
        v["SHIFT"]: "Shift", v["LSHIFT"]: "LShift", v["RSHIFT"]: "RShift",
        v["LWIN"]: "LWin", v["RWIN"]: "RWin",
        v["SNAPSHOT"]: "PrintScreen", v["SCROLL"]: "ScrollLock",
        v["PAUSE"]: "Pause",
        v["LBUTTON"]: "LBUTTON", v["RBUTTON"]: "RBUTTON", v["MBUTTON"]: "MBUTTON",
    }

    if layout == "jis":
        str_vk.update({
            "SEMICOLON": v["OEM_PLUS"], "COLON": v["OEM_1"],
            "SLASH": v["OEM_2"], "BACKQUOTE": v["OEM_3"], "TILDE": v["OEM_7"],
            "OPENBRACKET": v["OEM_4"], "BACKSLASH": v["OEM_102"],
            "YEN": v["OEM_5"], "CLOSEBRACKET": v["OEM_6"],
            "QUOTE": v["KEY_7"], "DOUBLEQUOTE": v["KEY_2"],
            "UNDERSCORE": v["OEM_102"], "ASTERISK": v["OEM_1"],
            "ATMARK": v["OEM_3"], "CARET": v["OEM_7"],
            "EQUAL": v["OEM_MINUS"], "PLUS": v["OEM_PLUS"],
        })
        vk_str.update({
            v["OEM_1"]: "Colon", v["OEM_2"]: "Slash", v["OEM_3"]: "Atmark",
            v["OEM_4"]: "OpenBracket", v["OEM_5"]: "Yen",
            v["OEM_6"]: "CloseBracket", v["OEM_7"]: "Caret",
            v["OEM_102"]: "BackSlash",
        })
    else:  # std / US
        str_vk.update({
            "SEMICOLON": v["OEM_1"], "COLON": v["OEM_1"],
            "SLASH": v["OEM_2"], "BACKQUOTE": v["OEM_3"], "TILDE": v["OEM_3"],
            "OPENBRACKET": v["OEM_4"], "BACKSLASH": v["OEM_5"],
            "YEN": v["OEM_5"], "CLOSEBRACKET": v["OEM_6"],
            "QUOTE": v["OEM_7"], "DOUBLEQUOTE": v["OEM_7"],
            "UNDERSCORE": v["OEM_MINUS"], "ASTERISK": v["KEY_8"],
            "ATMARK": v["KEY_2"], "CARET": v["KEY_6"],
            "EQUAL": v["OEM_PLUS"], "PLUS": v["OEM_PLUS"],
        })
        vk_str.update({
            v["OEM_1"]: "Semicolon", v["OEM_2"]: "Slash",
            v["OEM_3"]: "BackQuote", v["OEM_4"]: "OpenBracket",
            v["OEM_5"]: "BackSlash", v["OEM_6"]: "CloseBracket",
            v["OEM_7"]: "Quote",
        })

    modifier_vk_map = {
        v["LSHIFT"]: MODKEY_SHIFT_L, v["RSHIFT"]: MODKEY_SHIFT_R,
        v["LCONTROL"]: MODKEY_CTRL_L, v["RCONTROL"]: MODKEY_CTRL_R,
        v["LMENU"]: MODKEY_ALT_L, v["RMENU"]: MODKEY_ALT_R,
        v["LWIN"]: MODKEY_WIN_L, v["RWIN"]: MODKEY_WIN_R,
    }
    return str_vk, vk_str, modifier_vk_map


class KeyNames:
    """Key name <-> virtual key code translation for one OS + layout."""

    def __init__(self, os_name: str, layout: str = "ansi"):
        self.os_name = os_name
        self.layout = layout
        if os_name == "mac":
            self.str_vk_table, self.vk_str_table, self.modifier_vk_map = _mac_tables(layout)
        elif os_name == "windows":
            self.str_vk_table, self.vk_str_table, self.modifier_vk_map = _win_tables(layout)
        else:
            raise ValueError(f"Unknown OS name: {os_name}")

    def str_to_vk(self, name: str) -> int:
        try:
            return self.str_vk_table[name.upper()]
        except KeyError:
            try:
                return int(name.strip("()"))
            except Exception:
                raise ValueError(f"Unknown key name: {name}") from None

    def vk_to_str(self, vk: int) -> str:
        try:
            return self.vk_str_table[vk]
        except KeyError:
            return "(%d)" % vk

    @staticmethod
    def str_to_mod(name: str, force_LR: bool = False) -> int:
        try:
            mod = STR_MOD_TABLE[name.upper()]
        except KeyError:
            raise ValueError(f"Unknown modifier name: {name}") from None
        if force_LR and (mod & MODKEY_PLANE_MASK):
            mod <<= MODKEY_PLANE_BITS
        return mod


_active: KeyNames | None = None


def init_key_names(os_name: str, layout: str = "ansi") -> KeyNames:
    """Select the process-wide key name table (called by Keymap.configure)."""
    global _active
    _active = KeyNames(os_name, layout)
    return _active


def get_key_names() -> KeyNames:
    if _active is None:
        raise RuntimeError("init_key_names() has not been called yet")
    return _active
