"""Migemo expansion - romaji finds Japanese (issue #106, discussion #112).

Migemo turns a romaji query into a regex matching the Japanese it could
spell: ``kensaku`` becomes ``(検索|けんさく|...|kensaku)``.  Its significance
is not speed.  Where an application's UI is localised, filtering a candidate
list by name is not *slower* without Migemo, it is **unavailable** - the user
cannot type the candidate's text without going through an input method, and
a non-activating candidate window cannot host an input method at all
(composition follows OS keyboard focus, which such a window by definition
never takes).

This is a port of XeFM's ``xefm/migemo_search.py``, which has shipped the
same integration since its discussion #332.  The load-bearing choices carry
over unchanged; each was paid for once already:

- **Engine: oguna's pymigemo** - pure Python, BSD-3, dictionary bundled in
  the wheel.  No native library, no separate dictionary placement, no GPL
  entanglement, and the same artifact ships through PyPI, the dmg and the
  Windows zip.
- **Runtime patch, pinned.** pymigemo 0.0.1 cannot load its dictionary on
  LP64 platforms (macOS, Linux): the reader builds ``array.array('L')`` for a
  32-bit on-disk format, and ``'L'`` is 8 bytes there.  :class:`_Array32`
  swaps the ``array`` binding inside the dictionary module before the engine
  is constructed - inert on Windows, and inert once upstream reads ``'I'``
  itself (https://github.com/oguna/pymigemo/pull/1, unmerged).  Pin the
  dependency so a new release cannot silently move the internals it touches.
- **Everything degrades to plain matching.**  Package missing, dictionary
  unreadable, an engine landmine (a lone ``s`` raises IndexError inside its
  bit vector), a regex the stdlib rejects: the answer is always ``None`` and
  the caller's own matching stands alone.  Migemo is unioned on top, never
  substituted - see ``keyhac.core.matcher.with_migemo``.
- **A minimum-length gate instead of a mode.**  Generating the regex for a
  1-2 character query can take seconds (measured: 2 s for ``c``) while 3+
  characters sits around 5 ms.  In an incremental filter that recompiles on
  every keystroke, the gate is not a refinement - it is what keeps the first
  two keystrokes from freezing the window.  Two romaji characters are barely
  one kana, so nothing useful is gated away.
"""

import array
import re
import unicodedata
from functools import lru_cache

from keyhac.core import log

logger = log.getLogger("Migemo")

#: Queries shorter than this are not expanded - see the module docstring.
MIN_LENGTH = 3

#: None: not tried yet.  False: tried and unavailable.  Else the engine.
_engine = None


class _Array32:
    """Drop-in for the ``array`` module inside pymigemo's dictionary reader:
    typecode ``'L'`` (32-bit in the file format, but 64-bit on LP64
    platforms) is served as the always-32-bit ``'I'``.  Every other typecode
    passes through, so the patch is inert wherever the bug is not."""

    @staticmethod
    def array(typecode, *args):
        if typecode == "L" and array.array("L").itemsize != 4:
            typecode = "I"
        return array.array(typecode, *args)


#: hiragana -> katakana for str.translate: the blocks are parallel
#: (ぁ..ゖ at U+3041 -> ァ..ヶ at U+30A1), plus the iteration marks ゝゞ.
_HIRA2KATA = {c: c + 0x60 for c in range(0x3041, 0x3097)}
_HIRA2KATA.update({0x309D: 0x30FD, 0x309E: 0x30FE})


def available() -> bool:
    """Whether the engine loaded.  Loads it on the first call."""
    return _load_engine() is not None


def _load_engine():
    """Import, patch and construct the engine on first use, so the ~50 ms
    dictionary read happens on the first gated query rather than at startup.
    Failure is cached as False: one warning, then permanent silence."""
    global _engine
    if _engine is None:
        try:
            # pymigemo imports as plain ``migemo`` - the same name atzm's
            # C/Migemo binding claims.  Reaching for the pure-Python
            # dictionary module first doubles as the identity check: on any
            # other package this import raises and Migemo stays off.
            from migemo import migemocompactdictionary as _dict_module
            _dict_module.array = _Array32
            import migemo as _migemo
            _engine = _migemo.Migemo()
            logger.info("Migemo dictionary loaded.")
        except Exception as e:
            logger.warning(f"Migemo unavailable ({type(e).__name__}: {e}); "
                           "candidate filtering uses plain matching only.")
            _engine = False
    return _engine if _engine else None


