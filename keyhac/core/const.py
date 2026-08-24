"""Modifier key bit flags.

Three bit planes: generic (side-agnostic), Left, Right.

keyhac-win used 8-bit planes (Alt/Ctrl/Shift/Win/User0-3) and keyhac-mac used
8-bit planes (Alt/Ctrl/Shift/Win/Cmd/Fn/User0-1).  Keyhac2 needs the union
(10 modifiers), which no longer fits in 8 bits, so the planes are 16 bits wide.
"""

MODKEY_PLANE_BITS = 16
MODKEY_PLANE_MASK = (1 << MODKEY_PLANE_BITS) - 1

MODKEY_ALT   = 1 << 0
MODKEY_CTRL  = 1 << 1
MODKEY_SHIFT = 1 << 2
MODKEY_WIN   = 1 << 3
MODKEY_CMD   = 1 << 4
MODKEY_FN    = 1 << 5
MODKEY_USER0 = 1 << 6
MODKEY_USER1 = 1 << 7
MODKEY_USER2 = 1 << 8
MODKEY_USER3 = 1 << 9

MODKEY_ALT_L   = MODKEY_ALT   << MODKEY_PLANE_BITS
MODKEY_CTRL_L  = MODKEY_CTRL  << MODKEY_PLANE_BITS
MODKEY_SHIFT_L = MODKEY_SHIFT << MODKEY_PLANE_BITS
MODKEY_WIN_L   = MODKEY_WIN   << MODKEY_PLANE_BITS
MODKEY_CMD_L   = MODKEY_CMD   << MODKEY_PLANE_BITS
MODKEY_FN_L    = MODKEY_FN    << MODKEY_PLANE_BITS
MODKEY_USER0_L = MODKEY_USER0 << MODKEY_PLANE_BITS
MODKEY_USER1_L = MODKEY_USER1 << MODKEY_PLANE_BITS
MODKEY_USER2_L = MODKEY_USER2 << MODKEY_PLANE_BITS
MODKEY_USER3_L = MODKEY_USER3 << MODKEY_PLANE_BITS

MODKEY_ALT_R   = MODKEY_ALT   << (MODKEY_PLANE_BITS * 2)
MODKEY_CTRL_R  = MODKEY_CTRL  << (MODKEY_PLANE_BITS * 2)
MODKEY_SHIFT_R = MODKEY_SHIFT << (MODKEY_PLANE_BITS * 2)
MODKEY_WIN_R   = MODKEY_WIN   << (MODKEY_PLANE_BITS * 2)
MODKEY_CMD_R   = MODKEY_CMD   << (MODKEY_PLANE_BITS * 2)
MODKEY_FN_R    = MODKEY_FN    << (MODKEY_PLANE_BITS * 2)
MODKEY_USER0_R = MODKEY_USER0 << (MODKEY_PLANE_BITS * 2)
MODKEY_USER1_R = MODKEY_USER1 << (MODKEY_PLANE_BITS * 2)
MODKEY_USER2_R = MODKEY_USER2 << (MODKEY_PLANE_BITS * 2)
MODKEY_USER3_R = MODKEY_USER3 << (MODKEY_PLANE_BITS * 2)

#: The Win key in all three planes - the OS keeps hold of this one whatever
#: Keyhac does with it, so define_modifier refuses it.
MODKEY_WIN_ALL = MODKEY_WIN | MODKEY_WIN_L | MODKEY_WIN_R

_MODKEY_USER = MODKEY_USER0 | MODKEY_USER1 | MODKEY_USER2 | MODKEY_USER3

MODKEY_USER_ALL = (
    _MODKEY_USER
    | (_MODKEY_USER << MODKEY_PLANE_BITS)
    | (_MODKEY_USER << (MODKEY_PLANE_BITS * 2))
)


def mod_eq(mod1: int, mod2: int) -> bool:
    """Compare two modifier states, treating a generic (side-agnostic) bit as
    equal to either the Left or the Right bit of the same modifier.

    Ported from keyhac-win checkModifier() / keyhac-mac KeyCondition.mod_eq(),
    generalized to 16-bit planes.
    """
    _mod1 = mod1 & MODKEY_PLANE_MASK
    _mod2 = mod2 & MODKEY_PLANE_MASK
    _mod1_l = (mod1 >> MODKEY_PLANE_BITS) & MODKEY_PLANE_MASK
    _mod2_l = (mod2 >> MODKEY_PLANE_BITS) & MODKEY_PLANE_MASK
    _mod1_r = (mod1 >> (MODKEY_PLANE_BITS * 2)) & MODKEY_PLANE_MASK
    _mod2_r = (mod2 >> (MODKEY_PLANE_BITS * 2)) & MODKEY_PLANE_MASK

    if _mod1 & ~(_mod2 | _mod2_l | _mod2_r):
        return False
    if _mod1_l & ~(_mod2 | _mod2_l):
        return False
    if _mod1_r & ~(_mod2 | _mod2_r):
        return False
    if _mod2 & ~(_mod1 | _mod1_l | _mod1_r):
        return False
    if _mod2_l & ~(_mod1 | _mod1_l):
        return False
    if _mod2_r & ~(_mod1 | _mod1_r):
        return False
    return True
