"""Keyhac 2 - Python-scriptable keyboard customization for Windows and macOS.

Configuration files do `from keyhac import *` and get the user-facing API.
"""

__version__ = "2.1.0"

from keyhac.core.keymap import Keymap
from keyhac.core.key import KeyCondition, KeyTable
from keyhac.core.focus import FocusCondition
from keyhac.core.input import InputContext
from keyhac.core.log import getLogger, Console
from keyhac.core.action import (
    ThreadedAction, LaunchApplication, InputText,
    StartRecordingKeys, StopRecordingKeys, ToggleRecordingKeys,
    PlaybackRecordedKeys,
)
from keyhac.core.clipboard_history import ClipboardHistory
from keyhac.core.uitree import (
    UINode, find_element, find_elements, format_tree, get_ui_tree,
)
from keyhac.core.wait import (
    WaitTimeout, wait_for, wait_for_element, wait_for_stable, wait_until_gone,
)
from keyhac.actions import (
    ActivateWindow,
    MouseButtonClick,
    MouseButtonDown,
    MouseButtonUp,
    MouseHorizontalWheel,
    MouseMove,
    MouseWheel,
    MoveWindow,
    SnapWindow,
    ChooserAction,
    DateTimeSnippet,
    ShowClipboardHistory,
    ShowClipboardSnippets,
    ShowClipboardTools,
)
from keyhac.platform.base import Focus, KeyEvent

__all__ = [
    "Keymap",
    "KeyTable",
    "KeyCondition",
    "FocusCondition",
    "InputContext",
    "Focus",
    "KeyEvent",
    "ThreadedAction",
    "LaunchApplication",
    "InputText",
    "StartRecordingKeys",
    "StopRecordingKeys",
    "ToggleRecordingKeys",
    "PlaybackRecordedKeys",
    "ClipboardHistory",
    "UINode",
    "get_ui_tree",
    "find_element",
    "find_elements",
    "format_tree",
    "wait_for",
    "wait_for_element",
    "wait_until_gone",
    "wait_for_stable",
    "WaitTimeout",
    "ChooserAction",
    "MouseButtonClick",
    "MouseButtonDown",
    "MouseButtonUp",
    "MouseHorizontalWheel",
    "MouseMove",
    "MouseWheel",
    "MoveWindow",
    "SnapWindow",
    "ActivateWindow",
    "ShowClipboardHistory",
    "ShowClipboardSnippets",
    "ShowClipboardTools",
    "DateTimeSnippet",
    "getLogger",
    "Console",
    "__version__",
]
