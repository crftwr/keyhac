"""Windows keyboard-layout detection and the JIS/ANSI vk tables.

These pin the layout tables in keyhac/core/vk.py against Microsoft's own
keyboard-type DLLs, in the same spirit as test_win_focus.py pinning the UIA
vtable slots: a wrong entry here silently sends the wrong key on a JIS
machine, which nobody on ANSI hardware would ever notice.

Why the DLLs and not VkKeyScanEx: kbdjpn.dll picks its scancode->vk variant
from GetKeyboardType(), so on ANSI hardware the "Japanese" *layout* hands back
the US-101 mapping and would happily confirm a wrong table. The
keyboard-*type* DLLs do not depend on what is plugged in -- kbd106.dll is the
JIS 106 answer and kbdus.dll the US 101 answer on any machine -- so the table
half of the JIS check runs everywhere. What still needs real JIS hardware is
only the one-line detection in WinInputHook.keyboard_layout()
(GetKeyboardType(0) == 7), covered by test_keyboard_layout_reports_this_machine
below to the extent this machine can.
"""

import ctypes
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from keyhac.core.vk import KeyNames, init_key_names  # noqa: E402
from keyhac.platform.win.hook import WinInputHook  # noqa: E402

# KBDTABLES, x64. Only the two fields the scancode->vk map needs.
_PUSVSCTOVK_OFFSET = 48   # USHORT *pusVSCtoVK
_BMAXVSCTOVK_OFFSET = 56  # BYTE    bMaxVSCtoVK


def _vsc_to_vk(dll_name: str) -> dict[int, int]:
    """scancode -> vk, read out of a Windows keyboard-type DLL."""
    dll = ctypes.WinDLL(dll_name)
    dll.KbdLayerDescriptor.argtypes = []
    dll.KbdLayerDescriptor.restype = ctypes.c_void_p
    p = dll.KbdLayerDescriptor()
    assert p, f"{dll_name}: KbdLayerDescriptor() returned NULL"

    pus = ctypes.c_void_p.from_address(p + _PUSVSCTOVK_OFFSET).value
    bmax = ctypes.c_ubyte.from_address(p + _BMAXVSCTOVK_OFFSET).value
    assert pus and bmax, f"{dll_name}: empty VSCtoVK table"

    arr = (ctypes.c_ushort * bmax).from_address(pus)
    # low byte is the vk, high byte carries KBDEXT/KBDMULTIVK/... flags
    return {sc: arr[sc] & 0xFF for sc in range(bmax) if arr[sc] & 0xFF}


@pytest.fixture(scope="module")
def us_table():
    return _vsc_to_vk("kbdus.dll")


@pytest.fixture(scope="module")
def jis_table():
    return _vsc_to_vk("kbd106.dll")


@pytest.mark.parametrize("dll", ["kbdus.dll", "kbd106.dll"])
def test_kbdtables_offsets_are_right(dll):
    """If the alphanumerics are wrong the struct offsets are wrong, and every
    other assertion in this module is meaningless."""
    table = _vsc_to_vk(dll)
    assert table[0x1E] == 0x41, "scancode 0x1E is A on every keyboard"
    assert table[0x02] == 0x31, "scancode 0x02 is 1 on every keyboard"
    assert table[0x39] == 0x20, "scancode 0x39 is Space on every keyboard"


# (scancode, keyhac key name) for the physical positions that differ between
# the two keyboards -- the unshifted legend and the shifted one sharing the key.
_US_POSITIONS = [
    (0x0D, "Equal"), (0x0D, "Plus"),
    (0x1A, "OpenBracket"), (0x1B, "CloseBracket"),
    (0x27, "Semicolon"), (0x27, "Colon"),
    (0x28, "Quote"), (0x28, "DoubleQuote"),
    (0x29, "BackQuote"), (0x29, "Tilde"),
    (0x2B, "BackSlash"),
]

_JIS_POSITIONS = [
    (0x0D, "Caret"), (0x0D, "Tilde"),
    (0x1A, "Atmark"), (0x1A, "BackQuote"),
    (0x1B, "OpenBracket"), (0x2B, "CloseBracket"),
    (0x27, "Semicolon"), (0x27, "Plus"),
    (0x28, "Colon"), (0x28, "Asterisk"),
    (0x73, "BackSlash"), (0x73, "Underscore"),
    (0x7D, "Yen"),
]


@pytest.mark.parametrize("scancode,name", _US_POSITIONS)
def test_ansi_table_matches_kbdus(us_table, scancode, name):
    names = KeyNames("windows", "ansi")
    assert names.str_to_vk(name) == us_table[scancode], (
        f"{name} should be the key at scancode {scancode:#04x} on a US 101")


@pytest.mark.parametrize("scancode,name", _JIS_POSITIONS)
def test_jis_table_matches_kbd106(jis_table, scancode, name):
    names = KeyNames("windows", "jis")
    assert names.str_to_vk(name) == jis_table[scancode], (
        f"{name} should be the key at scancode {scancode:#04x} on a JIS 106")


def test_jis_and_ansi_actually_differ():
    """Guards against a regression that quietly makes 'jis' an alias of 'ansi'
    -- every assertion above would still pass if both tables were the US one."""
    ansi = KeyNames("windows", "ansi")
    jis = KeyNames("windows", "jis")
    differing = [n for n in ("Semicolon", "Atmark", "Caret", "Quote",
                             "DoubleQuote", "Asterisk", "Equal", "Underscore")
                 if ansi.str_to_vk(n) != jis.str_to_vk(n)]
    assert len(differing) == 8, f"only {differing} differ between the layouts"


def test_yen_and_backslash_are_distinct_keys_on_jis():
    """A JIS 106 has two keys a US 101 renders as one backslash: the yen key
    left of Backspace and the 'ro' key left of right Shift."""
    jis = KeyNames("windows", "jis")
    assert jis.str_to_vk("Yen") != jis.str_to_vk("BackSlash")

    ansi = KeyNames("windows", "ansi")
    assert ansi.str_to_vk("Yen") == ansi.str_to_vk("BackSlash")


@pytest.mark.parametrize("layout", ["ansi", "jis"])
def test_every_named_key_renders_back_to_a_name(layout):
    """vk_to_str falls back to '(226)' for a vk it has no name for; a key the
    config can *name* must never log or replay as a raw number."""
    names = KeyNames("windows", layout)
    unnamed = [n for n in names.str_vk_table
               if names.vk_to_str(names.str_to_vk(n)).startswith("(")]
    assert unnamed == []


def test_init_key_names_selects_the_jis_table():
    """The process-wide selection Keymap.configure() makes."""
    try:
        assert init_key_names("windows", "jis").str_to_vk("Atmark") == 0xC0
        assert init_key_names("windows", "ansi").str_to_vk("Atmark") == 0x32
    finally:
        init_key_names("windows", "ansi")


def test_keyboard_layout_reports_this_machine():
    """The detection itself. Whichever branch this machine takes, the answer
    must agree with GetKeyboardType(0) -- the full JIS half of this check needs
    a real JIS keyboard attached (issue #10)."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetKeyboardType.argtypes = [ctypes.c_int]
    user32.GetKeyboardType.restype = ctypes.c_int
    kbd_type = user32.GetKeyboardType(0)

    layout = WinInputHook().keyboard_layout()
    assert layout == ("jis" if kbd_type == 7 else "ansi")
    assert layout in ("jis", "ansi"), "Windows never reports iso"
