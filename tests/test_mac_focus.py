"""macOS focus provider - the rule that `Focus.app_name` and `Focus.element`
describe the same application (issue #45).

They used to come from two independent sources with no cross-check between
them: `NSWorkspace.frontmostApplication()` for the name, the system-wide
`AXFocusedApplication` for the element.  `app_name` is what
`define_keytable(app=...)` matches on and the actions in the table it selects
then act on `element`, so a `Focus` that names one application and carries
another's element selects the wrong key table silently.
"""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


def test_get_focus_names_the_application_its_element_belongs_to(monkeypatch):
    """The whole point, end to end: the provider is handed an element from
    application 99 while a *different* application is frontmost, and the
    `Focus` it builds names 99.
    """
    from keyhac.platform.mac import focus as mf

    monkeypatch.setattr(mf, "focused_application",
                        lambda system_wide: ("ax-app-ref", 99))
    monkeypatch.setattr(mf, "app_name_of", lambda pid: f"app-{pid}")
    monkeypatch.setattr(mf, "_ax_get", lambda element, attribute: {
        "AXFocusedUIElement": "element-ref",
        "AXRole": "AXTextArea",
    }.get(attribute))

    provider = mf.MacFocusProvider()
    result = provider.get_focus()

    assert result.app_name == "app-99"
    assert result.pid == 99


def test_an_unreadable_element_still_yields_the_name_for_keytable_matching(
        monkeypatch):
    """When the focused control cannot be read at all, a key table can still
    match on the application - that is why get_focus() has fallbacks its
    element-only twin deliberately does not.
    """
    from keyhac.platform.mac import focus as mf

    monkeypatch.setattr(mf, "focused_application",
                        lambda system_wide: (None, 99))
    monkeypatch.setattr(mf, "app_name_of", lambda pid: "Claude")

    result = mf.MacFocusProvider().get_focus()

    assert result.app_name == "Claude"
    assert result.element is None


def test_nothing_readable_anywhere_is_no_focus_at_all(monkeypatch):
    from keyhac.platform.mac import focus as mf

    monkeypatch.setattr(mf, "focused_application",
                        lambda system_wide: (None, None))

    assert mf.MacFocusProvider().get_focus() is None
