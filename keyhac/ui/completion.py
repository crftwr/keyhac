"""The two things Tab completion needs of a single-line field.

Taken in shape from XeFM's `xefm/completion.py`, which solved this for file
paths, and kept down to what the chooser turned out to need. XeFM's version
also carries a controller: a candidate list, a highlight, apply and dismiss,
and a threaded fetch for a directory that can stall.

None of that is here, and the reason is worth recording. The chooser
completes a **source name** typed after `@`, and those names are already on
screen - the badge beside every row says which source produced it. A
candidate list would show the user what they are looking at, and cover it to
do so. So Tab only ever lengthens the token in the field: it commits nothing,
opens no mode, and has nothing to dismiss. Where it cannot lengthen, the
badges are the list.
"""


def common_prefix(candidates) -> str:
    """The longest string every candidate starts with.

    The most Tab can insert without guessing.  Empty for no candidates; the
    whole string for one.

    Case-insensitive, unlike XeFM's, and that is the difference between a
    file path and a name: `men` should reach `Menus`.  The *comparison*
    ignores case; what comes back is cut from a real candidate, so the
    inserted text carries the name's own capitals.
    """
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]
    shortest = min(len(c) for c in candidates)
    length = 0
    while length < shortest:
        here = candidates[0][length].lower()
        if any(c[length].lower() != here for c in candidates[1:]):
            break
        length += 1
    return candidates[0][:length]


def token_span(text: str, cursor: int) -> tuple[int, int]:
    """Where the word under the caret starts and ends.

    Whitespace-delimited and taken from the caret backwards, so completion
    acts on what is being typed and leaves the rest of the query alone.  That
    is the whole reason the query survives: `save menu` completes `menu` and
    keeps `save`.
    """
    cursor = max(0, min(cursor, len(text)))
    start = cursor
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    return start, cursor
