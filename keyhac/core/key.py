"""KeyCondition and KeyTable.

Ported from keyhac-mac keyhac_key.py; modifier comparison semantics shared
with keyhac-win (hash by vk only, L/R-agnostic equality via mod_eq).
"""

from keyhac.core.const import *
from keyhac.core.vk import KeyNames, get_key_names
from keyhac.core import log

logger = log.getLogger("Key")


class KeyCondition:
    """A single key stroke condition (vk + modifier state + down/up/oneshot)."""

    def __init__(self, vk: int, mod: int = 0, down: bool = True, oneshot: bool = False):
        self.vk = vk
        self.mod = mod
        self.down = down
        self.oneshot = oneshot

    def __hash__(self):
        # Hash by vk only so that dict lookup buckets by key code and __eq__
        # resolves generic-vs-L/R modifier matching (both predecessors do this).
        return self.vk

    def __eq__(self, other):
        if self.vk != other.vk:
            return False
        if not mod_eq(self.mod, other.mod):
            return False
        if self.down != other.down:
            return False
        if self.oneshot != other.oneshot:
            return False
        return True

    def __str__(self):
        s = ""
        if self.oneshot:
            s += "O-"
        elif self.down:
            s += "D-"
        else:
            s += "U-"

        for name, generic, left, right in (
            ("Alt", MODKEY_ALT, MODKEY_ALT_L, MODKEY_ALT_R),
            ("Ctrl", MODKEY_CTRL, MODKEY_CTRL_L, MODKEY_CTRL_R),
            ("Shift", MODKEY_SHIFT, MODKEY_SHIFT_L, MODKEY_SHIFT_R),
            ("Win", MODKEY_WIN, MODKEY_WIN_L, MODKEY_WIN_R),
            ("Cmd", MODKEY_CMD, MODKEY_CMD_L, MODKEY_CMD_R),
            ("Fn", MODKEY_FN, MODKEY_FN_L, MODKEY_FN_R),
            ("User0", MODKEY_USER0, MODKEY_USER0_L, MODKEY_USER0_R),
            ("User1", MODKEY_USER1, MODKEY_USER1_L, MODKEY_USER1_R),
            ("User2", MODKEY_USER2, MODKEY_USER2_L, MODKEY_USER2_R),
            ("User3", MODKEY_USER3, MODKEY_USER3_L, MODKEY_USER3_R),
        ):
            if self.mod & generic:
                s += f"{name}-"
            elif self.mod & left:
                s += f"L{name}-"
            elif self.mod & right:
                s += f"R{name}-"

        s += get_key_names().vk_to_str(self.vk)
        return s

    def __repr__(self):
        return f"KeyCondition({self})"

    @staticmethod
    def from_str(s: str) -> "KeyCondition":
        """Parse a key expression like "Ctrl-X", "O-RCmd", "U-Fn-Space", "C-A"."""
        names = get_key_names()
        s = s.upper()

        mod = 0
        down = True
        oneshot = False

        token_list = s.split("-")

        for token in token_list[:-1]:
            token = token.strip()
            try:
                mod |= KeyNames.str_to_mod(token)
            except ValueError:
                if token == "O":
                    oneshot = True
                elif token == "D":
                    down = True
                elif token == "U":
                    down = False
                else:
                    raise ValueError(f"Unknown modifier: {token}") from None

        token = token_list[-1].strip()
        vk = names.str_to_vk(token)

        return KeyCondition(vk, mod, down=down, oneshot=oneshot)


class KeyTable:
    """Dict-like table assigning input key conditions to output actions.

    Values may be:
      - a key expression string, or a list/tuple of them (key output)
      - any callable (executed on key down)
      - another KeyTable (enters multi-stroke mode)
    """

    def __init__(self, name: str = None):
        self.name = name
        self.table = {}

    def __repr__(self):
        return f"KeyTable({self.name})"

    def __setitem__(self, key, value):
        try:
            key = KeyCondition.from_str(key)
        except ValueError:
            logger.error(f"Invalid key expression: {key}")
            return
        self.table[key] = value

    def __getitem__(self, key):
        try:
            key = KeyCondition.from_str(key)
        except ValueError:
            logger.error(f"Invalid key expression: {key}")
            return None
        return self.table[key]

    def __delitem__(self, key):
        try:
            key = KeyCondition.from_str(key)
        except ValueError:
            logger.error(f"Invalid key expression: {key}")
            return
        del self.table[key]
