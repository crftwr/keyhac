"""Platform abstraction interfaces.

keyhac.core depends only on this module; the OS implementations live in
keyhac.platform.mac (PyObjC) and keyhac.platform.win (ctypes).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class KeyEvent:
    """A normalized key event delivered by the OS hook.

    kind:
      "real"   - physical input (or input injected by other apps)
      "replay" - injected by Keyhac in replay mode; re-evaluated by the keymap
    Events injected by Keyhac in normal (translated) mode are filtered out by
    the platform layer and never reach the engine.
    """
    vk: int
    down: bool
    kind: str = "real"


@dataclass
class Focus:
    """Portable snapshot of the current keyboard focus.

    - app_name: process/exe base name without extension (Windows),
      localized application name (macOS)
    - path: focus path string; on macOS the AX focus path
      ("/AXApplication(Xcode)/AXWindow(...)..."), on Windows a synthesized
      "/{app_name}/{class_name}({title})" (provisional format)
    - class_name: Win32 window class name (Windows only, None on macOS)
    - native: platform object for power users - UIElement (macOS) /
      Window wrapper (Windows)
    """
    app_name: str | None = None
    pid: int | None = None
    window_title: str | None = None
    class_name: str | None = None
    path: str | None = None
    native: Any = None


class InputHook(ABC):
    """Low-level keyboard hook + key event injection."""

    @abstractmethod
    def install(self,
                on_key: Callable[[KeyEvent], bool],
                on_restored: Callable[[], None]) -> None:
        """Install the hook. on_key returns True to consume the event and is
        called synchronously on the thread that runs the event loop.
        on_restored is called when the OS disabled the hook and it was
        re-installed/re-enabled (modifier state must be reset)."""

    @abstractmethod
    def uninstall(self) -> None: ...

    @property
    @abstractmethod
    def installed(self) -> bool: ...

    @abstractmethod
    def send(self, events: Sequence[tuple[int, bool]], replay: bool = False) -> None:
        """Inject a batch of (vk, down) key events, tagged so the hook can
        classify them ("own" filtered / "replay" re-processed)."""

    @abstractmethod
    def keyboard_layout(self) -> str:
        """Return "ansi", "jis" or "iso"."""

    def check_health(self) -> None:
        """Called periodically (~100 ms) from the event loop.  Platforms that
        need watchdog recovery (Windows silent unhook) override this; others
        run their own timers."""


class FocusProvider(ABC):

    @abstractmethod
    def get_focus(self) -> Focus | None: ...


class EventLoop(ABC):
    """The main-thread native event loop (CFRunLoop / GetMessage pump)."""

    @abstractmethod
    def run(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def call_later(self, delay_seconds: float, func: Callable[[], None]) -> None: ...


class ClipboardProvider(ABC):
    """OS clipboard access + change detection (poll() driven by the app's
    periodic tick; event-driven listeners can layer on later)."""

    @abstractmethod
    def get_text(self) -> str | None: ...

    @abstractmethod
    def set_text(self, s: str) -> None: ...

    @abstractmethod
    def poll(self) -> bool:
        """Return True when the clipboard changed since the last poll."""


class AppControl(ABC):
    """Application-level actions (activate, launch)."""

    @abstractmethod
    def activate_pid(self, pid: int) -> bool: ...

    @abstractmethod
    def launch(self, app_name: str) -> None: ...
