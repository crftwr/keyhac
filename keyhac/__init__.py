"""Keyhac 2 - Python-scriptable keyboard customization for Windows and macOS.

Configuration files do `from keyhac import *` and get the user-facing API.
"""

__version__ = "2.2.3"

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
# The action API is reached through keymap.ui and UINode's own methods (see
# doc/action-api.md); only the node type and the two exceptions an action has
# to be able to catch are named here.
from keyhac.core.uitree import StaleElement, UINode
from keyhac.core.wait import WaitTimeout
from keyhac.core.fill import FillFailed
from keyhac.core.action import ActionCancelled
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
    "WaitTimeout",
    "FillFailed",
    "ActionCancelled",
    "StaleElement",
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
