"""Keyhac 2 - Python-scriptable keyboard customization for Windows and macOS.

Configuration files do `from keyhac import *` and get the user-facing API.
"""

__version__ = "2.8.0"

from keyhac.core.api_marks import DEPRECATED_APIS, PREVIEW_APIS
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
from keyhac.core.candidate import Candidate, CandidatePreview
from keyhac.core.source import CallableSource, ChooserPage, CandidateSource
from keyhac.core.sources import (
    ActionsSource,
    BrowserTabsSource,
    ClipboardHistorySource,
    KeyBindingsSource,
    MenuItemsSource,
    WindowControlsSource,
    ClipboardToolsSource,
    SnippetsSource,
)
# The action API is reached through keymap.ui and UINode's own methods (see
# doc/action-api.md); only the node type and the two exceptions an action has
# to be able to catch are named here.
from keyhac.core.uitree import StaleElement, UINode, Unresponsive
from keyhac.core.wait import WaitTimeout
from keyhac.core.fill import FillFailed
from keyhac.core.action import ActionCancelled
from keyhac.actions import (
    ActivateApplication,
    ActivateWindow,
    MouseButtonClick,
    MouseButtonDown,
    MouseButtonUp,
    MouseHorizontalWheel,
    MouseMove,
    MouseWheel,
    MoveWindow,
    ReportCaretAnchor,
    SnapWindow,
    ChooserAction,
    DateTimeSnippet,
    ShowCandidates,
    ShowClipboardHistory,
    ShowClipboardSnippets,
    ShowClipboardTools,
)
from keyhac.platform.base import Focus, KeyEvent

# The one list of what a config or an extension can import. Its order is
# doc/config-api.md's, grouped the way doc/configuration.md introduces the API, so
# someone reading top to bottom meets the keymap first and the leaf actions last.
# tools/public_api.py derives the rest, and tests/test_public_api.py checks it.
__all__ = [
    # The engine and its collaborators
    "Keymap",
    "KeyTable",
    "KeyCondition",
    "FocusCondition",
    "InputContext",
    "Focus",
    "KeyEvent",
    # Actions
    "ThreadedAction",
    "InputText",
    "LaunchApplication",
    "ActivateApplication",
    "ActivateWindow",
    "MoveWindow",
    "SnapWindow",
    "MouseMove",
    "MouseButtonDown",
    "MouseButtonUp",
    "MouseButtonClick",
    "MouseWheel",
    "MouseHorizontalWheel",
    "StartRecordingKeys",
    "StopRecordingKeys",
    "ToggleRecordingKeys",
    "PlaybackRecordedKeys",
    # Clipboard
    "ClipboardHistory",
    # Candidate window: sources are values, so several share one hotkey.
    "ChooserAction",
    "ShowCandidates",
    "Candidate",
    "CandidatePreview",
    "CandidateSource",
    "CallableSource",
    "ChooserPage",
    "ActionsSource",
    "BrowserTabsSource",
    "ClipboardHistorySource",
    "KeyBindingsSource",
    "MenuItemsSource",
    "WindowControlsSource",
    "SnippetsSource",
    "ClipboardToolsSource",
    "ShowClipboardHistory",
    "ShowClipboardSnippets",
    "ShowClipboardTools",
    "DateTimeSnippet",
    # Logging and diagnostics
    "getLogger",
    "Console",
    "ReportCaretAnchor",
    # The action API, documented in doc/action-api.md
    "UINode",
    "WaitTimeout",
    "FillFailed",
    "ActionCancelled",
    "StaleElement",
    "Unresponsive",
    # Which APIs carry a mark (keyhac/core/api_marks.py)
    "PREVIEW_APIS",
    "DEPRECATED_APIS",
    "__version__",
]
