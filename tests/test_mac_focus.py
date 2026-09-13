"""macOS focus provider - two rules.

`Focus.app_name` and `Focus.element` describe the same application (#45), and
the expensive path walk runs only when the cheap probe says something moved
(#144).

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


# ---------------------------------------------------------------------------
# The cheap probe (#144): 83 AX reads per call became 4 while nothing moves.


class _Probe:
    """A provider wired to fakes, with the AX reads counted.

    `moves()` changes what the focused-control read answers, which is what a
    focus move inside one application looks like from here.
    """

    def __init__(self, monkeypatch, pid=1, control="control-a", title="Doc"):
        from keyhac.platform.mac import focus as mf

        self.pid, self.control, self.title = pid, control, title
        self.reads = 0
        self.walks = 0

        def ax_get(element, attribute):
            self.reads += 1
            if attribute == "AXFocusedUIElement":
                return self.control
            if attribute == "AXFocusedWindow":
                return "window-ref"
            if attribute == "AXTitle":
                return self.title
            return None

        monkeypatch.setattr(mf, "_ax_get", ax_get)
        monkeypatch.setattr(mf, "focused_application",
                            lambda system_wide: ("app-ref", self.pid))
        # Mirrors the real one: a pid that names no application is unnamed.
        monkeypatch.setattr(mf, "app_name_of",
                            lambda pid: None if pid is None else f"app-{pid}")
        monkeypatch.setattr(mf.AS, "CFEqual", lambda a, b: a == b)

        def build_path(element):
            self.walks += 1
            return f"/AXApplication(app-{self.pid})/{element}", self.title

        monkeypatch.setattr(mf.MacFocusProvider, "_build_path",
                            staticmethod(build_path))
        self.provider = mf.MacFocusProvider()

    def get(self):
        self.reads = 0
        return self.provider.get_focus()


def test_nothing_moved_answers_out_of_the_cache_without_walking(monkeypatch):
    probe = _Probe(monkeypatch)

    first = probe.get()
    assert probe.walks == 1

    second = probe.get()
    assert second is first, "the same Focus object, as on Windows"
    assert probe.walks == 1, "the path walk must not run again"
    assert probe.reads == 3, "only the cheap tier: control, window, title"


def test_focus_moving_inside_one_application_invalidates_the_probe(monkeypatch):
    """The component `GUITHREADINFO.hwndFocus` cannot see on Windows (#145):
    same process, same window, same title, different control."""
    probe = _Probe(monkeypatch)

    first = probe.get()
    probe.control = "control-b"
    second = probe.get()

    assert second is not first
    assert probe.walks == 2


def test_a_title_change_under_a_still_focus_invalidates_the_probe(monkeypatch):
    """A document or tab switch: `title=` matches on this, and neither the pid
    nor the focused control moves."""
    probe = _Probe(monkeypatch)

    first = probe.get()
    probe.title = "Another Doc"
    second = probe.get()

    assert second is not first
    assert second.window_title == "Another Doc"


def test_an_application_switch_invalidates_the_probe(monkeypatch):
    probe = _Probe(monkeypatch)

    first = probe.get()
    probe.pid = 2
    second = probe.get()

    assert second is not first
    assert second.app_name == "app-2"


def test_losing_the_application_entirely_clears_the_cache(monkeypatch):
    """Otherwise a stale Focus outlives the application it describes."""
    from keyhac.platform.mac import focus as mf

    probe = _Probe(monkeypatch)
    probe.get()

    monkeypatch.setattr(mf, "focused_application",
                        lambda system_wide: (None, None))
    assert probe.provider.get_focus() is None
    assert probe.provider._focus is None
