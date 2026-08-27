"""Hook keystrokes -> PuiKit events, for a candidate window that is not
focused (spike - discussion #112).

A window created with ``WindowStyle(activates=False)`` never takes OS
keyboard focus, which is the whole point for the views under discussion: a
list of "what is actionable in the current window" must not change the
current window by opening, and a list of "which bindings apply to the
focused element" contradicts itself if it steals the focus.  PuiKit is
explicit that ``activates=False`` is display-only - "keyboard-taking
non-activating panels are a separate future feature".

Keyhac does not need that feature, because it already has a global keyboard
hook.  ``Keymap.push_modal_input`` routes each keystroke here, this turns it
into the ``Event`` PuiKit's widgets expect, and ``Panel.dispatch_event``
feeds it to the window's own focused widget.  No OS focus changes hands.

**What this route can and cannot deliver.**  The hook reports a virtual key
code, not a produced glyph, and Keyhac's per-layout tables map names to codes
rather than codes to glyphs - so a name like ``Minus`` does not say which
character it makes.  Letters, space and the named editing / cursor keys are
named here directly; everything else is asked of the OS through
``InputHook.char_for_key``, which is the same translation it performs for a
real keystroke and therefore follows whatever layout is selected.  Without
that answer (a platform that cannot say, a chord that is not text) the key is
dropped rather than guessed at.

**And no input method, ever.**  Composition is owned by the window with OS
keyboard focus - IMM32 delivers ``WM_IME_*`` only to the focused HWND, and
``NSTextInputClient`` only serves the key window.  A non-activating window
is by construction neither, and a hook delivering virtual key codes cannot
substitute: it sees the physical keys, not what an IME would make of them.

So a non-activating candidate window can be filtered by ASCII and nothing
else.  That is not a footnote about Japanese input - it is what makes
``keyhac.core.matcher.with_migemo()`` structural rather than optional: for a
localised candidate list, romaji matching is the only way the filter field
can reach the rows at all.
"""

from puikit.event import Event, EventType, char_key_event

from keyhac.core.const import (
    MODKEY_ALT, MODKEY_ALT_L, MODKEY_ALT_R,
    MODKEY_CMD, MODKEY_CMD_L, MODKEY_CMD_R,
    MODKEY_CTRL, MODKEY_CTRL_L, MODKEY_CTRL_R,
    MODKEY_SHIFT, MODKEY_SHIFT_L, MODKEY_SHIFT_R,
)
from keyhac.core.vk import get_key_names

#: Keyhac key name -> PuiKit key name, for the keys whose spellings differ
#: (PuiKit's names are the concatenated ones from its keyboard contract).
_NAMED = {
    "Return": "enter", "Escape": "escape", "Tab": "tab",
    "Back": "backspace", "Delete": "delete", "Insert": "insert",
    "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Home": "home", "End": "end",
    "PageUp": "pageup", "PageDown": "pagedown",
    "Space": "space",
    "NumEnter": "enter",
}

#: Keyhac modifier bits -> the four names PuiKit events carry.  Shift is
#: handled separately: it decides the glyph as well as the modifier set.
_MODS = (
    (MODKEY_CTRL | MODKEY_CTRL_L | MODKEY_CTRL_R, "ctrl"),
    (MODKEY_ALT | MODKEY_ALT_L | MODKEY_ALT_R, "alt"),
    (MODKEY_CMD | MODKEY_CMD_L | MODKEY_CMD_R, "cmd"),
)

_SHIFT = MODKEY_SHIFT | MODKEY_SHIFT_L | MODKEY_SHIFT_R


def to_event(key) -> Event | None:
    """The PuiKit ``Event`` for one hook `KeyCondition`, or None.

    None means "this key produces nothing this route can carry" - a chord
    that is a command rather than text, or a key the platform cannot
    translate.  The caller drops it rather than guessing.
    """
    name = get_key_names().vk_to_str(key.vk)
    shift = bool(key.mod & _SHIFT)
    modifiers = {n for bits, n in _MODS if key.mod & bits}

    if name in _NAMED:
        if shift:
            modifiers.add("shift")
        puikit_name = _NAMED[name]
        # SPACE keeps its glyph so a text field still inserts it, per the
        # contract's note on named-but-printable keys.
        char = " " if puikit_name == "space" else None
        return Event(type=EventType.KEY, key=puikit_name, char=char,
                     modifiers=frozenset(modifiers))

    if len(name) == 1 and name.isalpha():
        # Contract §2: key is always the lowercase letter, char is the glyph
        # actually produced, and shift is kept in the modifier set.
        if shift:
            modifiers.add("shift")
        return Event(type=EventType.KEY, key=name.lower(),
                     char=name.upper() if shift else name.lower(),
                     modifiers=frozenset(modifiers))

    if name.startswith("F") and name[1:].isdigit():
        if shift:
            modifiers.add("shift")
        return Event(type=EventType.KEY, key=name.lower(),
                     modifiers=frozenset(modifiers))

    # Digits and punctuation: contract §3 makes the produced glyph the key's
    # identity, so the glyph has to come from the layout rather than from the
    # key's name. char_key_event applies the rest of the rule, including
    # dropping shift - it is already baked into the glyph.
    char = _char_for(key)
    if char is not None:
        return char_key_event(char, frozenset(modifiers))

    return None


def _char_for(key) -> str | None:
    """What the OS says this key produces, or None."""
    from keyhac.core.keymap import Keymap

    keymap = Keymap.get_instance()
    if keymap is None:
        return None
    return keymap.char_for_key(key.vk, key.mod)
