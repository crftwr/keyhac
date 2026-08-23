"""The portable IME API: Keymap.get_ime_status / set_ime_status.

The interesting part of this API is what it promises when the OS cannot
answer, so most of these pin the None / False paths rather than the happy one.
The live per-OS behavior is in test_mac_ime.py and test_win_ime.py.
"""

import pytest

from keyhac.core.vk import init_key_names
from keyhac.platform.fake import FakeImeProvider


def configure_nothing(keymap):
    pass


@pytest.fixture
def keymap(engine):
    km = engine(configure_nothing).keymap
    km.ime_provider = FakeImeProvider()
    return km


# -- the Keymap surface -------------------------------------------------

def test_status_round_trip(keymap):
    assert keymap.get_ime_status() is False
    assert keymap.set_ime_status(True) is True
    assert keymap.get_ime_status() is True
    assert keymap.set_ime_status(False) is True
    assert keymap.get_ime_status() is False


def test_no_provider_reads_none_and_writes_false(engine):
    km = engine(configure_nothing).keymap
    assert km.ime_provider is None
    assert km.get_ime_status() is None
    assert km.set_ime_status(True) is False


def test_undeterminable_state_is_none_not_false(keymap):
    """None means "could not ask", which False would silently misreport."""
    keymap.ime_provider.status = None
    assert keymap.get_ime_status() is None
    assert keymap.set_ime_status(True) is False


def test_a_declined_change_reports_false(keymap):
    keymap.ime_provider.accepts = False
    assert keymap.set_ime_status(True) is False
    assert keymap.get_ime_status() is False


# -- macOS: mapping an input mode onto on/off ---------------------------

def test_mac_input_mode_maps_to_on_off():
    from keyhac.platform.mac.ime import _mode_is_on, ROMAN_MODE

    # A Japanese method in any of its kana modes is on ...
    for mode in ["com.apple.inputmethod.Japanese",
                 "com.apple.inputmethod.Japanese.Katakana",
                 "com.apple.inputmethod.Japanese.HalfWidthKana",
                 "com.apple.inputmethod.Japanese.FullWidthRoman"]:
        assert _mode_is_on(mode) is True, mode
    # ... and so is any other IME's non-Roman mode (the check is on the mode,
    # not on the bundle, which is what makes it work beyond Kotoeri).
    assert _mode_is_on("com.apple.inputmethod.SCIM.ITABC") is True
    # Alphanumeric mode, and a plain keyboard layout (no mode at all), are off.
    assert _mode_is_on(ROMAN_MODE) is False
    assert _mode_is_on(None) is False


# -- the key names the two OSes reach ------------------------------------

def test_eisu_and_kana_are_portable_by_meaning():
    """Same names on both OSes, different codes - Windows reaches VK_IME_OFF /
    VK_IME_ON, which is what macOS's Eisu / Kana keys mean."""
    mac = init_key_names("mac", "jis")
    win = init_key_names("windows", "jis")
    assert (mac.str_to_vk("Eisu"), mac.str_to_vk("Kana")) == (0x66, 0x68)
    assert (win.str_to_vk("Eisu"), win.str_to_vk("Kana")) == (0x1A, 0x16)
    for names in (mac, win):
        assert names.vk_to_str(names.str_to_vk("Eisu")) == "Eisu"
        assert names.vk_to_str(names.str_to_vk("Kana")) == "Kana"


def test_physical_jis_keys_are_windows_only():
    win = init_key_names("windows", "jis")
    assert win.str_to_vk("Kanji") == 0x19
    assert win.str_to_vk("Henkan") == 0x1C
    assert win.str_to_vk("Muhenkan") == 0x1D

    mac = init_key_names("mac", "jis")
    for name in ("Kanji", "Henkan", "Muhenkan"):
        with pytest.raises(ValueError, match="only on Windows"):
            mac.str_to_vk(name)
