"""Keyhac 2 - Python-scriptable keyboard customization for Windows and macOS.

Configuration files do `from keyhac import *` and get the user-facing API.
"""

__version__ = "2.0.0a0"

from keyhac.core.keymap import Keymap
from keyhac.core.key import KeyCondition, KeyTable
from keyhac.core.focus import FocusCondition
from keyhac.core.input import InputContext
from keyhac.core.log import getLogger, Console
from keyhac.platform.base import Focus, KeyEvent

__all__ = [
    "Keymap",
    "KeyTable",
    "KeyCondition",
    "FocusCondition",
    "InputContext",
    "Focus",
    "KeyEvent",
    "getLogger",
    "Console",
    "__version__",
]
