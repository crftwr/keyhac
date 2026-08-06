"""Key expression parsing and modifier matching semantics."""

import pytest

from keyhac.core.const import *
from keyhac.core.key import KeyCondition
from keyhac.core.vk import MAC_VK, WIN_VK


class TestParseMac:

    def test_plain_key(self, mac_names):
        k = KeyCondition.from_str("A")
        assert k.vk == MAC_VK["A"]
        assert k.mod == 0
        assert k.down is True
        assert k.oneshot is False

    def test_full_modifier_names(self, mac_names):
        k = KeyCondition.from_str("Ctrl-Shift-X")
        assert k.vk == MAC_VK["X"]
        assert k.mod == MODKEY_CTRL | MODKEY_SHIFT

    def test_side_specific_modifiers(self, mac_names):
        k = KeyCondition.from_str("LCmd-RAlt-J")
        assert k.mod == MODKEY_CMD_L | MODKEY_ALT_R

    def test_win_style_short_aliases(self, mac_names):
        assert KeyCondition.from_str("C-S-A-X") == KeyCondition.from_str("Ctrl-Shift-Alt-X")
        assert KeyCondition.from_str("LC-A") == KeyCondition.from_str("LCtrl-A")
        assert KeyCondition.from_str("U0-Left") == KeyCondition.from_str("User0-Left")

    def test_prefixes(self, mac_names):
        assert KeyCondition.from_str("D-F1").down is True
        assert KeyCondition.from_str("U-F1").down is False
        k = KeyCondition.from_str("O-RCmd")
        assert k.oneshot is True and k.down is True

    def test_case_insensitive(self, mac_names):
        assert KeyCondition.from_str("ctrl-x") == KeyCondition.from_str("Ctrl-X")

    def test_raw_vk_code(self, mac_names):
        assert KeyCondition.from_str("(123)").vk == 123
        assert KeyCondition.from_str("Ctrl-(200)").vk == 200

    def test_invalid_raises(self, mac_names):
        with pytest.raises(ValueError):
            KeyCondition.from_str("Bogus-X")
        with pytest.raises(ValueError):
            KeyCondition.from_str("NoSuchKey")

    def test_fn_and_cmd_are_mac_keys(self, mac_names):
        assert KeyCondition.from_str("Fn-J").mod == MODKEY_FN
        assert KeyCondition.from_str("Cmd-C").mod == MODKEY_CMD

    def test_round_trip_str(self, mac_names):
        for expr in ("D-Ctrl-Shift-A", "O-RCmd", "U-Fn-Space", "D-LAlt-F13"):
            k = KeyCondition.from_str(expr)
            assert KeyCondition.from_str(str(k)) == k


class TestParseWin:

    def test_win_modifier(self, win_names):
        k = KeyCondition.from_str("Win-E")
        assert k.vk == WIN_VK["E"]
        assert k.mod == MODKEY_WIN

    def test_oem_keys(self, win_names):
        assert KeyCondition.from_str("Semicolon").vk == WIN_VK["OEM_1"]
        assert KeyCondition.from_str("BackQuote").vk == WIN_VK["OEM_3"]

    def test_modifier_as_primary_key(self, win_names):
        assert KeyCondition.from_str("LWin").vk == WIN_VK["LWIN"]
        assert KeyCondition.from_str("Alt").vk == WIN_VK["MENU"]


