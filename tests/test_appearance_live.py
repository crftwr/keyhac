"""Theme and font size on this OS's real backend (#11): the base unit moves with
the font on an open window, a secondary window hears about it, and the OS calls
behind the theme choice go through."""

import pytest

# Opens windows: a VM's, not your desktop (conftest).
pytestmark = pytest.mark.desktop

from puikit import Font  # noqa: E402
from puikit.backends import create_backend  # noqa: E402
from puikit.event import EventType  # noqa: E402


@pytest.fixture
def backend():
    backend = create_backend("gui", width=60, height=20,
                             base_font=Font(size=12, monospace=True))
    backend.open()
    yield backend
    backend.set_appearance(None)
    backend.close()


def test_the_base_font_changes_on_an_open_window(backend):
    window = backend.create_window(20, 5, title="secondary")
    resized = []
    window.on_event = lambda event: resized.append(event.type)
    main_events = []
    backend._handler = main_events.append
    width, height = backend.base_size
    text_width = backend.measure_text("Keyhac")

    backend.set_base_font(Font(size=24, monospace=True))

    assert backend.base_size[0] > width and backend.base_size[1] > height
    # Twice the points in about twice the unit: the proportional face moved too.
    assert backend.measure_text("Keyhac") == pytest.approx(text_width, rel=0.2)
    assert EventType.RESIZE in resized
    assert any(event.type == EventType.RESIZE for event in main_events)
    window.close()


def test_the_appearance_can_be_pinned_and_released(backend):
    window = backend.create_window(20, 5, title="secondary")
    for pinned in ("dark", "light"):
        backend.set_appearance(pinned)
        assert backend.effective_appearance() == pinned
    backend.set_appearance(None)
    assert backend.effective_appearance() in ("light", "dark")
    window.close()
