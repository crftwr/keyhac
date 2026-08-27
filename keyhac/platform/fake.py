"""Fake platform implementations for tests and headless development.

FakeInputHook records injected events and lets tests drive the engine with
scripted key sequences.  FakeFocusProvider returns a settable Focus, and
FakeImeProvider a settable IME state.
"""

from typing import Callable, Sequence

from keyhac.core.vk import get_key_names
from keyhac.platform.base import (InputHook, FocusProvider, Focus, KeyEvent,
                                  ImeProvider)


#: US layout, enough for the tests that care about punctuation.
_FAKE_GLYPHS = {
    "Minus": ("-", "_"), "Equal": ("=", "+"), "Comma": (",", "<"),
    "Period": (".", ">"), "Slash": ("/", "?"), "Semicolon": (";", ":"),
    "Quote": ("'", '"'), "BackQuote": ("`", "~"),
    "OpenBracket": ("[", "{"), "CloseBracket": ("]", "}"),
    "BackSlash": ("\\", "|"),
}
_FAKE_SHIFTED_DIGITS = dict(zip("1234567890", "!@#$%^&*()"))


class FakeInputHook(InputHook):

    def __init__(self, layout: str = "ansi"):
        self._layout = layout
        self._on_key = None
        self._on_restored = None
        self._on_mouse = None
        self._installed = False
        self.sent: list[tuple[int, bool, bool]] = []  # (vk, down, replay)
        self.sent_text: list[str] = []
        self.sent_mouse: list[tuple] = []             # (event, replay)
        self.decisions: list[bool] = []               # consume decisions
        self._cursor = (100, 100)

    # InputHook interface -------------------------------------------------

    def install(self, on_key: Callable[[KeyEvent], bool],
                on_restored: Callable[[], None],
                on_mouse: Callable[[], None] | None = None) -> None:
        self._on_key = on_key
        self._on_restored = on_restored
        self._on_mouse = on_mouse
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

    def send_text(self, s: str) -> None:
        self.sent_text.append(s)

    def send_mouse(self, events: Sequence[tuple], replay: bool = False) -> None:
        for event in events:
            self.sent_mouse.append((event, replay))
            if event[0] == "move":
                self._cursor = (self._cursor[0] + event[1],
                                self._cursor[1] + event[2])

    def cursor_pos(self) -> tuple[int, int]:
        return self._cursor

    def char_for_key(self, vk: int, mod: int = 0) -> str | None:
        """A small US-layout stand-in for the OS translation, so the tests
        can exercise the punctuation path without a real keyboard."""
        from keyhac.core.const import (
            MODKEY_CMD, MODKEY_CMD_L, MODKEY_CMD_R,
            MODKEY_CTRL, MODKEY_CTRL_L, MODKEY_CTRL_R,
            MODKEY_SHIFT, MODKEY_SHIFT_L, MODKEY_SHIFT_R,
        )
        if mod & (MODKEY_CTRL | MODKEY_CTRL_L | MODKEY_CTRL_R
                  | MODKEY_CMD | MODKEY_CMD_L | MODKEY_CMD_R):
            return None
        name = get_key_names().vk_to_str(vk)
        shift = bool(mod & (MODKEY_SHIFT | MODKEY_SHIFT_L | MODKEY_SHIFT_R))
        pair = _FAKE_GLYPHS.get(name)
        if pair is not None:
            return pair[1] if shift else pair[0]
        if len(name) == 1 and name.isalpha():
            return name.upper() if shift else name.lower()
        if len(name) == 1 and name.isdigit():
            return _FAKE_SHIFTED_DIGITS[name] if shift else name
        return None

    # Test helpers ---------------------------------------------------------

    def mouse(self, kind: str = "button") -> None:
        """Deliver one physical mouse notification - "button" or "wheel"."""
        if self._on_mouse is not None:
            self._on_mouse(kind)

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


class FakeImeProvider(ImeProvider):
    """Settable IME state.  ``status = None`` models the OS having no IME to
    ask, and ``accepts`` an IME that declines the change."""

    def __init__(self, status: bool | None = False, accepts: bool = True):
        self.status = status
        self.accepts = accepts

    def get_status(self) -> bool | None:
        return self.status

    def set_status(self, on: bool) -> bool:
        if not self.accepts or self.status is None:
            return False
        self.status = on
        return True
