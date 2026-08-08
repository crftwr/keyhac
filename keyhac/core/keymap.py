"""The Keymap engine.

Ported from keyhac-mac keyhac_main.py (Keymap), decoupled from keyhac_core:
the OS is reached only through the InputHook / FocusProvider interfaces, so
the engine runs unmodified on Windows, macOS, and in tests (FakeInputHook).
"""

import functools
import operator
import os
import shutil
import sys
import threading
import traceback
from typing import Callable

from keyhac.core.const import *
from keyhac.core.vk import init_key_names, get_key_names
from keyhac.core.key import KeyCondition, KeyTable
from keyhac.core.focus import FocusCondition
from keyhac.core.input import InputContext
from keyhac.core import log
from keyhac.core.action import ThreadedAction
from keyhac.core.config import Config
from keyhac.platform.base import InputHook, FocusProvider, Focus, KeyEvent

logger = log.getLogger("Keymap")

# Generic-plane bit per modifier name, for diagnostics.
_MODIFIER_BITS = (
    ("Alt", MODKEY_ALT), ("Ctrl", MODKEY_CTRL), ("Shift", MODKEY_SHIFT),
    ("Win", MODKEY_WIN), ("Cmd", MODKEY_CMD), ("Fn", MODKEY_FN),
    ("User0", MODKEY_USER0), ("User1", MODKEY_USER1),
    ("User2", MODKEY_USER2), ("User3", MODKEY_USER3),
)


def _collapse_planes(mod: int) -> int:
    """Fold the Left and Right modifier planes down onto the generic plane."""
    mod |= mod >> MODKEY_PLANE_BITS
    mod |= mod >> MODKEY_PLANE_BITS
    return mod & MODKEY_PLANE_MASK


