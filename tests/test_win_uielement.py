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


class _Node:
    """A UINode in the shape menu_bar() reads: a name, and the element."""

    def __init__(self, name, element):
        self.name = name
        self.element = element


def _detached_element():
    """A UIElement with no COM pointer - menu_bar() only passes `self` to the
    (patched) search, so it never dereferences one."""
    element = UIElement.__new__(UIElement)
    element._ptr = None
    return element


def _found(monkeypatch, by_role):
    monkeypatch.setattr("keyhac.core.uitree.find_elements",
                        lambda root, role=None, **kw: by_role.get(role, []))


def test_the_application_menu_bar_is_preferred_over_the_system_one(monkeypatch):
    """Every window has a title-bar menu, bridged as MenuBar "System", and it
    comes earlier in the tree than the application's own "Application" bar -
    so taking the first MenuBar found offered exactly one row, reading
    "System". Seen live on a tk window whose real bar holds File/Edit/Help."""
    system, application = object(), object()
    _found(monkeypatch, {"MenuBar": [_Node("System", system),
                                     _Node("Application", application)]})
    assert _detached_element().menu_bar() is application


def test_a_window_with_only_a_system_menu_has_no_menu_bar(monkeypatch):
    """Which is the truth for an application that draws its own menus (the
    ribbon-era ones, and anything owner-drawn): better None than a row the
    user did not mean."""
    _found(monkeypatch, {"MenuBar": [_Node("System", object())]})
    assert _detached_element().menu_bar() is None


def test_a_menu_without_the_bar_role_still_answers(monkeypatch):
    """The fallback: some applications expose a Menu and no MenuBar."""
    menu = object()
    _found(monkeypatch, {"Menu": [_Node("Context", menu)]})
    assert _detached_element().menu_bar() is menu
