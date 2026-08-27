"""The unit a candidate source hands a candidate view (discussion #112).

The Chooser filters tuples of ``(icon, label, *payload)`` today, which is
enough for the three clipboard actions and for nothing else: a list of
strings can only ever be consumed by a list.  The views under consideration
need more - an overlay needs a screen rectangle to draw a label over, a
key-binding reference needs the key expression and the table it came from,
and any view that assigns short labels needs an identity so those labels can
be stable across invocations.

So a source yields :class:`Candidate` objects, and the tuple form stays
supported by :meth:`Candidate.from_item` - the chooser converts on the way
in and hands the original tuple back on selection, so every ``ChooserAction``
already written keeps working unchanged.

``provenance`` is the field that is not obvious.  An icon-only button often
exposes no name, only an ``AXDescription`` or a ``HelpText``; recording
*which* attribute the display text came from turns the candidate window into
an authoring aid, because an element reachable only by ``HelpText`` cannot be
found by ``find(name=...)`` in an Action either.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Candidate:
    """One row a source offers a view.

    Attributes:
        match_text: What the matcher runs against.  Defaults to `display`.
        display: What the user sees, which may differ from the match text -
            a file candidate can match on its full path and display its
            basename.
        payload: What the consumer wants back: a string to paste, a `UINode`,
            a callable, a window handle.
        identity: Stable across invocations where the source can manage it,
            so a view assigning short labels can keep giving the same
            candidate the same label.  None when the source has nothing
            stable to offer.
        icon: A short glyph shown before the display text.
        rect: Screen rectangle `(x, y, w, h)` in puikit's portable top-left
            coordinates, for views that draw over the real element.
        provenance: Where `display` came from, when that is not simply the
            element's name - `"description"`, `"identifier"`, `"position"`.
        extras: Anything else the source and its view agree on (a key
            expression, a role hint).
    """

    display: str
    payload: Any = None
    match_text: str = None
    identity: str = None
    icon: str = ""
    rect: tuple = None
    provenance: str = None
    extras: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.match_text is None:
            # frozen dataclass: the default has to be filled in through
            # object.__setattr__, which is what the stdlib recommends.
            object.__setattr__(self, "match_text", self.display)

    @property
    def label(self) -> str:
        """Icon and display text as one line, the way a list view draws it."""
        return f"{self.icon} {self.display}" if self.icon else self.display

    @staticmethod
    def from_item(item) -> "Candidate":
        """Adapt the `(icon, label, *payload)` tuple `ChooserAction.list_items`
        returns.  The whole tuple becomes the payload, so `on_chosen` still
        receives exactly what it received before."""
        if isinstance(item, Candidate):
            return item
        return Candidate(display=item[1], icon=item[0], payload=item)