class Keymap:
    """Manages key tables and executes key action translations.

    One Keymap exists per Keyhac process.  The configuration file receives it
    as ``configure(keymap)``'s argument; code outside ``configure()`` reaches
    the same object through ``Keymap.get_instance()``.

    Attributes:
        platform: "windows" or "mac" - branch on this where the two OSes
            genuinely differ.
        editor: The editor edit_config() opens the configuration file with:
            an application name or path the OS can resolve, or a callable
            receiving the path.  Empty (the default) picks a platform default.
        replay_buffer: The keystroke buffer behind the keyboard macro actions.
    """

    _instance = None

    @staticmethod
    def get_instance() -> "Keymap":
        """Get the Keymap singleton.

        Returns:
            The Keymap instance, or None before one has been created.
        """
        return Keymap._instance

    def __init__(self,
                 hook: InputHook,
                 focus_provider: FocusProvider,
                 platform: str,
                 config_path: str = None,
                 template_path: str = None):
        """Constructed by Keyhac's bootstrap.  Configurations receive the
        instance as configure()'s argument, or call Keymap.get_instance().

        lazydocs: ignore
        """

        self._hook = hook
        self._focus_provider = focus_provider
        self.platform = platform  # "windows" | "mac"

        self._config_path = config_path or os.path.expanduser("~/.keyhac/config.py")
        self._template_path = template_path or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "_config.py")

        # Serializes key dispatch and virtual input against worker threads.
        # RLock: actions triggered from the hook may open an InputContext.
        self._lock = threading.RLock()

        self._keytable_list = []            # list of (FocusCondition, KeyTable)
        self._all_keytables = []            # every table define_keytable created
        self._multi_stroke_keytable = None  # active multi-stroke KeyTable
        self._unified_keytable = {}         # merged assignments of active tables
        self._vk_mod_map = {}               # vk -> modifier bit
        self._vk_vk_map = {}                # replace_key map
        self._focus_path = None
        self._focus = None                  # Focus snapshot
        self._modifier = 0                  # tracked modifier state
        self._last_keydown = None           # for one-shot detection
        self.editor = ""                    # edit_config's editor: app name/path or callable(path)

        # Wired by main(): platform services + clipboard history + UI hooks
        self.app_control = None             # platform AppControl
        self.window_provider = None         # platform WindowProvider (may be None)
        self._clipboard_history = None      # core ClipboardHistory
        self._registered_actions = {}       # name -> action, for MCP
        self._mcp_server = None             # MCPServer while enabled
        self.on_enter_multi_stroke = None   # callable(name) - balloon help
        self.on_leave_multi_stroke = None   # callable()
        self._main_thread_dispatcher = None  # callable(callback) - see below

        from keyhac.core.replay import KeyReplayBuffer
        self.replay_buffer = KeyReplayBuffer()

        Keymap._instance = self

    # ------------------------------------------------------------------
    # Configuration

    def configure(self) -> None:
        """Load (or reload) the configuration file and rebuild the keymap.

        A configuration that fails to load leaves the previous keymap active
        and reports the traceback to the console.
        """

        with self._lock:

            # Release modifiers the engine may be holding down, so a reload
            # cannot leave one stuck.  Only meaningful while hooked: the
            # console's hook checkbox reconfigures on re-enable, and at that
            # moment nothing is held (uninstall dropped the virtual state).
            # Sending anyway logs "hook is not installed" on macOS, where
            # uninstall destroys the event sources, and injects stray key-ups
            # on Windows, where SendInput works with no hook at all.
            if self._vk_mod_map and self._hook.installed:
                self._release_modifier_all()

            init_key_names(self.platform, self._hook.keyboard_layout())

            self._keytable_list = []
            self._all_keytables = []
            self._multi_stroke_keytable = None
            self._unified_keytable = {}
            self._vk_vk_map = {}
            self._focus_path = None
            self._focus = None
            self._modifier = 0
            self.editor = ""
            self._vk_mod_map = dict(get_key_names().modifier_vk_map)
            # Layout-dependent like every other vk, so it cannot outlive the
            # init_key_names() above.
            self._escape_vk_cached = None

            logger.info("Loading configuration script.")

            extensions_dir = os.path.join(os.path.dirname(self._config_path), "extensions")
            try:
                os.makedirs(extensions_dir, exist_ok=True)
            except OSError as e:
                # A read-only data directory - a portable install on a
                # write-protected stick, or one whose config.py an admin put
                # beside Keyhac.exe under Program Files - costs the extensions
                # directory, not the config load.
                logger.warning(f"Could not create {extensions_dir}: {e}")

            self._prepare_extensions(extensions_dir)

            try:
                self.config = Config(self._config_path, self._template_path)
                self.config.call("configure", self)
            except Exception:
                print()
                logger.error(f"Loading configuration script failed:\n{traceback.format_exc()}")
                return

            self._warn_unreachable_modifiers()

    @staticmethod
    def _prepare_extensions(extensions_dir: str) -> None:
        """Make ``~/.keyhac/extensions`` importable, and re-importable.

        Two things, and the second is the one that bites.

        **On sys.path**, so a config can be split into modules -- which is what
        the directory is created for, and what doc/configuration.md has always
        promised.  *Appended*, not prepended: an extension is named after what
        it does, and ``queue.py`` beside a queue-handling action or ``copy.py``
        beside a copy one is not a far-fetched name.  Prepending would let one
        shadow the standard library for the whole process, and the traceback
        would surface somewhere with no visible connection to this directory.

        **Dropped from the module cache**, so a reload actually reloads.
        Without this, editing an extension and reloading runs the *previous*
        version: ``sys.modules`` answers the import, the edited file is never
        read, and the run reports success against stale code.  That failure is
        silent by construction, and it lands squarely on the edit-reload-run
        loop this directory exists to serve.
        """
        resolved = os.path.realpath(extensions_dir)

        if resolved not in (os.path.realpath(entry)
                            for entry in sys.path if entry):
            sys.path.append(resolved)

        prefix = resolved + os.sep
        for name, module in list(sys.modules.items()):
            path = getattr(module, "__file__", None)
            if path and os.path.realpath(path).startswith(prefix):
                del sys.modules[name]

    def reload_config(self) -> None:
        """Reload the configuration file.

        The keyhac-win name for configure(), kept because configurations and
        documentation refer to it.  The tray menu's "Reload Config" item calls
        this.
        """
        self.configure()

    def edit_config(self) -> None:
        """Open the configuration file in a text editor.

        ``keymap.editor`` chooses the editor: an application name or path
        the OS can resolve, or a callable receiving the config path.  Left
        empty, a platform default is used (Visual Studio Code / Xcode /
        TextEdit on macOS, Notepad on Windows).  The tray menu's
        "Edit Config" item calls this.
        """
        if not os.path.exists(self._config_path):
            # Deleted while running; recreate it like Config's first run does
            # ("open -a" refuses a nonexistent file on macOS).
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            shutil.copyfile(self._template_path, self._config_path)
        if callable(self.editor):
            try:
                self.editor(self._config_path)
            except Exception:
                print()
                logger.error(f"keymap.editor failed:\n{traceback.format_exc()}")
        elif self.app_control is not None:
            self.app_control.edit_file(self._config_path, self.editor or None)
        else:
            logger.warning("No editor available (running without platform "
                           "application control).")

    def _warn_unreachable_modifiers(self) -> None:
        """Warn about assignments whose modifier no key can produce here.

        Modifier *names* are OS independent, so a macOS config running on
        Windows parses "Cmd-V"/"Fn-V" without complaint - but nothing ever
        sets those bits and the assignment silently never fires.  Report it
        once per configuration load instead of leaving it to be discovered
        by pressing the key.
        """
        available = _collapse_planes(
            functools.reduce(operator.or_, self._vk_mod_map.values(), 0))

        used = 0
        for keytable in self._all_keytables:
            for key in keytable.table:
                used |= key.mod
        used = _collapse_planes(used)

        unreachable = used & ~available
        if unreachable:
            names = [n for n, bit in _MODIFIER_BITS if unreachable & bit]
            noun = "modifier" if len(names) == 1 else "modifiers"
            logger.warning(
                f"No key produces the {', '.join(names)} {noun} on "
                f"{self.platform}; key assignments using {'it' if len(names) == 1 else 'them'} "
                f"never fire. Guard them with keymap.platform, or map a key "
                f"with keymap.define_modifier().")

    def replace_key(self, src: str | int, dst: str | int) -> None:
        """Replace a key with a different key.

        The substitution runs before everything else, so the rest of the
        configuration only ever sees ``dst``.

        Args:
            src: Key to replace, as a key name or a virtual key code.
            dst: Key it is replaced with.
        """
        try:
            if isinstance(src, str):
                src = get_key_names().str_to_vk(src)
        except ValueError as e:
            logger.error(f"Invalid key expression for argument 'src': {src} ({e})")
            return
        try:
            if isinstance(dst, str):
                dst = get_key_names().str_to_vk(dst)
        except ValueError as e:
            logger.error(f"Invalid key expression for argument 'dst': {dst} ({e})")
            return
        self._vk_vk_map[src] = dst

    def define_modifier(self, key: str | int, mod: str | int) -> None:
        """Define a user modifier key.

        While defined, the key loses its original meaning entirely: a
        User0..User3 modifier is never emitted, so assignments hanging off it
        cannot collide with anything an application understands.

        Args:
            key: Key to use as the modifier, as a key name or a virtual key
                code.
            mod: Modifier the key produces - "User0".."User3", or a standard
                modifier such as "LCtrl" to give that modifier a second key.
        """
        try:
            if isinstance(key, str):
                key = get_key_names().str_to_vk(key)
        except ValueError as e:
            logger.error(f"Invalid key expression for argument 'key': {key} ({e})")
            return
        try:
            if isinstance(mod, str):
                mod = get_key_names().str_to_mod(mod, force_LR=True)
            else:
                raise TypeError
        except (ValueError, TypeError):
            logger.error(f"Invalid modifier expression for argument 'mod': {mod}")
            return
        self._vk_mod_map[key] = mod

    def define_keytable(self,
                        name: str = None,
                        focus_path_pattern: str = None,
                        custom_condition_func: Callable[[Focus], bool] = None,
                        app: str = None,
                        title: str = None,
                        class_name: str = None) -> KeyTable:
        """Define a key table.

        With any focus condition (focus_path_pattern / app / title /
        class_name / custom_condition_func) the table is added to the keymap
        and activates automatically whenever the condition is met.  Every
        matching table is active at once, merged in definition order, so a
        table defined later overrides exactly the keys it binds.

        With no condition the table is not added to the keymap: assign it to
        a key to make that key a multi-stroke prefix.

        Args:
            name: Name of the key table.  A multi-stroke table shows it in the
                balloon while armed.
            focus_path_pattern: Focus path pattern with wildcards, e.g.
                "*/AXTextArea(*)".  Watch the console's "Focus path" field for
                the live value.
            custom_condition_func: A function receiving the current Focus and
                returning whether the table applies.
            app: Application name pattern - process/exe base name on Windows
                (the ".exe" is optional), localized application name on macOS.
            title: Window title pattern.
            class_name: Win32 window class name pattern (Windows only).

        Returns:
            The KeyTable created.

        Note:
            app, title and class_name patterns are case-insensitive, take
            fnmatch wildcards (*, ?, []) and "|" alternation, and all the
            conditions given must match.
        """
        if class_name is not None and self.platform == "mac":
            logger.warning("class_name= is Windows-only; this key table never activates on macOS.")

        keytable = KeyTable(name=name)
        self._all_keytables.append(keytable)
        if focus_path_pattern or custom_condition_func or app or title or class_name:
            focus_condition = FocusCondition(
                focus_path_pattern=focus_path_pattern,
                custom_condition_func=custom_condition_func,
                app=app, title=title, class_name=class_name)
            self._keytable_list.append((focus_condition, keytable))
        return keytable

    def get_input_context(self, replay: bool = False) -> InputContext:
        """Get a key input context to send a batch of virtual key events.

        ```python
        with keymap.get_input_context() as ctx:
            ctx.send_key("Ctrl-C")
        ```

        Args:
            replay: Re-evaluate the injected events through the keymap
                (what the keyboard macro playback uses).

        Returns:
            An InputContext, to be used as a context manager.
        """
        return InputContext(self, replay)

    # ------------------------------------------------------------------
    # Main-thread dispatch

    def set_main_thread_dispatcher(self, dispatcher) -> None:
        """Wired by main() with whichever loop is actually running: PuiKit's
        Backend.call_on_main_thread with the console up, the platform
        EventLoop's under --no-ui.  *dispatcher* takes a single callable.

        lazydocs: ignore
        """
        self._main_thread_dispatcher = dispatcher

    def call_on_main_thread(self, callback) -> None:
        """Run a callback on the thread that owns the event loop.

        Thread-safe, and the supported way for a worker thread to reach
        anything main-thread-only: UI, window moves, AX writes.
        ThreadedAction.run() is the usual caller; finished() already arrives
        here, so it needs this only for work it defers further.

        Args:
            callback: Called with no arguments.

        Note:
            With no loop wired - Keyhac used as a library, or under test - the
            callback runs inline on the calling thread, which is what the code
            did everywhere before a dispatcher existed.
        """
        dispatcher = self._main_thread_dispatcher
        if dispatcher is None:
            callback()
            return
        dispatcher(callback)

    # ------------------------------------------------------------------
    # Hook entry points (called synchronously on the event-loop thread)

    def on_key_event(self, event: KeyEvent) -> bool:
        """InputHook on_key callback.  Returns True to consume the event.

        lazydocs: ignore
        """
        # Before the keytable, so Esc reaches a running action whether or not
        # the active table binds it - and outside the engine lock, because
        # this runs inside the hook's deadline and cancel_all() only sets an
        # Event.
        #
        # kind == "real" is the whole of "physical, not ours". Output Keyhac
        # injects in translated mode never reaches this callback at all (the
        # platform layer drops it on its own tag), and "replay" is excluded
        # here on purpose: a macro replaying an Esc must not kill an action
        # the user is watching. What "real" does still include is another
        # application's injected input, which the OS lets us distinguish but
        # Keyhac does not - and an Esc from anywhere is a request to stop.
        if event.down and event.kind == "real" and event.vk == self._escape_vk():
            if ThreadedAction.cancel_all():
                # Consumed only when it actually stopped something: swallowing
                # every Esc would change what the focused application sees.
                return True

        with self._lock:
            if event.down:
                return bool(self._on_key_down(event.vk))
            else:
                return bool(self._on_key_up(event.vk))

    def _escape_vk(self) -> int:
        """Esc's vk for the active layout, resolved once and remembered."""
        vk = getattr(self, "_escape_vk_cached", None)
        if vk is None:
            vk = self._escape_vk_cached = get_key_names().str_to_vk("Escape")
        return vk

    def on_hook_restored(self) -> None:
        """InputHook on_restored callback.

        lazydocs: ignore
        """
        with self._lock:
            logger.warning("Key hook timed out and has been restored.")
            # Modifier key state is not reliable anymore. Resetting.
            self._modifier = 0

    def on_mouse_event(self) -> None:
        """InputHook on_mouse callback: physical mouse button/wheel input
        cancels a pending one-shot modifier (keyhac-win behavior - clicking
        while holding a one-shot key means the hold was a drag/click
        modifier, not a tap).

        lazydocs: ignore
        """
        with self._lock:
            self._last_keydown = None

    # ------------------------------------------------------------------
    # Key dispatch (ported from keyhac-mac)

    def _on_key_down(self, vk):

        self._check_focus_change()

        if self.replay_buffer.recording:
            self.replay_buffer.record(vk, True)

        try:
            vk = self._vk_vk_map[vk]
            replaced = True
        except KeyError:
            replaced = False

        self._last_keydown = vk

        try:
            old_modifier = self._modifier
            if vk in self._vk_mod_map:
                self._modifier |= self._vk_mod_map[vk]
                if self._vk_mod_map[vk] & MODKEY_USER_ALL:
                    # User modifier keys are always consumed; dispatch a
                    # possible assignment of the key itself.
                    key = KeyCondition(vk, old_modifier, down=True)
                    self._set_last_key_text(key)
                    self._do_configured_key_action(key)
                    return True

            key = KeyCondition(vk, old_modifier, down=True)

            self._set_last_key_text(key)
            if self._do_configured_key_action(key):
                return True
            elif replaced:
                with self.get_input_context() as ctx:
                    ctx.send_key_by_vk(vk, down=True)
                logger.debug(f"REPLACE  : {key}")
                return True
            else:
                logger.debug(f"PASSTHRU : {key}")
                return False

        except Exception:
            print()
            logger.error(f"Unexpected error happened:\n{traceback.format_exc()}")
            # Pass the key through on unexpected errors so typing still works.
            return False

    def _on_key_up(self, vk):

        self._check_focus_change()

        if self.replay_buffer.recording:
            self.replay_buffer.record(vk, False)

        try:
            vk = self._vk_vk_map[vk]
            replaced = True
        except KeyError:
            replaced = False

        oneshot = (vk == self._last_keydown)
        self._last_keydown = None

        try:  # for errors
            try:  # for one-shot
                if vk in self._vk_mod_map:
                    self._modifier &= ~self._vk_mod_map[vk]
                    if self._vk_mod_map[vk] & MODKEY_USER_ALL:
                        key = KeyCondition(vk, self._modifier, down=False)
                        self._do_configured_key_action(key)
                        return True

                key = KeyCondition(vk, self._modifier, down=False)

                if self._do_configured_key_action(key):
                    return True
                elif replaced:
                    with self.get_input_context() as ctx:
                        ctx.send_key_by_vk(vk, down=False)
                    logger.debug(f"REPLACE  : {key}")
                    return True
                else:
                    logger.debug(f"PASSTHRU : {key}")
                    return False

            finally:
                if oneshot:
                    key = KeyCondition(vk, self._modifier, down=True, oneshot=True)
                    self._do_configured_key_action(key)

        except Exception:
            print()
            logger.error(f"Unexpected error happened:\n{traceback.format_exc()}")
            return False

    def _do_configured_key_action(self, key):

        logger.debug(f"INPUT    : {key}")

        action = None
        if key in self._unified_keytable:
            action = self._unified_keytable[key]

        # A non-modifier key-down leaves multi-stroke mode, whether or not it
        # matched an assignment in the multi-stroke table.
        left_multi_stroke = False
        if self._multi_stroke_keytable and key.down and not key.oneshot \
           and key.vk not in self._vk_mod_map:
            self._leave_multi_stroke()
            left_multi_stroke = True

        if action is None:
            return left_multi_stroke

        if callable(action):
            action_name = getattr(action, "__name__", None) or repr(action)
            logger.debug(f"CALL     : {action_name}")
            action()

        elif isinstance(action, KeyTable):
            self._enter_multi_stroke(action)

        else:
            if not isinstance(action, (list, tuple)):
                action = [action]

            logger.debug(f"OUTPUT   : {action}")

            with self.get_input_context() as ctx:
                for item in action:
                    if isinstance(item, str):
                        ctx.send_key(item)
                    else:
                        raise TypeError(f"Invalid key action: {item!r}")

        return True

    # ------------------------------------------------------------------
    # Multi-stroke

    def _enter_multi_stroke(self, keytable):
        logger.debug(f"Entering multi-stroke keytable - {keytable}")
        self._multi_stroke_keytable = keytable
        self._update_unified_keytable()
        if self.on_enter_multi_stroke is not None:
            try:
                self.on_enter_multi_stroke(keytable.name)
            except Exception:
                logger.error("on_enter_multi_stroke callback failed.")

    def _leave_multi_stroke(self):
        if self._multi_stroke_keytable:
            logger.debug(f"Leaving multi-stroke keytable - {self._multi_stroke_keytable}")
            self._multi_stroke_keytable = None
            self._update_unified_keytable()
            if self.on_leave_multi_stroke is not None:
                try:
                    self.on_leave_multi_stroke()
                except Exception:
                    logger.error("on_leave_multi_stroke callback failed.")

    # ------------------------------------------------------------------
    # Focus / key table selection

    def _check_focus_change(self):
        focus = self._focus_provider.get_focus()
        self._focus = focus
        new_focus_path = focus.path if focus else None

        if self._focus_path != new_focus_path:
            logger.debug(f"Focus path: {new_focus_path}")
            log.Console.get_instance().set_text("focusPath", new_focus_path or "")
            self._focus_path = new_focus_path
            self._update_unified_keytable()

    def _update_unified_keytable(self):
        self._unified_keytable = {}
        if self._multi_stroke_keytable:
            self._unified_keytable.update(self._multi_stroke_keytable.table)
        else:
            # Merged in definition order - later tables override earlier ones
            for focus_condition, keytable in self._keytable_list:
                if focus_condition.check(self._focus):
                    self._unified_keytable.update(keytable.table)

    # ------------------------------------------------------------------
    # Helpers

    def _release_modifier_all(self):
        with self.get_input_context() as ctx:
            for vk, modkey in self._vk_mod_map.items():
                if modkey & MODKEY_USER_ALL:
                    continue
                ctx.send_key_by_vk(vk, down=False)

    def _set_last_key_text(self, key):
        s = str(key)
        if s.startswith("D-"):
            s = s[2:]
        log.Console.get_instance().set_text("lastKey", s)

    @property
    def focus(self) -> Focus | None:
        """Portable snapshot of the current keyboard focus (a Focus), or None
        before the first key event."""
        return self._focus

    # ------------------------------------------------------------------
    # Window access (config-facing; see keyhac.platform.base.Window)

    def get_active_window(self):
        """Get the frontmost window.

        Returns:
            A Window, or None when there is none (or the platform has no
            window support).

        Note:
            UI-thread only, like everything on Window - never call it from a
            ThreadedAction.run().
        """
        if self.window_provider is None:
            return None
        return self.window_provider.get_active_window()

    def list_windows(self) -> list:
        """List the visible top-level windows.

        Returns:
            Window objects, front-most first where the OS says so.

        Note:
            UI-thread only.
        """
        if self.window_provider is None:
            return []
        return self.window_provider.list_windows()

    def find_window(self, app: str = None, title: str = None,
                    class_name: str = None):
        """Find the first visible window matching the given patterns.

        Matching is exactly define_keytable's: case-insensitive, fnmatch
        wildcards, "|" alternation, ".exe" optional, and all the conditions
        given must match.

        Args:
            app: Application name pattern.
            title: Window title pattern.
            class_name: Win32 window class name pattern (Windows only).

        Returns:
            A Window, or None when nothing matches.

        Note:
            UI-thread only.
        """
        if self.window_provider is None:
            return None
        return self.window_provider.find_window(
            app=app, title=title, class_name=class_name)

    def screen_frames(self) -> list:
        """Get the frame of every screen.

        Returns:
            One (x, y, w, h) tuple per screen, primary first, in the shared
            top-left-origin coordinate space.

        Note:
            Thread-safe - callable from a ThreadedAction.run().
        """
        if self.window_provider is None:
            return []
        return self.window_provider.screen_frames()

    def screen_work_frames(self) -> list:
        """Get the work area of every screen.

        Returns:
            screen_frames() minus the menu bar and Dock (macOS) or the taskbar
            (Windows), in the same order.

        Note:
            UI-thread only - the macOS implementation is an AppKit query.
        """
        if self.window_provider is None:
            return []
        return self.window_provider.screen_work_frames()

    def window_frames(self) -> list:
        """Get the frames of all normal on-screen windows.

        Returns:
            One (x, y, w, h) tuple per window, in the same coordinate space as
            screen_frames().

        Note:
            Thread-safe - callable from a ThreadedAction.run().  It is the
            geometry query to use there, since Window itself is not.
        """
        if self.window_provider is None:
            return []
        return self.window_provider.window_frames()

    def app_control_running_apps(self):
        """[(app_name, pid)] via the platform (empty when unavailable).

        lazydocs: ignore
        """
        if self.platform == "mac":
            from keyhac.platform.mac.uielement import UIElement
            return UIElement.get_running_applications()
        return []

    # ------------------------------------------------------------------
    # Named actions and the MCP endpoint

    def register_action(self, name: str, action) -> None:
        """Make an action runnable by name over MCP.

        Registering is opt-in and per-action, which is the point: it is the
        line between "Keyhac can be driven by a model" and "everything a
        configuration defines can be". Bind it to a key as usual too - this
        only adds the name.

        ```python
        keymap.register_action("extract_records", ExtractRecords())
        ```

        Args:
            name: The name run_action takes.
            action: Any callable, usually a ThreadedAction.
        """
        self._registered_actions[name] = action

    @property
    def registered_actions(self) -> dict:
        """The actions registered by name, for the MCP tools to list and run."""
        return dict(self._registered_actions)

    @property
    def mcp_server_running(self) -> bool:
        """Whether the endpoint is currently listening.

        lazydocs: ignore
        """
        return self._mcp_server is not None

    def start_mcp_server(self, port: int = 0) -> None:
        """Start the action-authoring endpoint on localhost.

        The switch is the console's **AI Integration** checkbox, or the tray
        menu's *AI Integration > MCP Server*; this is the mechanism behind it.
        There is deliberately no configuration API: an endpoint that reads every
        window
        and can run registered actions should be visibly on or visibly off,
        and a line in the middle of a config file tells you what was asked for
        once, never what is true now.

        Off until something asks. The endpoint binds to 127.0.0.1 only and
        every request carries a token published - readable by this user alone -
        beside the configuration. `keyhac-mcp-bridge` reads that file; nothing
        else needs to know the port.

        Args:
            port: TCP port, or 0 to let the OS choose (the default - clients
                read whichever port was chosen, so a fixed one buys nothing
                but a collision).

        lazydocs: ignore
        """
        if self._mcp_server is not None:
            return
        from keyhac.mcp.server import ENDPOINT_FILE, MCPServer
        from keyhac.mcp.tools import ToolRegistry

        endpoint = os.path.join(
            os.path.dirname(self._config_path or ""), ENDPOINT_FILE)
        self._mcp_server = MCPServer(ToolRegistry(self), endpoint, port=port)
        self._mcp_server.start()

    def stop_mcp_server(self) -> None:
        """Stop the endpoint and remove its published token.

        lazydocs: ignore
        """
        server, self._mcp_server = self._mcp_server, None
        if server is not None:
            server.stop()

    @property
    def ui(self):
        """The action-facing UI API - see doc/action_api.md.

        Reading and driving another application's elements: finding windows,
        searching trees, waiting for the screen to change, filling fields.
        Deliberately a separate namespace from the configuration API, and
        deliberately method-style, so `from keyhac import *` does not acquire a
        dozen generic verbs that only mean something inside an action.
        """
        if getattr(self, "_ui", None) is None:
            from keyhac.core.ui import UI
            self._ui = UI(self)
        return self._ui

    @property
    def clipboard_history(self):
        """The ClipboardHistory object (None while running without one, e.g.
        under --no-ui)."""
        return self._clipboard_history

    @property
    def clipboard(self):
        """The OS clipboard - get_text() / set_text(), or None if unwired.

        The history's provider, exposed directly because actions that paste
        need to read and restore the clipboard around what they do, which is
        not a history operation.
        """
        history = self._clipboard_history
        return getattr(history, "_provider", None) if history else None
