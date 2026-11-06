"""Marks on one API: Preview and deprecated (#278).

Additive-only freezes an API the day it ships, and these two loosen that for one
API at a time rather than for the whole surface:

- **``@preview``**: free to change in any release until the mark is deleted, so a
  new API need not be right the first time. Stabilizing it is deleting the mark.
- **``@deprecated("use X")``**: on its way out. It keeps working, and it is removed
  no earlier than the next major release. Its first use from outside Keyhac is
  logged as a warning, once per API per session.

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
