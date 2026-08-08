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

    Attributes:
        vk: Virtual key code.
        down: True for key down, False for key up.
        kind: "real" for physical input (or input injected by other apps),
            "replay" for input Keyhac injected in replay mode, which the
            keymap re-evaluates.  Events Keyhac injects in normal (translated)
            mode are filtered out by the platform layer and never arrive here.
    """
    vk: int
    down: bool
    kind: str = "real"


@dataclass
class Focus:
    """Portable snapshot of the current keyboard focus.

    Available as ``keymap.focus``, and passed to every
    ``custom_condition_func``.

    Attributes:
        app_name: Process/exe base name without extension (Windows), or the
            localized application name (macOS).
        pid: Process id of the focused application.
        window_title: Title of the focused window.
        class_name: Win32 window class name (Windows only; None on macOS).
        path: Focus path string - on macOS the AX focus path
            ("/AXApplication(Xcode)/AXWindow(...)..."), on Windows a
            synthesized "/{app_name}/{class_name}({title})" (provisional
            format).
        element: The focused *semantic* element - an AX UIElement (macOS) or a
            UI Automation UIElement (Windows).  Same shape on both
            (get_attribute_names(), get_attribute_value(), get_action_names(),
            perform_action(), parent()), but each uses its own OS's
            vocabulary of attribute names, "AXRole" versus "ControlType".
            Portable code uses app_name / window_title / class_name and the
            focus path instead.
        native: The platform power object - a UIElement (macOS) or
            NativeWindow, an HWND wrapper (Windows).
    """
    app_name: str | None = None
    pid: int | None = None
    window_title: str | None = None
    class_name: str | None = None
    path: str | None = None
    native: Any = None
    element: Any = None

    def __getattr__(self, name):
        # keyhac-mac's custom_condition_func received the UIElement itself;
        # forwarding unknown attributes to .native keeps those conditions
        # working unchanged (e.g. focus.get_attribute_value("AXWindow")).
        native = self.__dict__.get("native")
        if native is not None:
            try:
                return getattr(native, name)
            except AttributeError:
                # Typically an AX call from a macOS config running on Windows,
                # where Focus.native is an HWND wrapper. The accessibility
                # methods (get_attribute_value, get_attribute_names,
                # perform_action) do exist on Windows, but on the UI Automation
                # element in Focus.element, with UIA attribute names.
                raise AttributeError(
                    f"Focus has no attribute {name!r}, and neither does "
                    f"{type(native).__name__} (Focus.native). On Windows, "
                    f"accessibility calls go through Focus.element (UI "
                    f"Automation, \"ControlType\"/\"Name\" vocabulary); AX "
                    f"attribute names exist only on macOS.") from None
        raise AttributeError(
            f"Focus has no attribute {name!r} (and no native element to forward to)")


class InputHook(ABC):
    """Low-level keyboard hook + key event injection."""

    @abstractmethod
    def install(self,
                on_key: Callable[[KeyEvent], bool],
                on_restored: Callable[[], None],
                on_mouse: Callable[[], None] | None = None) -> None:
        """Install the hook. on_key returns True to consume the event and is
        called synchronously on the thread that runs the event loop.
        on_restored is called when the OS disabled the hook and it was
        re-installed/re-enabled (modifier state must be reset).

        on_mouse, when given, is called (no arguments; observation only,
        mouse events are never consumed) on physical mouse button-down or
        wheel input - the engine cancels a pending one-shot modifier on it
        (keyhac-win's WH_MOUSE_LL behavior). Platforms without a mouse hook
        may ignore it; a one-shot then simply survives mouse input, which is
        what keyhac-mac always did."""

    @abstractmethod
    def uninstall(self) -> None: ...

    @property
    @abstractmethod
    def installed(self) -> bool: ...

    @abstractmethod
    def send(self, events: Sequence[tuple[int, bool]], replay: bool = False) -> None:
        """Inject a batch of (vk, down) key events, tagged so the hook can
        classify them ("own" filtered / "replay" re-processed).

        Ordering contract: delivery is asynchronous - the events enter the OS
        input pipeline and re-enter the hook after the current callback
        returns - but the batch is delivered as a unit and lands *before* any
        physical key the user presses afterwards. How that is achieved is the
        platform's business: on Windows SendInput's atomic queue-tail insert
        gives it for free; on macOS CGEventPost has no such guarantee, so the
        hook defers concurrent real events until the batch has drained (see
        platform/mac/hook.py)."""

    @abstractmethod
    def keyboard_layout(self) -> str:
        """Return "ansi", "jis" or "iso"."""

    def send_text(self, s: str) -> None:
        """Type a literal string (unicode injection). Platform-optional."""
        raise NotImplementedError

    def send_mouse(self, events: Sequence[tuple], replay: bool = False) -> None:
        """Inject a batch of mouse events, tagged like send() so the hooks
        can classify them. Platform-optional (Windows first; macOS later via
        CGEvent mouse). Items:

          ("move", dx, dy)          relative cursor move in pixels; injected
                                    as an absolute position so pointer
                                    acceleration cannot distort the distance
          ("left"|"right"|"middle", down: bool)
          ("wheel", notches) / ("hwheel", notches)   positive = away / right

        Same ordering contract as send()."""
        raise NotImplementedError

    def cursor_pos(self) -> tuple[int, int]:
        """Current cursor position in virtual-screen pixels (portable
        top-left coordinates). Platform-optional."""
        raise NotImplementedError

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

    @abstractmethod
    def call_on_main_thread(self, callback: Callable[[], None]) -> None:
        """Schedule *callback* to run on the loop's own thread, waking the loop
        if it is blocked.

        Thread-safe, and the only method here that may be called off-thread -
        it is how a worker hands work back for main-thread-only APIs (UI, AX
        writes).  call_later() is *not* a substitute: on Windows it posts
        WM_TIMER to the calling thread's queue, so a worker's timer would never
        be pumped.

        With the console running, PuiKit's Backend.call_on_main_thread fills
        this role and its loop is the one turning; this exists for --no-ui,
        where there is no backend at all.  Whichever loop is actually running
        provides the vehicle."""


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

    @abstractmethod
    def edit_file(self, path: str, editor: str | None = None) -> None:
        """Open a text file for editing.  ``editor`` names the editor
        application (a name or path meaningful to the OS); None picks a
        platform default.  Failure is logged, not raised."""


class Window(ABC):
    """A top-level OS window.

    The portable half of keyhac-win's pyauto.Window and keyhac-mac's AXWindow
    element: window *operations* unify cleanly across both OSes (find,
    activate, move, restore, title, process), unlike element introspection,
    whose attribute vocabularies do not - see Focus.element.

    ``keymap.get_active_window()``, ``list_windows()`` and ``find_window()``
    hand these out; configurations never construct one.

    Note:
        Everything on this class is UI-thread only.  On macOS these are
        Accessibility calls, and AX into our own process off the main thread
        crashes with SIGTRAP.  A ThreadedAction therefore reads windows in
        starting(), computes in run(), and writes back in finished(); the
        thread-safe queries a run() may call are keymap.screen_frames() and
        keymap.window_frames().
    """

    @property
    @abstractmethod
    def title(self) -> str | None:
        """The window's title."""

    @property
    @abstractmethod
    def app_name(self) -> str | None:
        """Process base name without extension (Windows) / localized
        application name (macOS)."""

    @property
    @abstractmethod
    def pid(self) -> int | None:
        """Process id of the application owning the window."""

    @property
    def class_name(self) -> str | None:
        """Win32 window class. None on macOS, which has no such concept."""
        return None

    @property
    def element(self):
        """This window as an element, for searching inside it.

        The bridge from window operations to element introspection: an action
        finds a window portably (``keymap.find_window``) and then has to look
        *into* it, which until now meant reaching for a platform-specific
        entry point. macOS already holds the AX element; Windows resolves the
        HWND through UI Automation.
        """
        return None

    @abstractmethod
    def get_frame(self) -> tuple[float, float, float, float] | None:
        """Get the window's frame.

        Returns:
            (x, y, w, h) in global top-left-origin screen coordinates, or None
            when the window has no readable frame.
        """

    @abstractmethod
    def set_frame(self, x: float, y: float,
                  w: float = None, h: float = None) -> bool:
        """Move the window, and optionally resize it.

        Args:
            x: New left edge.
            y: New top edge.
            w: New width; None keeps the current one.
            h: New height; None keeps the current one.

        Returns:
            Whether the window accepted the change.
        """

    @abstractmethod
    def activate(self) -> bool:
        """Bring this window and its application to the front.

        Returns:
            Whether the activation succeeded.
        """

    def is_minimized(self) -> bool:
        """Whether the window is currently minimized."""
        return False

    def restore(self) -> bool:
        """Un-minimize the window.

        Returns:
            Whether the window was restored.
        """
        return False

    def minimize(self) -> bool:
        """Minimize the window.

        Returns:
            Whether the window was minimized.
        """
        return False

    @property
    def native(self) -> Any:
        """The underlying platform object (HWND wrapper / AX UIElement)."""
        return None


class WindowProvider(ABC):
    """Window discovery and screen geometry.

    find_window/list_windows/get_active_window follow Window's UI-thread
    contract; screen_frames/window_frames are deliberately thread-safe so that
    geometry math can run in a ThreadedAction worker.

    That split is not cosmetic on either OS. On macOS the safe pair uses
    CoreGraphics rather than AppKit/AX. On Windows, reading a window's *title*
    is a blocking SendMessage(WM_GETTEXT) to the owning thread, so anything
    that touches titles - list_windows, find_window - can wedge against a UI
    thread that is not pumping, while the pure-geometry queries never message
    another thread at all.
    """

    @abstractmethod
    def get_active_window(self) -> Window | None: ...

    @abstractmethod
    def list_windows(self) -> list[Window]:
        """Visible top-level windows, front-most first where the OS says so."""

    def find_window(self, app: str = None, title: str = None,
                    class_name: str = None) -> Window | None:
        """First visible window matching the given patterns (fnmatch, '|'
        alternation, case-insensitive - the same matching as focus
        conditions). All given conditions must match."""
        from keyhac.core.focus import match_window_fields
        for window in self.list_windows():
            if match_window_fields(window, app, title, class_name):
                return window
        return None

    @abstractmethod
    def screen_frames(self) -> list[tuple[float, float, float, float]]:
        """(x, y, w, h) per screen, primary first. Thread-safe."""

    @abstractmethod
    def screen_work_frames(self) -> list[tuple[float, float, float, float]]:
        """Like screen_frames(), but the *work area*: the part of each screen
        not covered by the menu bar / Dock (macOS) or taskbar (Windows).
        Same order as screen_frames(). UI-thread only in portable code: the
        macOS implementation is an AppKit query (thread-safe on Windows)."""

    @abstractmethod
    def window_frames(self) -> list[tuple[float, float, float, float]]:
        """Frames of all normal on-screen windows. Thread-safe."""
