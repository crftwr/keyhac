"""The two pieces Tab completion needs, on their own.

What the chooser does with them is in test_sources.py; these pin the
arithmetic, which is where the surprises are - a name that shares no prefix,
a caret past the end, and case.
"""

from keyhac.ui.completion import common_prefix, token_span


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