def _word_expansion(engine, word: str) -> str:
    """pymigemo's regex for one lowercased word, plus the katakana it
    forgets: C/Migemo unions hiragana->katakana (and half-width katakana)
    forms into every expansion, while pymigemo 0.0.1 stops at hiragana - so
    romaji could never find ダウンロード.  Longest form first, so a span
    covers the whole hit rather than a shorter alternative's prefix."""
    base = engine.query(word)
    forms = set()
    try:
        from migemo import characterconverter, romajiconverter
        readings = list(
            romajiconverter.convert_romaji_to_hiragana_predictively(word))
        readings.append(word)
        for hira in readings:
            kata = hira.translate(_HIRA2KATA)
            if kata == hira:
                continue
            forms.add(kata)
            han = characterconverter.zen2han(kata)
            if han != kata:
                forms.add(han)
    except Exception as e:
        # The base expansion still stands; only the katakana forms are lost.
        logger.warning(f"Migemo katakana forms failed for {word!r} "
                       f"({type(e).__name__}: {e}).")
    if not forms:
        return base
    alts = ([base] if base else []) + [
        re.escape(f) for f in sorted(forms, key=lambda s: (-len(s), s))]
    return "(?:" + "|".join(alts) + ")"


def _words_expansion(engine, words, min_length: int) -> str:
    """The concatenated expansion for a word split: each word expanded
    lowercased, words under the gate as escaped literals - expanding 1-2
    character words is the seconds-slow path, and inside a camel pattern
    (``abC``) they would dodge the whole-pattern gate."""
    return "".join(
        _word_expansion(engine, w.lower()) if len(w) >= min_length
        else re.escape(w)
        for w in words
    )


@lru_cache(maxsize=256)
def _compiled(query: str, min_length: int):
    """The compiled regex for `query`, or None.  Cached per (query, gate):
    generation is the expensive step, matching with the result is cheap.

    Each word is expanded *lowercased*, unlike pymigemo's built-in query: its
    romaji conversion is case-sensitive, so ``Mudai`` would expand without
    無題, and an all-caps word expands to a degenerate alternative
    (``KENSAKU`` -> ``(ＫＥＮＳＡＫＵ|KE)``) that under IGNORECASE floods the
    result with every row containing "ke".

    A mixed-case query is ambiguous - ``TenkiYohou`` is two camel words, but
    ``Sa-bisu`` is one word typed with a capital, whose camel split demands a
    literal ``Sa`` no Japanese text contains.  Migemo is additive, so both
    readings are expanded and unioned."""
    engine = _load_engine()
    if engine is None:
        return None
    try:
        expansion = _words_expansion(engine, engine.parse_query(query),
                                     min_length)
        lower = query.lower()
        if lower != query:
            whole = _words_expansion(engine, engine.parse_query(lower),
                                     min_length)
            if whole and whole != expansion:
                expansion = (f"(?:{expansion})|(?:{whole})" if expansion
                             else whole)
        if not expansion:
            # An all-whitespace query: re.compile("") matches everything,
            # which would light up every row.
            return None
        return re.compile(expansion, re.IGNORECASE)
    except Exception as e:
        logger.warning(f"Migemo query failed for {query!r} "
                       f"({type(e).__name__}: {e}); matching it plainly.")
        return None


def get_regex(query: str):
    """The Migemo regex for one query, or None when Migemo does not apply -
    empty query, shorter than :data:`MIN_LENGTH`, or the engine failed.
    Callers union a non-None regex with their own matching, never replace
    it."""
    if not query or len(query) < MIN_LENGTH:
        return None
    return _compiled(query, MIN_LENGTH)


def hits(regex, text: str) -> list:
    """`(start, end)` spans where `regex` adds a match in `text`, NFC-folded.

    Only spans containing a non-ASCII character count: an ASCII hit is
    already the caller's own matching (the expansion keeps the typed romaji
    as an alternative), and pymigemo's expansion of an ASCII word can be
    wrong-broad.  macOS serves NFD strings while Migemo emits NFC kana, so
    the haystack is normalised - which can shift offsets, so the spans are
    only trustworthy for highlighting when the text was already NFC.  Rows
    drawn from an accessibility tree on macOS are the case to watch.
    """
    try:
        folded = unicodedata.normalize("NFC", text)
        return [m.span() for m in regex.finditer(folded)
                if m.end() > m.start() and not m.group().isascii()]
    except Exception:
        return []
