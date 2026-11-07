"""Theme and font size on this OS's real backend (#11): the base unit moves with
the font on an open window, a secondary window hears about it, and the OS calls
behind the theme choice go through.

Each case runs in a process of its own. On macOS a CFRunLoopRun after a PuiKit
backend has closed dies of SIGTRAP, and tests/test_mac_loop.py runs one later in
this suite's process. Keyhac itself never does that: it runs either a backend's
loop or a bare CFRunLoop, never one after the other.
"""

import os
import subprocess
import sys

import pytest

# Opens windows: a VM's, not your desktop (conftest).
pytestmark = pytest.mark.desktop


def _in_own_process(case: str) -> None:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in sys.path if p)}
    done = subprocess.run([sys.executable, __file__, case], env=env, timeout=60,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_base_font_changes_on_an_open_window():
    _in_own_process("base_font")


def test_the_appearance_can_be_pinned_and_released():
    _in_own_process("appearance")


def _open_backend():
    from puikit import Font
    from puikit.backends import create_backend
    backend = create_backend("gui", width=60, height=20,
                             base_font=Font(size=12, monospace=True))
    backend.open()
    return backend


def _base_font() -> None:
    from puikit import Font
    from puikit.event import EventType
    backend = _open_backend()
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
    backend.close()


def _appearance() -> None:
    backend = _open_backend()
    window = backend.create_window(20, 5, title="secondary")
    for pinned in ("dark", "light"):
        backend.set_appearance(pinned)
        assert backend.effective_appearance() == pinned
    backend.set_appearance(None)
    assert backend.effective_appearance() in ("light", "dark")
    window.close()
    backend.close()


if __name__ == "__main__":
    {"base_font": _base_font, "appearance": _appearance}[sys.argv[1]]()
