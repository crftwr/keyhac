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
import time
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


def _modifier_name(mod: int) -> str:
    """A modifier bit mask as its name, sided when it is one side only."""
    names = []
    for name, generic in _MODIFIER_BITS:
        left = generic << MODKEY_PLANE_BITS
        right = generic << (MODKEY_PLANE_BITS * 2)
        if mod & generic or (mod & left and mod & right):
            names.append(name)
        elif mod & left:
            names.append(f"L{name}")
        elif mod & right:
            names.append(f"R{name}")
    return "+".join(names) if names else "(none)"


#: How long the MCP endpoint stays open once switched on, in seconds.
#:
#: One switch with a deadline rather than a switch you remember to turn off.
#: The feature is an authoring-time one - a model helps you *write* an action,
#: and the action then runs with no model involved - so an endpoint still
#: listening the next day is serving nothing, while still being able to read
#: every window you open.  That is the larger of the two exposures here, and it
#: is the one a forgotten switch leaves armed.
#:
#: Fixed from the moment it is switched on, never extended by use.  A sliding
#: window would be kinder to a long session and would also hand the window's
#: length to whoever is driving the endpoint: a model working every few minutes
#: - because it was told to by something it read on screen - would keep its own
#: permission alive indefinitely, which is the one property this is here to
#: deny.  An hour is longer than the authoring sessions measured so far, so
#: re-arming should be rare enough not to become reflexive.
_AUTHORING_WINDOW = 60 * 60


#: ``{module name: (real path, source mtime)}`` for every module loaded out of
#: ``extensions/``.  Process-wide rather than per-Keymap because ``sys.modules``
#: is, and the question this answers is about that one dictionary: is the copy
#: in it still the file on disk?  Written by ``_stamp_extensions`` after a
#: configuration load and by the loader that imports a module to run it; read by
#: ``_loaded_extension``.
_extension_stamps: dict[str, tuple[str, int]] = {}


def _source_mtime(path: str) -> int:
    """A file's mtime in nanoseconds, or -1 when it cannot be read.

    Nanoseconds because the whole-second resolution of a ``.pyc`` header is
    what made issue #41 possible; nothing here should repeat that.
    """
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return -1


def _extension_files(extensions_dir: str):
    """``(module name, real path)`` for each loaded module under the directory.

    lazydocs: ignore
    """
    prefix = os.path.realpath(extensions_dir) + os.sep
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if not path:
            continue
        path = os.path.realpath(path)
        if path.startswith(prefix):
            yield name, path


def stamp_extension_module(module_name: str, path: str) -> None:
    """Record that `module_name` was just loaded from `path`.

    For the one importer that is not a plain ``import``: `start_action` loads a
    module by file location, and what it leaves in ``sys.modules`` has to be
    recognizable as current afterwards, or the next start re-imports it and
    splits the module in two again (issue #40).

    lazydocs: ignore
    """
    _extension_stamps[module_name] = (os.path.realpath(path),
                                      _source_mtime(path))


def _key_text(key: KeyCondition) -> str:
    """A KeyCondition spelled the way a `config.py` would write it.

    `str(KeyCondition)` always states the edge - `D-Fn-J` - because it is a
    diagnostic. A configuration writes `kt["Fn-J"]`, key-down being the default,
    so reporting the diagnostic spelling here would hand back something that
    does not match the file it came from and would not round-trip if pasted.
    `U-` and `O-` stay, being the spellings you do have to write.
    """
    text = str(key)
    return text[2:] if text.startswith("D-") else text


