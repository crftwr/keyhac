"""The Tab-completion core, on its own.

Ported in shape from XeFM's `xefm/completion.py` - longest common prefix on
Tab, a list when more than one thing could be meant - so these pin the
behaviour that shape is worth having, independently of what the chooser does
with it.
"""

import pytest

from keyhac.ui.completion import Completion, common_prefix, token_span


class _Field:
    """The three attributes the controller writes to."""

    def __init__(self, text="", cursor=None):
        self.text = text
        self.cursor = len(text) if cursor is None else cursor
        self._anchor = None


class TestCommonPrefix:

    def test_nothing_from_nothing(self):
        assert common_prefix([]) == ""

    def test_one_candidate_is_its_whole_self(self):
        assert common_prefix(["Menus"]) == "Menus"

    def test_the_longest_shared_start(self):
        assert common_prefix(["Clipboard", "Controls"]) == "C"
        assert common_prefix(["Tools old", "Tools new"]) == "Tools "

    def test_nothing_shared_is_empty(self):
        assert common_prefix(["Menus", "Windows"]) == ""

    def test_case_is_ignored_for_matching_and_kept_for_inserting(self):
        """A file path is case-sensitive because a filesystem is; a name is
        not. `men` should reach `Menus`, and what goes in is the name's own
        capitals."""
        assert common_prefix(["Menus", "menagerie"]) == "Men"


class TestTokenSpan:

    def test_the_word_the_caret_is_in(self):
        assert token_span("save menu", 9) == (5, 9)

    def test_it_stops_at_the_caret_not_at_the_end(self):
        assert token_span("save menu", 4) == (0, 4)

    def test_empty(self):
        assert token_span("", 0) == (0, 0)

    def test_a_caret_past_the_end_is_clamped(self):
        assert token_span("ab", 99) == (0, 2)


class TestCompleting:

    def _completion(self, text, candidates):
        field = _Field(text)
        return field, Completion(field, lambda token: [
            c for c in candidates if c.lower().startswith(token.lower())])

    def test_one_match_goes_in_whole_and_opens_nothing(self):
        field, completion = self._completion("men", ["Menus", "Windows"])
        assert completion.on_tab() is True
        assert field.text == "Menus"
        assert completion.active is False, "one match is an answer, not a list"

    def test_several_matches_insert_the_common_prefix_and_open(self):
        field, completion = self._completion("t", ["Tools old", "Tools new"])
        completion.on_tab()
        assert field.text == "Tools "
        assert completion.active is True
        assert completion.focused_index == -1, "it proposes nothing yet"

    def test_a_second_tab_steps_into_the_list(self):
        _field, completion = self._completion("t", ["Tools old", "Tools new"])
        completion.on_tab()
        completion.on_tab()
        assert completion.focused_index == 0
        completion.on_tab()
        assert completion.focused_index == 1
        completion.on_tab()
        assert completion.focused_index == 0, "it wraps"

    def test_backwards(self):
        _field, completion = self._completion("t", ["Tools old", "Tools new"])
        completion.on_tab()
        completion.on_tab(forward=False)
        assert completion.focused_index == 1

    def test_no_match_leaves_the_field_alone(self):
        field, completion = self._completion("zzz", ["Menus"])
        assert completion.on_tab() is False
        assert field.text == "zzz"
        assert completion.active is False

    def test_the_query_before_the_token_is_untouched(self):
        field, completion = self._completion("save men", ["Menus"])
        completion.on_tab()
        assert field.text == "save Menus"

    def test_open_all_ignores_what_is_typed(self):
        _field, completion = self._completion("zzz", ["Menus", "Windows"])
        assert completion.open_all() is True
        assert completion.candidates == ["Menus", "Windows"]

    def test_typing_narrows_and_then_closes(self):
        field, completion = self._completion("", ["Menus", "Windows"])
        completion.on_tab()
        assert completion.active
        field.text, field.cursor = "men", 3
        completion.on_text_changed()
        assert completion.candidates == ["Menus"]
        field.text, field.cursor = "zzz", 3
        completion.on_text_changed()
        assert completion.active is False

    def test_accept_answers_the_highlight(self):
        _field, completion = self._completion("t", ["Tools old", "Tools new"])
        completion.on_tab()
        completion.move_focus(1)
        completion.move_focus(1)
        assert completion.accept() == "Tools new"

    def test_accept_with_no_highlight_answers_the_first(self):
        """A list is open only because it was asked for, so this has an
        obvious thing to mean and no reason to dead-end."""
        _field, completion = self._completion("t", ["Tools old", "Tools new"])
        completion.on_tab()
        assert completion.focused_index == -1
        assert completion.accept() == "Tools old"

    def test_accept_with_nothing_open_answers_nothing(self):
        _field, completion = self._completion("", ["Menus"])
        assert completion.accept() is None


class TestTakingTheTokenBack:
    """A path completes into the text because the text is the answer. A scope
    completes into a change of what the window shows, so the token goes back
    out - and that is where a name with a space in it breaks a naive rule."""

    def _completion(self, text, candidates):
        field = _Field(text)
        return field, Completion(field, lambda token: [
            c for c in candidates if c.lower().startswith(token.lower())])

    def test_a_one_word_name(self):
        field, completion = self._completion("men", ["Menus"])
        completion.on_tab()
        completion.take_token()
        assert field.text == "" and field.cursor == 0

    def test_a_name_with_a_space_in_it(self):
        """The bug this exists for: asking afterwards which word the caret is
        in answers "only", and takes back half a name."""
        field, completion = self._completion("too", ["Tools only"])
        completion.on_tab()
        assert field.text == "Tools only"
        completion.take_token()
        assert field.text == "", field.text

    def test_the_rest_of_the_query_and_its_gap_survive(self):
        field, completion = self._completion("a too", ["Tools only"])
        completion.on_tab()
        completion.take_token()
        assert field.text == "a"

    def test_with_nothing_completed_it_takes_the_word_under_the_caret(self):
        field, completion = self._completion("a men", ["Menus"])
        completion.take_token()
        assert field.text == "a"