class TestParseWinJIS:
    """The layout the engine picks up from GetKeyboardType(0) == 7.

    The vk numbers themselves are pinned against Microsoft's kbd106.dll in
    tests/test_win_layout.py; these check that a JIS config parses and
    dispatches through the engine, which nothing exercised before.
    """

    @pytest.fixture
    def jis_names(self):
        from keyhac.core.vk import init_key_names
        try:
            yield init_key_names("windows", "jis")
        finally:
            init_key_names("windows", "ansi")

    def test_punctuation_moves_to_its_jis_key(self, jis_names):
        # On a JIS 106 these live on their own keys, not as shifted digits.
        assert KeyCondition.from_str("Atmark").vk == WIN_VK["OEM_3"]
        assert KeyCondition.from_str("Caret").vk == WIN_VK["OEM_7"]
        assert KeyCondition.from_str("Semicolon").vk == WIN_VK["OEM_PLUS"]
        assert KeyCondition.from_str("Colon").vk == WIN_VK["OEM_1"]

    def test_quote_and_doublequote_are_shifted_digits(self, jis_names):
        assert KeyCondition.from_str("Quote").vk == WIN_VK["KEY_7"]
        assert KeyCondition.from_str("DoubleQuote").vk == WIN_VK["KEY_2"]

    def test_the_two_backslash_keys_are_distinct(self, jis_names):
        assert KeyCondition.from_str("BackSlash").vk == WIN_VK["OEM_102"]
        assert KeyCondition.from_str("Yen").vk == WIN_VK["OEM_5"]

    def test_modifiers_combine_with_jis_keys(self, jis_names):
        k = KeyCondition.from_str("Ctrl-Shift-Atmark")
        assert k.vk == WIN_VK["OEM_3"]
        assert k.mod == MODKEY_CTRL | MODKEY_SHIFT

    def test_remap_of_a_jis_key_dispatches(self, engine):
        """A JIS config end to end: the engine resolves the name, matches the
        incoming vk and sends the replacement."""
        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["Ctrl-Atmark"] = "Escape"

        e = engine(configure, platform="windows", layout="jis")
        assert e.vk("Atmark") == WIN_VK["OEM_3"]
        e.down("LCtrl")
        e.hook.clear()
        assert e.down("Atmark") is True
        assert "D-Escape" in e.sent_names()

    def test_the_same_config_hits_a_different_vk_on_ansi(self, engine):
        """The layout genuinely changes dispatch: Atmark is Shift-2 on ANSI,
        so the key that carries it on a JIS board must not trigger there."""
        def configure(keymap):
            kt = keymap.define_keytable(focus_path_pattern="*")
            kt["Ctrl-Atmark"] = "Escape"

        e = engine(configure, platform="windows", layout="ansi")
        assert e.vk("Atmark") == WIN_VK["KEY_2"]
        e.down("LCtrl")
        e.hook.clear()
        assert e.hook.key(WIN_VK["OEM_3"], True) is False
        assert "D-Escape" not in e.sent_names()


class TestModEq:

    def test_generic_matches_either_side(self):
        assert mod_eq(MODKEY_CTRL, MODKEY_CTRL_L)
        assert mod_eq(MODKEY_CTRL, MODKEY_CTRL_R)
        assert mod_eq(MODKEY_CTRL_L, MODKEY_CTRL)

    def test_side_specific_does_not_match_other_side(self):
        assert not mod_eq(MODKEY_CTRL_L, MODKEY_CTRL_R)

    def test_missing_modifier_fails(self):
        assert not mod_eq(MODKEY_CTRL, 0)
        assert not mod_eq(0, MODKEY_CTRL_L)
        assert not mod_eq(MODKEY_CTRL | MODKEY_SHIFT, MODKEY_CTRL_L)

    def test_combined(self):
        assert mod_eq(MODKEY_CTRL | MODKEY_SHIFT, MODKEY_CTRL_L | MODKEY_SHIFT_R)
        assert not mod_eq(MODKEY_CTRL | MODKEY_SHIFT, MODKEY_CTRL_L | MODKEY_ALT_L)

    def test_user_modifiers_16bit_planes(self):
        # USER2/USER3 exist only in keyhac-win; ensure the widened planes work
        assert mod_eq(MODKEY_USER3, MODKEY_USER3_L)
        assert not mod_eq(MODKEY_USER3, MODKEY_USER2_L)


class TestKeyConditionDict:

    def test_hash_buckets_by_vk_eq_resolves_sides(self, mac_names):
        table = {KeyCondition.from_str("Ctrl-A"): "action"}
        # Input with a concrete left-side modifier must find the generic entry
        probe = KeyCondition(MAC_VK["A"], MODKEY_CTRL_L, down=True)
        assert table[probe] == "action"

    def test_keytable_setitem_parses(self, mac_names):
        from keyhac.core.key import KeyTable
        kt = KeyTable(name="t")
        kt["Ctrl-X"] = "Cmd-C"
        probe = KeyCondition(MAC_VK["X"], MODKEY_CTRL_R, down=True)
        assert kt.table[probe] == "Cmd-C"

    def test_keytable_invalid_expression_ignored(self, mac_names):
        from keyhac.core.key import KeyTable
        kt = KeyTable(name="t")
        kt["Bogus-X"] = "A"  # logged, not raised
        assert len(kt.table) == 0


class TestCrossPlatformDiagnostics:
    """A config written for one OS should say *why* it fails on the other."""

    def test_mac_only_key_on_windows(self, win_names):
        with pytest.raises(ValueError, match="only on macOS"):
            KeyCondition.from_str("O-RCmd")

    def test_windows_only_key_on_mac(self, mac_names):
        with pytest.raises(ValueError, match="only on Windows"):
            KeyCondition.from_str("O-RWin")

    def test_genuinely_unknown_key_is_plain(self, win_names):
        with pytest.raises(ValueError, match="Unknown key name: Nonsense"):
            KeyCondition.from_str("Nonsense")

    def test_error_quotes_the_original_case(self, win_names):
        with pytest.raises(ValueError, match="RCmd"):
            KeyCondition.from_str("RCmd")
