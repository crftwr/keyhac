"""Platform abstraction interfaces.

keyhac.core depends only on this module; the OS implementations live in
keyhac.platform.mac (PyObjC) and keyhac.platform.win (ctypes).
"""

import functools
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
        window_title: Title of the focused window.  On macOS it is captured
            during the focus-path walk and carries the path's transliteration
            of fnmatch special characters ("(" and "[" become "<", ")" and
            "]" become ">", and "/", "*", "?", ":" each become "-"); on
            Windows it is the raw title.  A ``title=`` pattern containing
            one of those characters must match the escaped spelling on
            macOS - or use a "*" wildcard across it, which works on both.
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

            **May be None**, so check before using it:

            - while one of Keyhac's own windows is in front. Keyhac's own
              windows do not exist to its API (issue #173), and the rest of
              the Focus is still filled in for key tables to match on;
            - on Windows, when UI Automation could not say - unavailable, or
              a call into an application that stopped answering timed out
              (issue #171);
            - on macOS, only when not even the front application could be
              named. An unreadable control falls back to its window, then to
              the application element, so this is otherwise never None -
              but it is not always the control itself.
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
                on_mouse: Callable[[str], None] | None = None) -> None:
        """Install the hook. on_key returns True to consume the event and is
        called synchronously on the thread that runs the event loop.
        on_restored is called when the hook has missed, or is about to miss,
        key events - the OS disabled it and it was re-installed/re-enabled,
        or input moved somewhere the hook cannot follow - so the engine's
        modifier state can no longer be trusted and must be reset. Saying
        *which* is the platform's job; the engine only resets.

        on_mouse, when given, is called on physical mouse button-down or
        wheel input (observation only; mouse events are never consumed) with
        the kind that happened - "button" or "wheel". The engine cancels a
        pending one-shot modifier on either (keyhac-win's WH_MOUSE_LL
        behavior), but the two are not interchangeable to everything: a
        button press says the user acted somewhere, while a wheel turn scrolls
        a background window without going anywhere, so an open candidate
        window dismisses on the first and not the second.

        Platforms without a mouse hook may ignore it; a one-shot then simply
        survives mouse input, which is what keyhac-mac always did."""

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

    def char_for_key(self, vk: int, mod: int = 0) -> str | None:
        """The character this key produces on the *active keyboard layout*,
        or None when it produces none (a function key, a chord that is not
        text) or the platform cannot say.

        A virtual key code does not name a character: which glyph ``Shift-2``
        makes is a property of the layout, and Keyhac's own tables map names
        to codes, not codes to glyphs. Anything that has to reconstruct typed
        text from hook events - the candidate window's filter field, which is
        fed through the hook because its window has no keyboard focus - has to
        ask the OS instead. See ``keyhac.ui.keyroute``.

        `mod` is Keyhac's modifier mask. Only the modifiers that change the
        produced glyph are honoured (Shift, and Alt/Option/AltGr, which
        several layouts need for ``@`` or a backslash); Ctrl and Cmd are ignored,
        since a Ctrl chord is a command rather than text.

        Platform-optional: the base returns None and the caller falls back to
        what it can name itself.
        """
        return None

    def check_health(self) -> None:
        """Called periodically (~100 ms) from the event loop.  Platforms that
        need watchdog recovery (Windows silent unhook) override this; others
        run their own timers."""


class FocusProvider(ABC):

    @abstractmethod
    def get_focus(self) -> Focus | None: ...

    def get_focused_element(self):
        """The element with keyboard focus *now*, or None.

        Separate from ``get_focus()`` because the two are asked for different
        reasons and only one of them is a snapshot.  ``get_focus()`` runs
        inside the keyboard hook and its answer is deliberately frozen with the
        key being dispatched -- the whole point is to decide which key table
        applies to *that* keystroke.  This one is asked by an action, minutes
        later, when what it needs is the truth right now (issue #44).

        The default builds the whole Focus and takes its element, which is
        correct everywhere.  A platform overrides it to skip the part it does
        not need: constructing the focus *path* costs a parent walk of AX or
        UIA round trips, and nothing here reads the path.
        """
        focus = self.get_focus()
        return getattr(focus, "element", None) if focus else None


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


def main_thread_only(method):
    """Run this platform call on the event-loop thread, wherever it is called.

    For an OS API that segfaults rather than complains when touched from a
    worker.  NSPasteboard is one: an action calling
    `keymap.clipboard.get_text()` from `run()` killed the whole process -
    `EXC_BAD_ACCESS` in `-[NSPasteboard stringForType:]`, on the action's own
    worker thread, taking the keyboard hook down with it.  Nothing warned, and
    nothing could: AppKit's answer to being used off the main thread is a
    corrupt read, not an exception.

    It is a decorator rather than a rule in the documentation because the rule
    was already documented and the crash happened anyway.  `run()` is where the
    skill tells an action to put its slow work, and `keymap.clipboard` is
    handed out precisely so that actions can read and restore the clipboard
    around a paste - so the one API that had to survive a worker thread was the
    one that did not.

    Free where it is already right: `evaluate_on_main_thread` runs inline when
    the caller is on the loop thread, which is where the history's own polling
    calls these from.
    """
    @functools.wraps(method)
    def dispatched(self, *args, **kwargs):
        # Imported here: platform modules load before the core is wired, and
        # this is the one call that needs the running Keymap.
        from keyhac.core.wait import evaluate_on_main_thread

        return evaluate_on_main_thread(lambda: method(self, *args, **kwargs))

    return dispatched


#: Kinds of clipboard content, in the order a snapshot is labelled by: a copy
#: holding both files and their names as text is a file copy, and an image
#: that came with an <img> tag in HTML is an image.
CLIPBOARD_KINDS = ("files", "image", "rich", "text")


@dataclass
class ClipboardSnapshot:
    """What the clipboard held, as far as the history keeps it.

    The provider reads a fixed set of formats - text, RTF, HTML, an image,
    files - rather than everything on offer.  Two reasons, both Windows-shaped
    but true of macOS too: some formats are not memory at all (GDI handles),
    and asking for a delay-rendered format makes the source application render
    it synchronously, which an Excel range does in dozens of formats and a hung
    application does never.  What is not read is dropped, and `lossy` says so.

    `items` is the provider's own: a list of ``{format name: bytes}``, one per
    pasteboard item on macOS (a multi-file copy is several), always one on
    Windows.  The core never looks inside it; it only counts its bytes and
    hands it back.  The other fields are the portable description.

    `kind` is None when the clipboard held something, but nothing kept - the
    "cannot be shown" case.  An empty clipboard is no snapshot at all.
    """
    kind: str | None = "text"
    text: str | None = None
    image_size: tuple | None = None
    files: tuple = ()
    lossy: bool = False
    items: list = field(default_factory=list)

    def nbytes(self) -> int:
        """Bytes held in `items` - what the session budget counts."""
        return sum(len(data) for item in self.items for data in item.values())

    def without_items(self) -> "ClipboardSnapshot":
        """The same content reduced to what survives a restart: text and files."""
        return ClipboardSnapshot(kind="files" if self.files else "text",
                                 text=self.text, files=self.files,
                                 lossy=bool(self.items) or self.lossy)


class ClipboardProvider(ABC):
    """OS clipboard access + change detection (poll() driven by the app's
    periodic tick; event-driven listeners can layer on later).

    **Implementations decorate every method with `main_thread_only`.** Both
    OSes have thread affinity here - NSPasteboard crashes, and the Win32
    clipboard associates an open with the thread that opened it - and an action
    reaches these from a worker by design."""

    @abstractmethod
    def get_text(self) -> str | None: ...

    @abstractmethod
    def set_text(self, s: str, concealed: bool = False) -> None:
        """Replace the clipboard with `s`.

        `concealed` adds the OS's monitor markers (see is_concealed), so that
        putting a password back - `preserve_clipboard` restoring what it saved
        - does not hand it to every clipboard history, ours included."""

    @abstractmethod
    def poll(self) -> bool:
        """Return True when the clipboard changed since the last poll."""

    def is_concealed(self) -> bool:
        """True when the copying app asked monitors to leave this content alone.

        Password managers mark what they copy - nspasteboard.org's
        ConcealedType / TransientType / AutoGeneratedType on macOS,
        ExcludeClipboardContentFromMonitorProcessing and
        CanIncludeInClipboardHistory = 0 on Windows - and a clipboard history
        is exactly the monitor those markers address.  Answered from the
        format list alone, so the caller can decide before reading any data.

        Not abstract: a provider that cannot tell says False, which is what
        every provider said before this existed."""
        return False

    def get_snapshot(self, max_format_size: int = None) -> "ClipboardSnapshot | None":
        """The clipboard's content in the formats the history keeps.

        `max_format_size` drops any single format larger than that (the
        snapshot is then `lossy`).  None means an empty clipboard.

        The default reads text only, which is what a provider written before
        snapshots existed can offer."""
        s = self.get_text()
        return ClipboardSnapshot(text=s) if s else None

    def image_data(self, snapshot: "ClipboardSnapshot") -> bytes | None:
        """A snapshot's image as a self-contained file's bytes (PNG, TIFF,
        BMP ...), for a preview to decode; None when it has no image.

        The snapshot's items are this provider's own format, and on Windows
        the bitmap in them is a bare DIB, which no decoder reads without the
        file header written here.  Pure data: no clipboard access."""
        return None

    def set_snapshot(self, snapshot: "ClipboardSnapshot") -> None:
        """Put a snapshot back on the clipboard, every format it kept.

        The default restores the text, the one thing every snapshot of a
        text-only provider has."""
        if snapshot.text:
            self.set_text(snapshot.text)


class ImeProvider(ABC):
    """IME (input method) on/off state for whatever holds the input focus.

    Deliberately window-less.  Windows reaches the state through a window
    handle, so it *could* address a background window; macOS has no such
    handle - TIS only ever exposes "the current input source".  Naming a
    window would therefore split the contract between the two OSes, so the
    portable one is the intersection: whatever has the input focus right now.

    Whether a change leaks to other applications is the user's OS setting
    ("Let me use a different input method for each app window" on Windows,
    "Automatically switch to a document's input source" on macOS), not
    something this interface decides.
    """

    @abstractmethod
    def get_status(self) -> bool | None:
        """True when the IME is on, False when off, None when undeterminable."""

    @abstractmethod
    def set_status(self, on: bool) -> bool:
        """Turn the IME on/off; return whether the state was actually reached."""


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

    @abstractmethod
    def open_url(self, url: str) -> None:
        """Open a URL in the user's default browser.  Logged, not raised."""


class Window(ABC):
    """A top-level OS window.

    The portable half of keyhac-win's pyauto.Window and keyhac-mac's AXWindow
    element: window *operations* unify cleanly across both OSes (find,
    activate, move, restore, title, process), unlike element introspection,
    whose attribute vocabularies do not - see Focus.element.

    ``keymap.get_active_window()``, ``list_windows()`` and ``find_window()``
    hand these out; configurations never construct one.

    Note:
        Usable from any thread, because no Window is ever one of Keyhac's
        own (issue #173): the lookups skip our process before asking it
        anything.  That is the whole of the rule.  On macOS these are
        Accessibility calls, and AX into our own process off the main thread
        crashes with SIGTRAP - but AX into another application is safe from a
        worker, which is how the candidate window walks a menu bar
        (doc/dev/design-notes.md, "Streaming a source") and how an action's
        run() reads and moves windows.  On Windows, reading another
        process's window title does not message it.
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

        The frame is the window as drawn: invisible padding around it (the
        resize border Windows keeps outside the visible edge) is excluded, so
        these are the coordinates the OS itself positions windows in.

        Returns:
            (x, y, w, h) in global top-left-origin screen coordinates, or None
            when the window has no readable frame.
        """

    @abstractmethod
    def set_frame(self, x: float, y: float,
                  w: float = None, h: float = None) -> bool:
        """Move the window, and optionally resize it.

        The coordinates are the visible frame get_frame reports, so a frame
        read from one window can be given to another unchanged.

        A maximized window is taken out of the maximized state by being
        placed, so it does not spring back to filling the screen.  A window
        in full screen or minimized cannot be placed; leave those states
        first with `restore()`.

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

    # Window state.  The OS keeps a window's placement - normal or maximized -
    # underneath minimized and full screen, which cover it for a while and
    # hand it back unchanged.  The is_* queries report what is on screen now,
    # so at most one of them is true.  Each state has one verb into it
    # (minimize, maximize, enter_full_screen) and restore() is the one way
    # back out of all three, lifting one layer the way Windows' SW_RESTORE
    # does.  Every verb returns False when there was nothing to do.  Why it
    # is this model and not "full screen is a bigger maximize":
    # doc/dev/design-notes.md, "Maximized and full screen".

    def is_minimized(self) -> bool:
        """Whether the window is currently minimized."""
        return False

    def is_maximized(self) -> bool:
        """Whether the window is currently maximized.

        False while the window is minimized or in full screen, even when it
        will come back maximized: this reports what is on screen now, so at
        most one of `is_minimized()`, `is_maximized()` and `is_full_screen()`
        is true.

        On macOS a window counts as maximized when it fills the work area of
        its screen - the screen less the menu bar and the Dock - which is
        what Window > Fill gives it.  macOS keeps no maximized flag that
        another application can read, so a window the user sized to exactly
        that frame by hand counts too.

        Returns:
            Whether the window is maximized.
        """
        return False

    def is_full_screen(self) -> bool:
        """Whether the window is in the OS's full-screen mode.

        macOS only.  Windows has no such mode - full screen there is
        something each application draws for itself, and nothing marks it -
        so this is always False on Windows.

        Returns:
            Whether the window is in full screen.
        """
        return False

    def restore(self) -> bool:
        """Take the window back one state - the way out of all of them.

        A minimized window comes back as it was before - maximized, if it
        was.  A full-screen window leaves full screen, again returning to
        what it was before.  A maximized window returns to the size it had
        before it was maximized.  To leave one particular state and nothing
        else, ask first:

        ```python
        if window.is_full_screen():
            window.restore()
        ```

        On macOS, returning from maximized brings the window's application
        to the front, and works only for a window the OS filled (Window >
        Fill, or `maximize()` on a window that was not in full screen); one
        sized to the work area any other way has no previous size to return
        to, and stays as it is.  The way back animates like the way there
        (see `maximize()`).

        Returns:
            False when the window was in none of the three states.
            Otherwise whether the change was made - on macOS, coming back
            from maximized, whether it was accepted, since the animation
            leaves nothing to check yet.
        """
        return False

    def minimize(self) -> bool:
        """Minimize the window.

        On macOS a window in full screen cannot be minimized; `restore()` it
        first.

        Returns:
            False when the window was already minimized or cannot be
            minimized; otherwise whether it was.
        """
        return False

    def maximize(self) -> bool:
        """Maximize the window.

        On macOS this is Window > Fill: the window fills the work area and
        the OS remembers its previous size for `restore()`.  It brings the
        window's application to the front, which Fill needs, and the window
        animates into place for about a third of a second: `is_maximized()`
        turns true only at the end of it.  For about half a second more the
        OS is still finishing Fill and puts back any frame written to the
        window, so a `SnapWindow` straight after a `maximize()` does not
        stick.

        A minimized window is restored first.  On macOS a window in full
        screen leaves it and is placed over the work area instead of filled
        - Fill does not work until the way out of full screen has finished -
        so `restore()` has no previous size for it, unless it was filled
        before it went full screen.

        Returns:
            False when the window was already maximized; otherwise whether
            it was maximized - on macOS, whether Fill was accepted, since the
            animation leaves nothing to check yet.
        """
        return False

    def enter_full_screen(self) -> bool:
        """Put the window in the OS's full-screen mode.

        Leave it with `restore()`.  macOS only; on Windows it does nothing
        and returns False (see `is_full_screen()`).  It brings the window's
        application to the front, and a minimized window is restored first.

        The transition animates for about half a second, and a `restore()`
        made meanwhile is accepted and then ignored - a full-screen toggle
        bound to a key will not take a second press straight after the
        first:

        ```python
        if window.is_full_screen():
            window.restore()
        else:
            window.enter_full_screen()
        ```

        Returns:
            False when the window was already in full screen; otherwise
            whether the request was accepted.
        """
        return False

    @property
    def native(self) -> Any:
        """The underlying platform object (HWND wrapper / AX UIElement)."""
        return None


class WindowProvider(ABC):
    """Window discovery and screen geometry.

    Everything here is usable from any thread, and none of it ever returns
    one of Keyhac's own windows (issue #173) - the two facts are one: the
    lookups skip our own process before asking it anything, which is what
    keeps them off the paths that only our own UI thread could serve (AX
    into our own process on macOS, WM_GETTEXT to our own windows on
    Windows).  screen_work_frames is the exception on macOS, where it is an
    AppKit query; `Keymap.screen_work_frames` takes it to the loop thread.
    screen_frames/window_frames ask no application anything at all, which
    makes them the cheap choice for geometry.
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

    def foreground_hides_our_windows(self) -> bool:
        """Whether a window opened now would be buried by what is in front.

        Not "is something in front of us" - anything can be raised over. This
        is the case where it cannot: Windows sorts top-level windows into
        absolute z-order *bands*, and neither WS_EX_TOPMOST nor SetWindowPos
        moves a window out of its own. Measured on Windows 11 with the Start
        menu open: it is band 6 while a PuiKit window and a screen mark are
        both band 1, so ours goes under it and stays there.

        It matters because a chooser does not take the focus - it reads the
        keystrokes through the key hook - so one that cannot be seen is one
        that silently eats what the user types into whatever they *can* see.

        Default False: a platform that cannot say assumes it can be seen.
        Refusing to open is the more damaging way to be wrong.
        """
        return False

    @abstractmethod
    def screen_frames(self) -> list[tuple[float, float, float, float]]:
        """(x, y, w, h) per screen, primary first. Thread-safe."""

    @abstractmethod
    def screen_work_frames(self) -> list[tuple[float, float, float, float]]:
        """Like screen_frames(), but the *work area*: the part of each screen
        not covered by the menu bar / Dock (macOS) or taskbar (Windows).
        Same order as screen_frames(). An AppKit query on macOS, so
        `Keymap.screen_work_frames` takes it to the loop thread; thread-safe
        on Windows."""

    @abstractmethod
    def window_frames(self) -> list[tuple[float, float, float, float]]:
        """Frames of all normal on-screen windows. Thread-safe."""
