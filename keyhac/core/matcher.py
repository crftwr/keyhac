"""Pluggable query matching for the candidate window (discussion #112).

The same window filters material with very different properties - clipboard
text, snippet names, accessibility labels, key expressions - so how a query
is matched has to be a parameter, not a hard-coded ``in``.

**Shape.** A matcher compiles a query *once* into a :class:`Match`, and the
view applies that to every candidate.  ``(query, candidate) -> bool`` would
force per-candidate work that Migemo in particular does not need: one romaji
query becomes one alternation regex, and building it is the expensive step
(measured in XeFM: seconds for a 1-char query, ~5 ms from 3 chars up), while
running it over a candidate is cheap.  Compiling once also means the matched
spans come back with the hit, so a view can highlight what matched.

**Spans are not free in the UI, though.** puikit's ``TableView`` /
``MarkdownView`` / ``JsonView`` take a ``search_matcher``; ``ListView`` does
not, so drawing the highlight in the chooser's list needs an additive puikit
addition mirroring the three that exist.  :meth:`Match.spans` is here so that
addition is a wiring change and not a redesign.

**Migemo composes, it does not replace.**  :func:`with_migemo` unions a
Migemo regex onto an existing matcher, so an engine quirk can only ever add
matches, never remove them - the rule XeFM arrived at the hard way (its
predecessor gated Migemo behind a mixed-case heuristic and lost matches
plain search would have found).
"""

import re

from keyhac.core import log

logger = log.getLogger("Matcher")

#: Characters that make a query a wildcard pattern.
_GLOB_CHARS = ("*", "?")


class Match:
    """A compiled query.  Views call :meth:`hit` per candidate, and
    :meth:`spans` only for the rows they are about to draw."""

    def hit(self, text: str) -> bool:
        """Whether `text` matches the query this was compiled from."""
        raise NotImplementedError

    def spans(self, text: str) -> list:
        """`(start, end)` character spans of what matched, for highlighting.
        An empty list is always a valid answer - a matcher that cannot
        localise its hit simply does not offer one."""
        return []


class _MatchAll(Match):
    """The empty query: every candidate passes, nothing is highlighted."""

    def hit(self, text: str) -> bool:
        return True


MATCH_ALL = _MatchAll()


class _RegexMatch(Match):
    """Every one of `patterns` must be found somewhere in the text (the
    multi-word AND the chooser has always used)."""

    def __init__(self, patterns):
        self._patterns = patterns

    def hit(self, text: str) -> bool:
        return all(p.search(text) for p in self._patterns)

    def spans(self, text: str) -> list:
        out = []
        for p in self._patterns:
            out.extend(m.span() for m in p.finditer(text) if m.end() > m.start())
        return sorted(out)


class Matcher:
    """Compiles a query string into a :class:`Match`."""

    def compile(self, query: str) -> Match:
        raise NotImplementedError


class SubstringMatcher(Matcher):
    """Case-insensitive substring, whitespace-separated words ANDed together.

    What the chooser has always done, kept as the default so no shipped
    behaviour changes.
    """

    def compile(self, query: str) -> Match:
        words = [w for w in query.split() if w]
        if not words:
            return MATCH_ALL
        return _RegexMatch([re.compile(re.escape(w), re.IGNORECASE)
                            for w in words])


class WildcardMatcher(Matcher):
    """Substring matching with 1.x's ``*`` and ``?`` wildcards.

    ``*`` stands for any run of characters and ``?`` for exactly one; a word
    with neither behaves exactly as under :class:`SubstringMatcher`.  The
    pattern is *searched* rather than anchored, so ``ab*yz`` finds a row
    whose label merely contains that shape - matching how an incremental
    filter reads, and how keyhac-win 1.x's list window behaved.
    """

    def compile(self, query: str) -> Match:
        words = [w for w in query.split() if w]
        if not words:
            return MATCH_ALL
        return _RegexMatch([re.compile(_wildcard_regex(w), re.IGNORECASE)
                            for w in words])


def _wildcard_regex(word: str) -> str:
    return "".join("." if c == "?" else ".*" if c == "*" else re.escape(c)
                   for c in word)


def has_wildcard(query: str) -> bool:
    """Whether `query` uses ``*`` or ``?``."""
    return any(c in query for c in _GLOB_CHARS)


class _UnionMatch(Match):
    """A hit in either half.  Used to add Migemo without ever subtracting."""

    def __init__(self, base: Match, extra: Match):
        self._base = base
        self._extra = extra

    def hit(self, text: str) -> bool:
        return self._base.hit(text) or self._extra.hit(text)

    def spans(self, text: str) -> list:
        return sorted(self._base.spans(text) + self._extra.spans(text))


class _MigemoMatcher(Matcher):
    """`base`, unioned with the Migemo expansion of the query.

    Wildcards bypass Migemo entirely: the generated regex would collide with
    the pattern's own metacharacters, so a query using ``*`` or ``?`` keeps
    exactly the wildcard semantics it asked for.
    """

    def __init__(self, base: Matcher):
        self._base = base

    def compile(self, query: str) -> Match:
        base = self._base.compile(query)
        if has_wildcard(query):
            return base
        from keyhac.core import migemo
        regex = migemo.get_regex(query)
        if regex is None:
            return base
        return _UnionMatch(base, _MigemoRegexMatch(regex))


class _MigemoRegexMatch(Match):
    """A Migemo regex, counted only where it hits non-ASCII.

    An ASCII-only hit is already the base matcher's business - the expansion
    keeps the typed romaji as one of its alternatives - and pymigemo's
    expansion of an ASCII word can be wrong-broad (``x25`` -> ``(x2|...)``,
    which would make ``x25`` "match" ``x24``).
    """

    def __init__(self, regex):
        self._regex = regex

    def _hits(self, text: str):
        from keyhac.core import migemo
        return migemo.hits(self._regex, text)

    def hit(self, text: str) -> bool:
        return bool(self._hits(text))

    def spans(self, text: str) -> list:
        return self._hits(text)


def with_migemo(base: Matcher = None) -> Matcher:
    """`base` (:class:`SubstringMatcher` by default) plus Migemo.

    Romaji finds Japanese, which is the difference between filtering being
    slower and it being unavailable: in a localised UI the user cannot type a
    candidate's text at all without going through an input method.  Degrades
    to `base` alone whenever Migemo does not apply - package missing,
    dictionary unreadable, query too short, query using wildcards.
    """
    return _MigemoMatcher(base if base is not None else SubstringMatcher())


#: The matcher a candidate view uses when its caller names none.
DEFAULT_MATCHER = SubstringMatcher()
