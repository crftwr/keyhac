"""Fake platform implementations for tests and headless development.

FakeInputHook records injected events and lets tests drive the engine with
scripted key sequences.  FakeFocusProvider returns a settable Focus.
"""

from typing import Callable, Sequence

from keyhac.platform.base import InputHook, FocusProvider, Focus, KeyEvent


class FakeInputHook(InputHook):

    def __init__(self, layout: str = "ansi"):
        self._layout = layout
        self._on_key = None
        self._on_restored = None
        self._installed = False
        self.sent: list[tuple[int, bool, bool]] = []  # (vk, down, replay)
        self.decisions: list[bool] = []               # consume decisions

    # InputHook interface -------------------------------------------------

    def install(self, on_key: Callable[[KeyEvent], bool],
                on_restored: Callable[[], None]) -> None:
        self._on_key = on_key
        self._on_restored = on_restored
        self._installed = True

    def uninstall(self) -> None:
        self._installed = False

    @property
    def installed(self) -> bool:
        return self._installed

    def send(self, events: Sequence[tuple[int, bool]], replay: bool = False) -> None:
        for vk, down in events:
            self.sent.append((vk, down, replay))
            if replay and self._on_key:
                # Replay events re-enter the engine, like the real platforms
                self._on_key(KeyEvent(vk, down, kind="replay"))

    def keyboard_layout(self) -> str:
        return self._layout

    # Test helpers ---------------------------------------------------------

    def key(self, vk: int, down: bool, kind: str = "real") -> bool:
        """Deliver one key event to the engine; returns the consume decision."""
        decision = self._on_key(KeyEvent(vk, down, kind))
        self.decisions.append(decision)
        return decision

    def stroke(self, vk: int, kind: str = "real") -> tuple[bool, bool]:
        """Deliver a down+up pair; returns both consume decisions."""
        return self.key(vk, True, kind), self.key(vk, False, kind)

    def restore(self) -> None:
        self._on_restored()

    def clear(self) -> None:
        self.sent.clear()
        self.decisions.clear()


class FakeFocusProvider(FocusProvider):

    def __init__(self, focus: Focus | None = None):
        self.focus = focus if focus is not None else Focus(
            app_name="testapp", pid=1, window_title="Test Window",
            class_name="TestClass", path="/testapp/TestClass(Test Window)")

    def get_focus(self) -> Focus | None:
        return self.focus
