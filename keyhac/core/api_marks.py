"""Marks on one API: Preview and deprecated (#278).

Additive-only freezes an API the day it ships, and these two loosen that for one
API at a time rather than for the whole surface:

- **``@preview``**: free to change in any release until the mark is deleted, so a
  new API need not be right the first time. Stabilizing it is deleting the mark.
- **``@deprecated("use X")``**: on its way out. It keeps working, and it is removed
  no earlier than the next major release. Its first use from outside Keyhac is
  logged as a warning, once per API per session. :class:`deprecated_alias` is the
  same for a member that moved to a new name.

The mark sits on the definition, where whoever reads the code meets it, and goes
on a function, a method, a property's getter (under ``@property``) or a class. It
opens the docstring, so ``help()`` and the generated reference say the same as the
source, and it records the API in a registry, which is what
``tools/api_snapshot.py`` and the check on what extensions import read. Names are
as the reference spells them: ``Keymap.define_keytable``. An entry for a class covers
its members.

Ported from XeFM's ``xefm/preview.py``, less what comes from its separate API layer
and ``API_VERSION``. Nothing here tells Preview use at run time: a Preview API says
so where it is read, and an entry point logging every call from a config would
only be noise.
"""

from __future__ import annotations

import functools
import inspect
import sys
from typing import Any, Callable, TypeVar

from keyhac.core import log

T = TypeVar("T")

logger = log.getLogger("API")

#: The note a Preview API's docstring opens with.
PREVIEW_NOTE = "Preview — may change in any release."

#: Every Preview API by name.
PREVIEW_APIS: set[str] = set()

#: Every deprecated API by name, mapped to what to use instead.
DEPRECATED_APIS: dict[str, str] = {}

#: ``(module, api)`` pairs already logged, so each is said once per session.
_recorded: set[tuple[str, str]] = set()


def _open_docstring(obj, note: str) -> None:
    obj.__doc__ = f"{note}\n\n{obj.__doc__}" if obj.__doc__ else note


def preview(obj: T) -> T:
    """Mark a function, method, property getter or class as Preview."""
    PREVIEW_APIS.add(obj.__qualname__)
    _open_docstring(obj, PREVIEW_NOTE)
    return obj


def deprecated(instead: str) -> Callable[[T], T]:
    """Mark an API as on its way out, saying what replaces it:
    ``@deprecated("use keymap.ui.find")``. A class's first use is constructing one."""

    def mark(obj: T) -> T:
        api = obj.__qualname__
        DEPRECATED_APIS[api] = instead
        _open_docstring(obj, f"Deprecated — {instead}. Removed in a later major release.")
        if inspect.isclass(obj):
            original = obj.__init__

            @functools.wraps(original)
            def init(*args, **kwargs):
                _record_use(api, sys._getframe(1).f_globals.get("__name__", ""), instead)
                original(*args, **kwargs)

            obj.__init__ = init
            return obj

        @functools.wraps(obj)
        def recorded(*args, **kwargs):
            _record_use(api, sys._getframe(1).f_globals.get("__name__", ""), instead)
            return obj(*args, **kwargs)

        return recorded  # type: ignore[return-value]

    return mark


class deprecated_alias:
    """The old name of a member that moved to ``new``, kept until the next major.

    ``on_key_event = deprecated_alias("_on_key_event", "it is internal wiring")``
    in the class body. It has to keep working in every way a config can use the old
    name, not only for calling it, so reading, assigning to and deleting the old name
    all act on the new one. A subclass overriding the old name is the fourth way, and
    needs :func:`follow_renamed_overrides` from the class's ``__init_subclass__``, so
    that Keyhac's own calls, which use the new name, reach the override.

    Assigning to the old name on the class itself replaces this descriptor rather
    than the new member. Nothing can see that without a metaclass, and a config holds
    an instance.
    """

    def __init__(self, new: str, instead: str):
        self.new = new
        self.instead = instead

    def __set_name__(self, owner, name):
        self.owner = owner
        self.api = f"{owner.__qualname__}.{name}"
        self.old = name
        DEPRECATED_APIS[self.api] = self.instead
        self.__doc__ = f"Deprecated — {self.instead}. Removed in a later major release."

    def _record(self):
        _record_use(self.api, sys._getframe(2).f_globals.get("__name__", ""), self.instead)

    def __get__(self, obj, objtype=None):
        self._record()
        if obj is None:
            return getattr(objtype, self.new)
        # A class before this one in the MRO that defines the old name would have
        # been found first. So getting here despite one means super() from that
        # override, and the new name now leads back to the override: go to the
        # implementation below it instead.
        mro = type(obj).__mro__
        overridden = any(self.old in vars(cls) for cls in mro[:mro.index(self.owner)])
        if overridden:
            return inspect.getattr_static(self.owner, self.new).__get__(obj, type(obj))
        return getattr(obj, self.new)

    def __set__(self, obj, value):
        self._record()
        setattr(obj, self.new, value)

    def __delete__(self, obj):
        self._record()
        delattr(obj, self.new)


def follow_renamed_overrides(cls: type) -> None:
    """Make a subclass's override of a renamed member reach Keyhac's own calls. Call
    it from ``__init_subclass__``."""
    for name, value in list(vars(cls).items()):
        # Any class above: an override of an override has a plain function next.
        alias = next((vars(base)[name] for base in cls.__mro__[1:]
                      if isinstance(vars(base).get(name), deprecated_alias)), None)
        if alias is not None and not isinstance(value, deprecated_alias):
            _record_use(alias.api, cls.__module__, alias.instead)
            setattr(cls, alias.new, value)


def _record_use(api: str, module: str, instead: str) -> None:
    """Log, once per session, that ``module`` used the deprecated ``api``. Keyhac's
    own modules are not reported: a config cannot do anything about those."""
    if module == "keyhac" or module.startswith("keyhac."):
        return
    if (module, api) in _recorded:
        return
    _recorded.add((module, api))
    logger.warning(f"{module} uses deprecated API {api} - {instead}. It will be "
                   f"removed in a later major release.")


def is_preview(name: str) -> bool:
    """Whether the API called ``name`` is Preview, itself or through its class."""
    parts = name.split(".")
    return any(".".join(parts[:i]) in PREVIEW_APIS for i in range(1, len(parts) + 1))


def is_deprecated(name: str) -> str | None:
    """What replaces the API called ``name``, if it is deprecated, itself or through
    its class; else None."""
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        instead = DEPRECATED_APIS.get(".".join(parts[:i]))
        if instead is not None:
            return instead
    return None


def warn_unless_callable_with(callback: Any, count: int, what: str) -> None:
    """Say at load time that ``callback`` cannot take the ``count`` arguments it will
    be called with, rather than when someone first presses the key.

    A warning, not a refusal: a config that does this today has a key that fails
    when pressed, and everything else in the file still works. A callable that
    cannot be inspected gets the benefit of the doubt.
    """
    if callback is None:
        return
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return
    try:
        signature.bind(*[None] * count)
    except TypeError:
        name = getattr(callback, "__qualname__", repr(callback))
        logger.warning(f"{what} is called with {count} argument(s), and {name}{signature} "
                       f"cannot take that many, so it will fail when it is called.")