def _condition_text(condition) -> str:
    """A FocusCondition as the `define_keytable(...)` call that made it."""
    parts = [f"{name}={value!r}"
             for name, value in (("focus_path_pattern",
                                  condition.focus_path_pattern),
                                 ("app", condition.app),
                                 ("title", condition.title),
                                 ("class_name", condition.class_name))
             if value]
    if condition.custom_condition_func is not None:
        # Reported but not evaluated for the listing: it is the operator's own
        # code, and the * marker already says whether it passed just now.
        parts.append("custom_condition_func=...")
    return ", ".join(parts) or "no condition"


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
        self._modal_input = None            # sticky key grab - see push_modal_input
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
        self.ime_provider = None            # platform ImeProvider (may be None)
        self._clipboard_history = None      # core ClipboardHistory
        self._mcp_server = None             # MCPServer while enabled
        self._mcp_timer = None              # closes the window on its own
        self.on_enter_multi_stroke = None   # callable(name) - balloon help
        self.on_leave_multi_stroke = None   # callable()
        self.on_mouse_button = None         # callable() - see on_mouse_event
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

            extensions_dir = self.extensions_dir
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
            finally:
                # In `finally` because a config that raised half way through
                # still imported everything above the line that raised, and
                # those modules are in sys.modules either way.
                self._stamp_extensions(extensions_dir)

            self._warn_unreachable_modifiers()

    @staticmethod
    def _prepare_extensions(extensions_dir: str) -> None:
        """Make ``~/.keyhac/extensions`` importable, and re-importable.

        Three things, and only the first is obvious.

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

        **And its bytecode dropped with it**, which is the same failure one
        layer down and was missed the first time (issue #41).  A timestamp
        ``.pyc`` is validated against the source's mtime **in whole seconds**
        and its size, so an edit landing in the same second that leaves the
        file the same length reloads the *previous* bytecode -- through an
        eviction that did everything else right.  That is not the corner it
        sounds like: ``write_extension`` replaces whole files, and a model
        fixing one character in a format string sends back a file of identical
        length, seconds later.  Measured, twice: once on the ``start_action``
        path, where the fix landed, and again here through ``reload_config``,
        where the reasoning for leaving it out ("a config reload happens by
        hand and rarely") stopped being true the moment an agent started
        calling it in a loop.
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
                _extension_stamps.pop(name, None)

        shutil.rmtree(os.path.join(resolved, "__pycache__"), ignore_errors=True)

    @staticmethod
    def _stamp_extensions(extensions_dir: str) -> None:
        """Record which file, at which mtime, each loaded extension came from.

        Called once the configuration script has finished importing, which is
        the only moment anything knows: a plain ``import`` leaves no trace of
        *when* it read the file, and there is no import hook here to add one.

        What it buys is that something else can ask whether the copy in
        ``sys.modules`` is still the file on disk -- see
        :meth:`_loaded_extension`, and issue #40 for what happened without it.
        """
        for name, path in _extension_files(extensions_dir):
            _extension_stamps[name] = (path, _source_mtime(path))

    @staticmethod
    def _loaded_extension(module_name: str, path: str):
        """The module already loaded from `path`, or None if it is not current.

        The whole of the answer to issue #40.  ``start_action`` used to evict
        and re-import unconditionally, so a class it ran and the *same* class
        bound to a key came from two different module objects and shared no
        module-level state at all -- two caches, two connections, two counters,
        numbered independently.  A module that has not changed since it was
        loaded is the one to run.

        When the file *has* changed, this returns None and the caller
        re-imports: the divergence then lasts exactly as long as it is correct,
        because until the operator reloads, the class on their key genuinely is
        the previous version.
        """
        module = sys.modules.get(module_name)
        if module is None:
            return None
        stamp = _extension_stamps.get(module_name)
        if stamp is None:
            return None
        stamped_path, stamped_mtime = stamp
        if stamped_path != os.path.realpath(path):
            return None
        if stamped_mtime != _source_mtime(path):
            return None
        return module

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

        A Windows key cannot be one, and the call is refused with an error
        in the log. Defining it does not take the key away from the OS:
        Keyhac consumes the key-down, so no application ever receives it and
        the Start menu stays shut, but anything watching the keyboard ahead
        of Keyhac still sees the physical key held - the Xbox Game Bar opens
        on Win+G either way, and it swallows that keystroke, including one
        Keyhac itself injected. A modifier that is invisible to applications
        but not to the shell is not what this promises, so it is not offered.

        Any other key may be redefined, including one that already is a
        modifier - ``define_modifier("RAlt", "RUser0")`` works - but prefer a
        key that is not one: the key stops being Alt (or Ctrl, or Shift) for
        everything, everywhere, and that is a large thing to give up by
        accident. Redefining a modifier is noted in the log.

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
        # keyhac-win refused every key that already was a modifier, and its
        # sample configuration went through replaceKey to reach the Win key.
        # Only the Win keys are refused here - the rest of that rule would
        # break define_modifier("RAlt", "RUser0"), which both the macOS
        # sample and keyhac-mac configurations have always used. Laundering
        # the key through replace_key does not help either: what still holds
        # the Win key is the OS, which never hears about Keyhac's renaming.
        if self._vk_mod_map.get(key, 0) & MODKEY_WIN_ALL:
            name = get_key_names().vk_to_str(key)
            logger.error(f"A Windows key cannot be a user modifier: {name}")
            return
        # Redefining a modifier is legitimate, so this is not a warning - but
        # it is silent about a real loss: that key is not Alt (or Ctrl, or
        # Shift) for anything, anywhere, any more. Said once, at INFO, which
        # the console shows by default.
        if key in self._vk_mod_map:
            was = _modifier_name(self._vk_mod_map[key])
            logger.info(
                f"{get_key_names().vk_to_str(key)} was the {was} modifier and "
                f"is now {_modifier_name(mod)}; nothing sees {was} from it "
                f"any more.")
        self._vk_mod_map[key] = mod

    def describe_keymap(self, limit: int = 300) -> str:
        """Every key table this configuration defined, as readable text.

        What a key binding has that an action does not is a way to check it
        without pressing it. An action can be started and its result read, so
        the write-run-read loop closes; nothing can press a key on the
        operator's behalf, and nothing should. This is the half of that loop
        that *can* be closed - it answers "did the binding land, in the table I
        meant, and does that table apply where the operator is standing?", and
        leaves only "does pressing it do the right thing?" to them.

        Reported per table rather than merged, because which table a key lands
        in is the thing configurations get wrong: every matching table is
        active at once and merged in definition order, so a binding can be
        present and still be overridden by one defined later.

        Args:
            limit: Maximum bindings to report, oldest table first.

        lazydocs: ignore
        """
        with self._lock:
            focus = self._focus
            conditioned = list(self._keytable_list)
            all_tables = list(self._all_keytables)

        lines = []
        if focus is not None:
            lines.append(f"focus now: {focus.app_name} - {focus.window_title!r}")
            # The one value a focus_path_pattern is written against, and the
            # one nothing else in the tool set reports in a form you can paste.
            lines.append(f"focus path: {focus.path}")
        else:
            lines.append("focus now: unknown (no window has been focused yet)")
        lines.append("")
        lines.append("Key tables, in definition order. Every table whose "
                     "condition matches is active at")
        lines.append("once and they merge in this order, so a later table "
                     "overrides the keys it binds.")

        by_table = {id(table): condition for condition, table in conditioned}
        shown = 0
        for table in all_tables:
            condition = by_table.get(id(table))
            if condition is None:
                where = "no condition - a multi-stroke table, reached from a key"
                state = "     "
            else:
                where = _condition_text(condition)
                state = "  *  " if condition.check(focus) else "     "
            lines.append("")
            lines.append(f"{state}{table.name or '(unnamed)'}: {where}")
            if not table.table:
                lines.append("       (nothing bound)")
            for key, action in table.table.items():
                if shown >= limit:
                    lines.append(f"       ... more, stopped at limit={limit}")
                    return "\n".join(lines)
                shown += 1
                lines.append(f"       {_key_text(key)} -> {action!r}")

        lines.append("")
        lines.append("  *  = its condition matches the focus above")
        return "\n".join(lines)

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
        if event.down and event.kind == "real" and event.vk == self._escape_vk() \
           and self._modal_input is None:
            # Not while a candidate window holds the keyboard: Esc there is
            # "close this window", and the window is the thing the user is
            # looking at.  Without the guard a background action would eat
            # the Esc and the window would stay up (discussion #112).
            if ThreadedAction.cancel_all():
                # Consumed only when it actually stopped something: swallowing
                # every Esc would change what the focused application sees.
                return True

        with self._lock:
            if event.down:
                return bool(self._on_key_down(event.vk))
            else:
                return bool(self._on_key_up(event.vk))

    def read_focus(self) -> Focus | None:
        """Ask the platform where the focus is *now*.

        `focus` is the snapshot taken during key dispatch, which is what a
        key table should be resolved against; this bypasses it for a caller
        that needs to notice a change no keystroke reported - an open
        candidate window watching for the user to move somewhere else.

        lazydocs: ignore
        """
        try:
            return self._focus_provider.get_focus()
        except Exception:
            logger.debug("Focus read failed.")
            return None

    def cursor_pos(self) -> tuple[int, int] | None:
        """The pointer position in portable top-left screen pixels - the same
        space `WindowHandle.frame_px()` and `screen_frames()` report, so the
        three compare directly. None where the platform does not offer it.

        lazydocs: ignore
        """
        try:
            return self._hook.cursor_pos()
        except (NotImplementedError, Exception):
            return None

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

    def on_mouse_event(self, kind: str = "button") -> None:
        """InputHook on_mouse callback: physical mouse button/wheel input
        cancels a pending one-shot modifier (keyhac-win behavior - clicking
        while holding a one-shot key means the hold was a drag/click
        modifier, not a tap).

        `on_mouse_button` is the UI's wiring point on the same signal, and it
        hears about **buttons only**. A wheel turn cancels a one-shot the same
        way a click does, but it is not the user going anywhere: macOS scrolls
        the window under the pointer without focusing it, so an open candidate
        window that dismissed on it would vanish whenever the user nudged a
        background list. Spotlight does not, and neither does this.

        The observer is called outside the lock: it does UI work, and the
        engine has nothing left to protect by then.

        lazydocs: ignore
        """
        with self._lock:
            self._last_keydown = None
        if kind != "button":
            return
        observer = self.on_mouse_button
        if observer is not None:
            try:
                observer()
            except Exception:
                logger.error(f"on_mouse_button callback failed:\n"
                             f"{traceback.format_exc()}")

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

        # A key grab (push_modal_input) outranks every table: the candidate
        # window that owns it is not focused, so this is the only route its
        # keystrokes have.  Modifier keys fall through - their bookkeeping
        # already ran in the caller, and consuming them here would strand
        # the modifier state of the application underneath.
        if self._modal_input is not None and key.vk not in self._vk_mod_map:
            if key.down and not key.oneshot:
                try:
                    self._modal_input(key)
                except Exception:
                    logger.error(f"Modal input handler failed:\n"
                                 f"{traceback.format_exc()}")
                    self._modal_input = None
            return True

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
            self._cancel_oneshot_win_alt()
            action()

        elif isinstance(action, KeyTable):
            self._cancel_oneshot_win_alt()
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
    # Modal input (spike - discussion #112)

    def push_modal_input(self, handler) -> None:
        """Route every non-modifier key to `handler` until it is popped.

        The mechanism a non-activating candidate window needs: the window
        never takes OS keyboard focus, so its keystrokes have to arrive
        through the hook that is already installed.

        A grab is the same kind of state a multi-stroke prefix already is -
        "the next keystroke resolves somewhere other than the active key
        tables, and unmatched keys are consumed rather than passed through"
        - differing in only two properties: it does not leave after one key,
        and it has a catch-all instead of a table.  So the two are kept
        mutually exclusive by construction: pushing a grab disarms any armed
        prefix, and there is never a prefix and a grab up at once.  Whether
        they should share one slot outright, rather than one policy, is the
        open question this spike exists to inform.

        Esc is the one key that does not arrive here first: `on_key_event`
        offers it to a running `ThreadedAction` before the tables are
        consulted, and consumes it if that stopped something.  With a grab
        up and an action running, Esc therefore cancels the action and the
        candidate window stays open.  That ordering is deliberate for the
        action, and probably wrong for the window; it is one of the
        decisions a real implementation has to make explicitly.

        Args:
            handler: Called with the `KeyCondition` of each non-modifier key
                *down* (never a one-shot echo, never a key up).  Modifier
                keys keep their normal bookkeeping so the handler sees
                correct modifier state; every non-modifier key is consumed,
                so the focused application sees nothing while the grab is up.

        lazydocs: ignore
        """
        self._leave_multi_stroke()
        self._modal_input = handler

    def pop_modal_input(self) -> None:
        """Release the grab pushed by :meth:`push_modal_input`.

        lazydocs: ignore
        """
        self._modal_input = None

    def modal_input_active(self) -> bool:
        """Whether a key grab is up.

        lazydocs: ignore
        """
        return self._modal_input is not None

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

    def _cancel_oneshot_win_alt(self):
        """Mark a held lone Win/Alt as used, so releasing it does nothing.

        An action that emits no key output leaves the OS with a Win or Alt
        that went down and comes back up with nothing in between - the key
        the user actually pressed was consumed - and Windows reads that as a
        lone tap: the Start menu opens, or the menu bar takes focus. Injecting
        a Ctrl tap before the action runs is what keyhac-win did here
        (_cancelOneshotWinAlt), for the same two cases: a callable, and
        entering a multi-stroke table.

        Key output does not need this: InputContext.send_modifier_keys emits
        the same tap while reconciling the modifiers around its batch.
        """
        if self.platform != "windows":
            return
        if mod_eq(self._modifier, MODKEY_ALT) or mod_eq(self._modifier, MODKEY_WIN):
            with self.get_input_context() as ctx:
                ctx.send_modifier_keys(self._modifier | MODKEY_CTRL_L)

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

    # ------------------------------------------------------------------
    # IME access (config-facing; see keyhac.platform.base.ImeProvider)

    def get_ime_status(self) -> bool | None:
        """Get whether the IME is on for whatever holds the input focus.

        There is no window argument on purpose: macOS can only ever address
        the current input source, so naming a window would mean two different
        contracts on the two OSes.

        Returns:
            True when the IME is on, False when it is off, or None when the
            state cannot be determined - no IME is installed or reachable, or
            (Windows) a TSF-only IME does not answer the IMM32 query.

        Note:
            UI-thread only.  "Off" is the same answer for two different
            situations, on both OSes: an IME that is installed and closed,
            and no IME in the picture at all - a plain keyboard layout on
            Windows, a plain layout or an input method's Roman mode on macOS.
        """
        if self.ime_provider is None:
            return None
        return self.ime_provider.get_status()

    def set_ime_status(self, on: bool) -> bool:
        """Turn the IME on or off for whatever holds the input focus.

        Args:
            on: True to turn the IME on, False to turn it off.

        Returns:
            Whether the requested state was actually reached - the result is
            read back rather than assumed, so False means the IME declined or
            there was none to ask.

        Note:
            UI-thread only, and it takes effect **at once** - unlike key
            output, which `InputContext` only queues for the application.
            Wrapping a `send_key` batch in "off ... back on" therefore does
            not work: the restore lands before the keys do and they are
            composed anyway.  Use `InputContext.send_text` for literal text,
            which the IME does not intercept.

            The two OSes differ in how far "on" reaches:
            macOS selects a Japanese input source even from a US layout,
            while Windows only opens an IME that the focused window is
            already typing under - asking for "on" while a plain layout like
            en-US is active returns False rather than switching the input
            language, which is the user's own Win+Space to give.

            Whether a change also affects other applications is the user's
            OS setting ("Let me use a different input method for each app
            window" on Windows, "Automatically switch to a document's input
            source" on macOS), not something Keyhac decides.
        """
        if self.ime_provider is None:
            return False
        return self.ime_provider.set_status(on)

    def app_control_running_apps(self):
        """[(app_name, pid)] via the platform (empty when unavailable).

        lazydocs: ignore
        """
        if self.platform == "mac":
            from keyhac.platform.mac.uielement import UIElement
            return UIElement.get_running_applications()
        return []

    # ------------------------------------------------------------------
    # The MCP endpoint
    #
    # There is no registry of named actions here any more. A model reaches an
    # action by finding the class in `extensions/` (keyhac/mcp/extensions.py),
    # which needs no `config.py` line at all - so `register_action`, whose whole
    # job was to add that line, was removed rather than kept as a second way in.
    # What a `config.py` still does is bind a key, and a key binding was never
    # what registration bought.

    @property
    def mcp_server_running(self) -> bool:
        """Whether the endpoint is currently listening.

        lazydocs: ignore
        """
        return self._mcp_server is not None

    def start_mcp_server(self, port: int = 0) -> None:
        """Start the AI-integration endpoint on localhost, for a while.

        The switch is the console's **AI Integration: MCP Server** checkbox, or
        the tray menu's *AI Integration > MCP Server*; this is the mechanism
        behind it.
        There is deliberately no configuration API: an endpoint that reads every
        window you have open, and that can write and run action code, should be
        visibly on or visibly off, and a line in the middle of a config file
        tells you what was asked for once, never what is true now.

        **It stops itself after :data:`_AUTHORING_WINDOW`**, and is not
        remembered across restarts. Both follow from what the feature is for:
        an agent is used *while writing* an action, and the action it produces
        then runs with no model involved (doc/dev/ai-integration.md §1). So an
        endpoint still listening a day later is not serving anything - it is
        only still reading every window you open, which is the larger of the
        two exposures here and the one least worth leaving armed.

        Fixed from the moment it is switched on rather than extended by use, so
        that whatever is driving the endpoint cannot hold its own permission
        open by working periodically.

        Off until something asks. The endpoint binds to 127.0.0.1 only and
        every request carries a token published - readable by this user alone -
        beside the configuration. `keyhac-mcp-bridge` reads that file on every
        request, so a window that closes and is reopened on a new port needs no
        configuration change.

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

        # A timer rather than a deadline checked at each request: what expiry
        # has to do here is *stop listening*, and nothing arrives to trigger a
        # lazy check once the conversation has moved on - which is exactly the
        # state this exists to end. One timer, cancelled by stop_mcp_server.
        self._mcp_timer = threading.Timer(_AUTHORING_WINDOW, self._mcp_expired)
        self._mcp_timer.daemon = True
        self._mcp_timer.start()

    def _mcp_expired(self) -> None:
        """The window ran out. Say so, then close it.

        Named rather than a lambda so the console line reads as a timeout and
        not as something the operator did - a switch that turns itself off with
        no explanation is the cost this design accepts, and one log line is what
        pays it back.
        """
        logger.info(f"MCP server stopped ({_AUTHORING_WINDOW // 60} minute "
                    f"timeout). Tick AI Integration again to reopen it.")
        self.stop_mcp_server()

    def stop_mcp_server(self) -> None:
        """Stop the endpoint and remove its published token.

        lazydocs: ignore
        """
        timer, self._mcp_timer = self._mcp_timer, None
        if timer is not None:
            timer.cancel()
        server, self._mcp_server = self._mcp_server, None
        if server is not None:
            server.stop()

    @property
    def config_path(self) -> str:
        """The configuration script this run loads.

        lazydocs: ignore
        """
        return self._config_path

    @property
    def extensions_dir(self) -> str:
        """``extensions/`` beside config.py: on sys.path, and re-imported on
        every reload.

        lazydocs: ignore
        """
        return os.path.join(
            os.path.dirname(self._config_path or ""), "extensions")

    @property
    def ui(self):
        """The action-facing UI API - see doc/action-api.md.

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
