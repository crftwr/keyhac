"""ToUnicodeEx-backed character translation (InputHook.char_for_key).

The chooser's filter field is fed through the key hook, because its window
takes no keyboard focus - so reconstructing typed text from virtual key codes
is the only way symbols reach it. A vk does not name a character, and Keyhac's
tables map names to codes rather than codes to glyphs, so the answer comes
from the OS.

These run against whatever layout the machine actually has, so they assert
what is true of *every* layout rather than pinning US glyphs: a letter key
produces that letter, Shift changes what a key produces, a Ctrl chord is not
text. The one layout-specific check is gated on the layout reporting itself
as US.
"""

import ctypes
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from keyhac.core.const import (  # noqa: E402
    MODKEY_ALT, MODKEY_CMD, MODKEY_CTRL, MODKEY_SHIFT,
)
from keyhac.core.vk import get_key_names, init_key_names  # noqa: E402
from keyhac.platform.win.hook import WinInputHook  # noqa: E402


@pytest.fixture
def hook():
    init_key_names("windows", "ansi")
    return WinInputHook()


def vk(name: str) -> int:
    return get_key_names().str_to_vk(name)


def _layout_is_us() -> bool:
    """Low word of the HKL is the language id; 0x0409 is en-US."""
    hkl = ctypes.WinDLL("user32").GetKeyboardLayout(0)
    return (hkl & 0xFFFF) == 0x0409


needs_us = pytest.mark.skipif(not _layout_is_us(),
                              reason="US layout only")


class TestEveryLayout:

    def test_a_letter_key_produces_its_letter(self, hook):
        assert hook.char_for_key(vk("A")) == "a"
        assert hook.char_for_key(vk("A"), MODKEY_SHIFT) == "A"

    def test_a_digit_key_produces_its_digit(self, hook):
        assert hook.char_for_key(vk("1")) == "1"

    def test_shift_changes_what_a_digit_produces(self, hook):
        plain = hook.char_for_key(vk("1"))
        shifted = hook.char_for_key(vk("1"), MODKEY_SHIFT)
        assert shifted is not None and shifted != plain

    def test_punctuation_produces_something(self, hook):
        # Which glyph depends on the layout; that one arrives at all is the
        # point - none of it reached the filter field before this existed.
        for name in ("Minus", "Period", "Comma", "Slash", "Semicolon"):
            assert hook.char_for_key(vk(name)) is not None, name

    def test_a_ctrl_chord_is_a_command_not_text(self, hook):
        assert hook.char_for_key(vk("A"), MODKEY_CTRL) is None
        assert hook.char_for_key(vk("Slash"), MODKEY_CTRL) is None

    def test_a_cmd_chord_is_not_text_either(self, hook):
        assert hook.char_for_key(vk("A"), MODKEY_CMD) is None

    def test_a_non_text_key_produces_nothing(self, hook):
        for name in ("F1", "Up", "Escape", "LShift"):
            assert hook.char_for_key(vk(name)) is None, name

    def test_translating_does_not_arm_a_dead_key(self, hook):
        """A dead key must not leave the keyboard composing: the next real
        keystroke would come out combined. Whatever this layout has, asking
        twice must give the same answer as asking once."""
        for name in ("A", "Minus", "BackQuote", "Quote", "6"):
            first = hook.char_for_key(vk(name))
            assert hook.char_for_key(vk(name)) == first, name


class TestUSLayout:

    @needs_us
    def test_the_us_glyphs(self, hook):
        for name, plain, shifted in (
            ("Minus", "-", "_"), ("Equal", "=", "+"),
            ("Comma", ",", "<"), ("Period", ".", ">"),
            ("Slash", "/", "?"), ("Semicolon", ";", ":"),
            ("Quote", "'", '"'), ("BackQuote", "`", "~"),
            ("OpenBracket", "[", "{"), ("CloseBracket", "]", "}"),
            ("BackSlash", "\\", "|"),
        ):
            assert hook.char_for_key(vk(name)) == plain, name
            assert hook.char_for_key(vk(name), MODKEY_SHIFT) == shifted, name

    @needs_us
    def test_the_us_shifted_digits(self, hook):
        for digit, shifted in zip("1234567890", "!@#$%^&*()"):
            assert hook.char_for_key(vk(digit), MODKEY_SHIFT) == shifted, digit

    @needs_us
    def test_altgr_is_not_text_on_a_us_layout(self, hook):
        # AltGr is Ctrl+Alt; US has no AltGr layer, so nothing comes back.
        assert hook.char_for_key(vk("Q"), MODKEY_ALT) is None
