"""Which public names go where, derived from `keyhac.__all__`.

`__all__` is the list of what a config or an extension can import. It is in the
order doc/config-api.md presents them. What cannot be derived from it is here:
the objects that are public but reached rather than imported, and which imported
names belong to the action API.

Separate from generate_api_reference.py because that needs lazydocs, which the
tests do not install. tests/test_public_api.py fails when these lists, the skill's
import lists and `__all__` disagree.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import keyhac  # noqa: E402

#: Public, but a config only ever receives it: keymap.get_active_window().
REACHED_BY_CONFIG = ["Window"]

#: Public, but reached through `keymap.ui`: the object itself, and the values
#: its verbs take, as `ui.Locator(...)`.
REACHED_BY_ACTION = ["UI", "Locator", "Appears", "Front", "Gone", "Reads", "Stable"]

#: What doc/action-api.md documents, in reading order.
ACTION_API_NAMES = [
    # The class every action *is*. It lived only in config-api.md, on the
    # grounds that a config author binds one to a key - but the action's own
    # body is `run()`, and SKILL.md sends the author here for "every signature
    # an action's body uses". A clean room looked here for `check_cancelled`,
    # found the whole class missing, and recorded that it could not tell
    # whether the method exists.
    "ThreadedAction",
    "UI",
    "UINode",
    # A reader who meets `locator: Locator` in a signature needs somewhere that
    # says what is in one, which is here.
    "Locator",
    "Appears",
    "Front",
    "Gone",
    "Reads",
    "Stable",
    "WaitTimeout",
    "FillFailed",
    "ActionCancelled",
    "StaleElement",
    "Unresponsive",
]

#: The whole import line of an action module, as the skill teaches it: the action
#: API's imported names, and the logger every action makes.
ACTION_IMPORTS = [name for name in ACTION_API_NAMES
                  if name not in REACHED_BY_ACTION] + ["getLogger"]

#: In `__all__` without being API to document.
NOT_DOCUMENTED = ["__version__", "PREVIEW_APIS", "DEPRECATED_APIS"]


def _config_api_names() -> list[str]:
    """What doc/config-api.md documents: `__all__`, less the action API."""
    action_only = set(ACTION_API_NAMES) - {"ThreadedAction"}
    names = [name for name in keyhac.__all__
             if name not in action_only and name not in NOT_DOCUMENTED]
    # Beside the Focus and KeyEvent it is read with, not at the end.
    names[names.index("KeyEvent") + 1:0] = REACHED_BY_CONFIG
    return names


API_NAMES = _config_api_names()
