"""Where candidates come from (discussion #112).

A source is a value, not a subclass.  That distinction is the whole point:
while the only way to offer a new kind of row was to override a method, every
new capability cost a whole action class *and a hotkey to reach it* - and a
hotkey is the scarce resource here, not code.  As a value, several sources go
into one window and one key reaches all of them.

Two shapes, because two things are being named:

- **what the rows are** - `candidates()`, rebuilt on each invocation, since
  anything read from the screen is stale the moment it is cached;
- **what choosing one does** - `on_chosen()`, declared once per source
  because rows from one source almost always do the same kind of thing.  A
  row that differs sets `Candidate.action` and wins.

`name` exists for the unified window, which shows each row's source beside
it: with clipboard entries, windows and on-screen controls in one list, a row
without its provenance is a guess.  Deliberately on the source and not on the
candidate - the view already knows which source produced which row, so
copying it onto every candidate would be storing what is already known.
"""

from typing import Any, Callable

from keyhac.core.candidate import Candidate


class Source:
    """A named set of candidates, and what choosing one does."""

    #: Shown beside each row when more than one source is in the window.
    name: str = ""

    def candidates(self) -> list[Candidate]:
        """The rows this source offers *right now*.  Override this.

        Called on every invocation rather than cached: a source reading the
        screen - the windows that exist, the controls in the front window -
        is describing something that has already moved on by the time it is
        asked again.
        """
        return []

    def on_chosen(self, candidate: Candidate, modifier_flags: int) -> None:
        """Act on the chosen row.  Override this.

        Args:
            candidate: The row the user picked.
            modifier_flags: Modifiers held at the moment of choosing, as a
                bit mask - Shift-Enter is how the clipboard sources tell
                "copy this" from "paste it".
        """

    def choose(self, candidate: Candidate, modifier_flags: int) -> None:
        """Run the candidate's own action if it has one, else `on_chosen`.

        lazydocs: ignore
        """
        if candidate.action is not None:
            candidate.action(modifier_flags)
        else:
            self.on_chosen(candidate, modifier_flags)

    def __repr__(self):
        return f"{type(self).__name__}({self.name!r})"


class CallableSource(Source):
    """A source built from a plain callable, so anything that can produce a
    list can be one without subclassing - SSH hosts, git branches, records
    out of a line-of-business system.

    ```python
    branches = CallableSource(git_branches, "Branches", on_chosen=checkout)
    ```

    The callable returns `Candidate` objects, or the `(icon, label, *rest)`
    tuples `ChooserAction.list_items` has always returned - those are adapted,
    and `on_chosen` then receives a candidate whose payload is the tuple.
    """

    def __init__(self, produce: Callable[[], Any], name: str = "",
                 on_chosen: Callable[[Candidate, int], None] = None):
        self._produce = produce
        self.name = name
        self._on_chosen = on_chosen

    def candidates(self) -> list[Candidate]:
        """lazydocs: ignore"""
        return [Candidate.from_item(item) for item in self._produce()]

    def on_chosen(self, candidate: Candidate, modifier_flags: int) -> None:
        """lazydocs: ignore"""
        if self._on_chosen is not None:
            self._on_chosen(candidate, modifier_flags)


def as_source(source: Any, name: str = "",
              on_chosen: Callable[[Candidate, int], None] = None) -> Source:
    """Coerce a `Source`, or a bare callable, into a `Source`.

    A `Source` is returned untouched, `on_chosen` included: it already says
    what choosing one of its rows does, and overriding that from outside is
    how a source ends up meaning something different depending on which
    window it was opened from.  A bare callable has no such opinion, so it
    takes the one it is given.

    lazydocs: ignore
    """
    if isinstance(source, Source):
        return source
    return CallableSource(source, name, on_chosen)


class Scope:
    """A named set of sources the candidate window can switch between.

    One key opens the window; Tab and Shift-Tab move along the cycle, and the
    query survives the move - type `kensaku`, then look for it somewhere else
    without retyping it. That is the thing a typed prefix (`>`, `@`) cannot
    do, and the reason the switch is a key rather than a sigil. The other
    reason is that with Migemo the query alphabet is exactly ASCII, so a sigil
    sits in the middle of what the user is trying to type.

    Scopes are also how an *expensive* source stays affordable. A source that
    walks the accessibility tree costs a real traversal every time the window
    opens; put it in its own scope and it is paid for only when the user asks
    for it, instead of on every invocation of a merged everything-scope.

    ```python
    keymap_global["Fn-P"] = ShowCandidates([
        Scope("All", [clipboard, snippets, windows]),
        Scope("Clipboard", [clipboard, snippets]),
        Scope("Windows", [windows]),
    ])
    ```
    """

    def __init__(self, name: str, sources):
        """Build a scope.

        Args:
            name: Shown in the window while this scope is the current one.
            sources: The sources it draws from - `Source` objects, plain
                callables, or a mix.
        """
        self.name = name
        listed = sources if isinstance(sources, (list, tuple)) else [sources]
        self.sources = [as_source(s) for s in listed]

    def __repr__(self):
        return f"Scope({self.name!r}, {len(self.sources)} sources)"
