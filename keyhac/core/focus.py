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


class FocusCondition:
    """Condition deciding whether a key table is active for the current focus.

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
        self.focus_path_pattern = focus_path_pattern
        self.custom_condition_func = custom_condition_func
        self.app = app
        self.title = title
        self.class_name = class_name

    def check(self, focus: Focus | None) -> bool:

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
                print()
                logger.error(f"Running custom focus condition function failed:\n{traceback.format_exc()}")
                return False

        return True

    @staticmethod
    def has_condition(**kwargs) -> bool:
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
