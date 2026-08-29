"""Focus conditions.

Unifies keyhac-mac's FocusCondition (focus_path_pattern / custom function)
with keyhac-win's WindowKeymap matching (exe / class / title), behind the
portable Focus snapshot defined in keyhac.platform.base.
"""

import fnmatch
import traceback
from typing import Callable

from keyhac.platform.base import Focus
from keyhac.core import log

logger = log.getLogger("Focus")


def _match_any(value: str, pattern: str) -> bool:
    """Case-insensitive fnmatch with '|' alternation."""
    value = value.lower()
    return any(fnmatch.fnmatch(value, p.strip().lower()) for p in pattern.split("|"))


def match_app_name(name: str | None, pattern: str) -> bool:
    """Whether an application name matches an `app=` pattern.

    The app= half of match_window_fields, for a caller holding a bare name
    rather than a Window - the running-application list, which has no windows
    to be matched against.
    """
    name = (name or "").lower().removesuffix(".exe")
    pattern = "|".join(p.strip().removesuffix(".exe") for p in pattern.split("|"))
    return bool(name) and _match_any(name, pattern)


def match_window_fields(window, app: str = None, title: str = None,
                        class_name: str = None) -> bool:
    """Whether a Window matches the given patterns (all specified must match).

    Shared with FocusCondition so that `define_keytable(app=...)` and
    `find_window(app=...)` cannot mean different things - same fnmatch, same
    '|' alternation, same ".exe"-optional app names.
    """
    if app is not None:
        if not match_app_name(window.app_name, app):
            return False
    if title is not None:
        if window.title is None or not _match_any(window.title, title):
            return False
    if class_name is not None:
        if window.class_name is None or not _match_any(window.class_name, class_name):
            return False
    return True


class FocusCondition:
    """Condition deciding whether a key table is active for the current focus.

    ``keymap.define_keytable()`` builds one from the focus arguments it is
    given, so configurations do not normally construct it themselves.

    All specified conditions must match (AND).  Within `app`/`title`/
    `class_name` patterns, "|" separates alternatives (OR) and fnmatch
    wildcards (*, ?, []) are available.
    """

    def __init__(self,
                 focus_path_pattern: str = None,
                 custom_condition_func: Callable[[Focus], bool] = None,
                 app: str = None,
                 title: str = None,
                 class_name: str = None):
        """Build a focus condition.

        Args:
            focus_path_pattern: Focus path pattern with wildcards.
            custom_condition_func: A function receiving the current Focus and
                returning whether the condition holds.
            app: Application name pattern (".exe" optional on Windows).
            title: Window title pattern.
            class_name: Win32 window class name pattern (Windows only).
        """
        self.focus_path_pattern = focus_path_pattern
        self.custom_condition_func = custom_condition_func
        self.app = app
        self.title = title
        self.class_name = class_name
        # A failing custom condition fails again on every focus change, so the
        # traceback is reported once per configuration load.
        self._error_reported = False

    def check(self, focus: Focus | None) -> bool:
        """Whether the condition holds for this focus snapshot.

        lazydocs: ignore
        """

        if self.focus_path_pattern:
            if not focus or not focus.path or not fnmatch.fnmatch(focus.path, self.focus_path_pattern):
                return False

        if self.app:
            if not focus or not focus.app_name:
                return False
            # Accept patterns with or without ".exe" (Windows migration aid)
            app_name = focus.app_name.lower().removesuffix(".exe")
            pattern = "|".join(p.strip().removesuffix(".exe") for p in self.app.split("|"))
            if not _match_any(app_name, pattern):
                return False

        if self.title:
            if not focus or focus.window_title is None:
                return False
            if not _match_any(focus.window_title, self.title):
                return False

        if self.class_name:
            if not focus or focus.class_name is None:
                return False
            if not _match_any(focus.class_name, self.class_name):
                return False

        if self.custom_condition_func:
            try:
                if not focus or not self.custom_condition_func(focus):
                    return False
            except Exception:
                if not self._error_reported:
                    self._error_reported = True
                    print()
                    logger.error(
                        f"Running custom focus condition function failed "
                        f"(reported once per configuration load):"
                        f"\n{traceback.format_exc()}")
                return False

        return True

    @staticmethod
    def has_condition(**kwargs) -> bool:
        """Whether any focus condition argument was given.

        lazydocs: ignore
        """
        return any(v is not None for v in kwargs.values())


# Transliteration used when building focus path strings, so that fnmatch
# special characters in titles cannot break patterns (from keyhac-mac).
FOCUS_PATH_TRANS_TABLE = str.maketrans({
    "(": "<",
    ")": ">",
    "/": "-",
    "*": "-",
    "?": "-",
    "[": "<",
    "]": ">",
    ":": "-",
    "\n": " ",
    "\t": " ",
})
