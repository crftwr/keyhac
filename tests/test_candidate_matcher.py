"""Candidate objects and pluggable matchers (discussion #112, issue #106).

The Migemo tests skip when pymigemo is not installed; everything else -
including the degradation path - runs without it.
"""

import pytest

from keyhac.core import matcher as m
from keyhac.core import migemo
from keyhac.core.candidate import Candidate

needs_migemo = pytest.mark.skipif(not migemo.available(),
                                  reason="pymigemo not installed")


class TestCandidate:

    def test_match_text_defaults_to_display(self):
        assert Candidate(display="alpha").match_text == "alpha"

    def test_match_text_can_differ_from_display(self):
        c = Candidate(display="notes.txt", match_text="/home/u/notes.txt")
        assert c.display == "notes.txt"
        assert c.match_text == "/home/u/notes.txt"

    def test_from_tuple_keeps_the_whole_tuple_as_payload(self):
        item = ("*", "alpha", "extra")
        c = Candidate.from_item(item)
        assert (c.icon, c.display, c.payload) == ("*", "alpha", item)
        assert c.label == "* alpha"

    def test_from_item_passes_a_candidate_through(self):
        c = Candidate(display="x")
        assert Candidate.from_item(c) is c

    def test_label_without_an_icon_is_the_display_text(self):
        assert Candidate(display="alpha").label == "alpha"


class TestSubstringMatcher:

    def test_empty_query_matches_everything(self):
        match = m.SubstringMatcher().compile("")
        assert match.hit("anything")
        assert match.spans("anything") == []

    def test_case_insensitive(self):
        assert m.SubstringMatcher().compile("AL").hit("alpha")

    def test_words_are_anded_in_any_order(self):
        match = m.SubstringMatcher().compile("ph al")
        assert match.hit("alpha")
        assert not match.hit("alps")

    def test_spans_locate_every_word(self):
        assert m.SubstringMatcher().compile("a").spans("banana") == [
            (1, 2), (3, 4), (5, 6)]

    def test_metacharacters_are_literal(self):
        match = m.SubstringMatcher().compile("a.c")
        assert match.hit("a.c")
        assert not match.hit("abc")


class TestWildcardMatcher:

    def test_star_spans_any_run(self):
        match = m.WildcardMatcher().compile("a*a")
        assert match.hit("alpha")
        assert not match.hit("beta")

    def test_question_mark_is_exactly_one_character(self):
        assert m.WildcardMatcher().compile("a?pha").hit("alpha")
        assert not m.WildcardMatcher().compile("a?pha").hit("apha")

    def test_a_plain_word_still_matches_as_a_substring(self):
        assert m.WildcardMatcher().compile("lph").hit("alpha")

    def test_pattern_is_searched_not_anchored(self):
        assert m.WildcardMatcher().compile("l*h").hit("alpha")

    def test_has_wildcard(self):
        assert m.has_wildcard("a*b")
        assert m.has_wildcard("a?b")
        assert not m.has_wildcard("ab")


class TestMigemoDegradation:
    """Migemo may never subtract a match, whatever state the engine is in."""

    def test_falls_back_to_the_base_matcher_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(migemo, "get_regex", lambda q: None)
        match = m.with_migemo().compile("kensaku")
        assert match.hit("kensaku")
        assert not match.hit("beta")

    def test_short_query_is_gated(self, monkeypatch):
        seen = []
        monkeypatch.setattr(migemo, "_compiled",
                            lambda q, n: seen.append(q))
        assert migemo.get_regex("ke") is None
        assert seen == []

    def test_wildcards_bypass_migemo(self, monkeypatch):
        called = []
        monkeypatch.setattr(migemo, "get_regex",
                            lambda q: called.append(q))
        m.with_migemo().compile("ken*")
        assert called == []

    def test_base_hits_survive_even_if_migemo_misses(self, monkeypatch):
        import re
        monkeypatch.setattr(migemo, "get_regex",
                            lambda q: re.compile("(?!x)x"))  # never matches
        assert m.with_migemo().compile("alpha").hit("alpha")


@needs_migemo
class TestMigemo:

    def test_romaji_finds_kanji(self):
        assert m.with_migemo().compile("kensaku").hit("検索")

    def test_romaji_finds_hiragana_and_katakana(self):
        match = m.with_migemo().compile("kensaku")
        assert match.hit("けんさく")
        assert match.hit("ケンサク")

    def test_katakana_forms_pymigemo_omits(self):
        # pymigemo 0.0.1 stops at hiragana; keyhac.core.migemo adds the
        # katakana and half-width katakana forms C/Migemo would union in.
        assert m.with_migemo().compile("daunro").hit("ダウンロード")

    def test_the_typed_romaji_still_matches(self):
        assert m.with_migemo().compile("kensaku").hit("kensaku file")

    def test_non_matching_row_is_still_rejected(self):
        assert not m.with_migemo().compile("kensaku").hit("beta")

    def test_spans_cover_the_japanese_hit(self):
        assert m.with_migemo().compile("kensaku").spans("検索") == [(0, 2)]

    def test_ascii_only_hits_come_from_the_base_matcher(self):
        # The Migemo half reports nothing for a pure-ASCII hit; the union's
        # spans are the substring matcher's.
        regex = migemo.get_regex("kensaku")
        assert migemo.hits(regex, "kensaku") == []
