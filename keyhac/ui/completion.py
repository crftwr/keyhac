"""Tab completion for a single-line field, and the state a candidate list needs.

Ported in shape from XeFM's `xefm/completion.py`, which solved this for file
paths: the longest common prefix goes in on Tab, a list opens when more than
one match remains, and the field is the only thing the controller writes to.
Keeping the same shape matters more than sharing the code - the two are
different applications, and this one completes *scope names*, which are a
handful of strings already in memory rather than a directory listing that can
stall.  So there is no threaded fetch here, and no need of one.

What is deliberately different: **a completed scope does not stay in the
field.**  A path completes into the text because the text is the answer; a
scope completes into a *change of what the window is showing*, so committing
one takes the token back out of the query.  That is what keeps the rest of
the query alive across the switch - `save menu` + Tab leaves `save` behind and
moves to Menus, which is the invariant the cycling switcher had and a typed
sigil could not offer.
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


class Completion:
    """Tab-completion state for one field.

    The host forwards keys to `on_tab`, `move_focus`, `accept` and `dismiss`,
    calls `on_text_changed` after an ordinary edit, and reads `active`,
    `candidates` and `focused_index` to draw the list.  Nothing here draws.

    `candidates_for(token)` is the seam: it answers what could be meant, in
    the order they should be offered.
    """

    def __init__(self, edit, candidates_for):
        self.edit = edit
        self.candidates_for = candidates_for
        #: Whether a list is open and taking the arrow keys.
        self.active = False
        self.candidates: list[str] = []
        #: -1 while nothing is highlighted, which is how the list opens: the
        #: first Tab proposes nothing, the next one steps into the list.
        self.focused_index = -1
        #: Where the token being completed began.  Remembered rather than
        #: recomputed, because what gets inserted may not be one word:
        #: "Tools only" completed from "too" is two, and asking afterwards
        #: which word the caret is in answers "only" and takes back half a
        #: name.  None when nothing has been completed since the last edit.
        self._origin = None

    # --- keys -------------------------------------------------------------

    def on_tab(self, forward: bool = True) -> bool:
        """Advance the completion.  True when Tab was spent here.

        Closed, it completes: the common prefix goes in, and a list opens if
        more than one thing could still be meant.  Open, it steps the
        highlight - which is the old cycling gesture, except that it now runs
        over *what matches* rather than over everything, and a query of one
        or two letters usually leaves two or three.
        """
        if self.active:
            self.move_focus(1 if forward else -1)
            return True
        start, _end = token_span(self.edit.text, self.edit.cursor)
        token = self.token()
        candidates = list(self.candidates_for(token))
        if not candidates:
            self.dismiss()
            return False
        self._origin = start
        common = common_prefix(candidates)
        if len(common) > len(token) and common.lower().startswith(token.lower()):
            self._replace_token(common)
        self.candidates = candidates
        self.focused_index = -1
        # One candidate is not a list to choose from, it is an answer; the
        # host commits it and never sees `active`.
        self.active = len(candidates) > 1
        return True

    def open_all(self) -> bool:
        """Offer everything, whatever is typed.  What a user reaching for
        "just show me" presses, and the reason the window needs no permanent
        list of scopes: it appears when asked for."""
        candidates = list(self.candidates_for(""))
        if not candidates:
            return False
        self.candidates = candidates
        self.focused_index = -1
        self.active = True
        return True

    def on_text_changed(self) -> None:
        """Narrow or widen an open list after an ordinary edit, and close it
        when nothing matches.  Typing clears the highlight - the arrows are
        what moves it."""
        if not self.active:
            return
        self._origin = token_span(self.edit.text, self.edit.cursor)[0]
        candidates = list(self.candidates_for(self.token()))
        if not candidates:
            self.dismiss()
            return
        self.candidates = candidates
        self.focused_index = -1

    def move_focus(self, delta: int) -> None:
        """Step the highlight, wrapping.  From nothing highlighted, forward
        lands on the first and backward on the last."""
        if not self.active or not self.candidates:
            return
        count = len(self.candidates)
        if self.focused_index < 0:
            self.focused_index = 0 if delta > 0 else count - 1
        else:
            self.focused_index = (self.focused_index + delta) % count

    def accept(self) -> str | None:
        """What the user settled on, or None when there was nothing open.

        With nothing highlighted this answers the first candidate rather than
        refusing: a list is open only because the user asked for one, so
        Enter has an obvious thing to mean and no reason to dead-end.
        """
        if not self.active or not self.candidates:
            return None
        index = self.focused_index if self.focused_index >= 0 else 0
        return self.candidates[index]

    def dismiss(self) -> None:
        """Close the list without choosing.  The typed token stays in the
        field - it was the user's text before it was a prefix."""
        self.active = False
        self.candidates = []
        self.focused_index = -1

    # --- the field --------------------------------------------------------

    def token(self) -> str:
        """The word the caret is in."""
        start, end = token_span(self.edit.text, self.edit.cursor)
        return self.edit.text[start:end]

    def take_token(self) -> None:
        """Remove what was being completed, and any space it left behind.

        Called when a completion is committed to something that is not text -
        a scope - so the field goes back to holding only the query.

        It works from the remembered start, not from the word under the
        caret: a completed name may contain spaces, and by this point the
        field holds the name rather than the prefix that was typed.
        """
        text = self.edit.text
        start = self._origin if self._origin is not None else \
            token_span(text, self.edit.cursor)[0]
        end = max(start, min(self.edit.cursor, len(text)))
        while start > 0 and text[start - 1].isspace():
            start -= 1
        self.edit.text = text[:start] + text[end:]
        self.edit.cursor = start
        self.edit._anchor = None
        self._origin = None

    def _replace_token(self, replacement: str) -> None:
        text = self.edit.text
        start, end = token_span(text, self.edit.cursor)
        self.edit.text = text[:start] + replacement + text[end:]
        self.edit.cursor = start + len(replacement)
        self.edit._anchor = None
