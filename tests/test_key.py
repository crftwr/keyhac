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
