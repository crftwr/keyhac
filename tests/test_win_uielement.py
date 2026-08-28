"""Windows UI Automation element behavior that is a *choice*, not a slot index.

The hand-written vtable indices are pinned in test_win_focus.py against the
Win32 answer for the same window; what is here is what the element decides on
top of them.
"""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from keyhac.platform.win.uielement import UIElement  # noqa: E402


def _detached_element():
    """A UIElement with no COM pointer, for a method that dereferences none."""
    element = UIElement.__new__(UIElement)
    element._ptr = None
    return element


def test_there_is_no_application_menu_bar_to_offer():
    """Windows has no menu bar in the sense the question asks about, and
    answering None is what carries that decision to MenuItemsSource.

    On macOS a menu bar is an OS-level part: one per application, always at
    the top of the screen, readable in full while closed. A Windows menu
    belongs to a window, may not be there at all, and fills only when it
    opens - so a "every command in the menus" list cannot be produced without
    opening every menu in turn. The top-level items are UI elements of the
    window instead, and the controls walk lists them (measured on a tk window:
    File, Edit, Shell, Debug, Options, Window, Help, all role MenuItem).
    """
    assert _detached_element().menu_bar() is None


def test_a_source_asking_for_one_gets_an_empty_list_not_an_error():
    """The whole point of answering None: MenuItemsSource in a shared config
    is a no-op here, not a crash and not a wrong list."""
    import keyhac.core.sources as src

    class _Win:
        element = _detached_element()

    class _Keymap:
        get_active_window = staticmethod(_Win)

    original = src.Keymap.get_instance
    src.Keymap.get_instance = staticmethod(lambda: _Keymap())
    try:
        assert list(src.MenuItemsSource().candidates()) == []
    finally:
        src.Keymap.get_instance = original
