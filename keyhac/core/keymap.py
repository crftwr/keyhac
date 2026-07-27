"""The Keymap engine.

Ported from keyhac-mac keyhac_main.py (Keymap), decoupled from keyhac_core:
the OS is reached only through the InputHook / FocusProvider interfaces, so
the engine runs unmodified on Windows, macOS, and in tests (FakeInputHook).
"""

import functools
import operator
import os
import threading
import traceback
from typing import Callable

from keyhac.core.const import *
from keyhac.core.vk import init_key_names, get_key_names
from keyhac.core.key import KeyCondition, KeyTable
from keyhac.core.focus import FocusCondition
from keyhac.core.input import InputContext
from keyhac.core import log
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
    """Manages key tables and executes key action translations."""

    _instance = None

    @staticmethod
    def get_instance() -> "Keymap":
        return Keymap._instance

    def __init__(self,
                 hook: InputHook,
                 focus_provider: FocusProvider,
                 platform: str,
                 config_path: str = None,
                 template_path: str = None):

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

        # Wired by main(): platform services + clipboard history + UI hooks
        self.app_control = None             # platform AppControl
        self._clipboard_history = None      # core ClipboardHistory
        self.on_enter_multi_stroke = None   # callable(name) - balloon help
        self.on_leave_multi_stroke = None   # callable()

        from keyhac.core.replay import KeyReplayBuffer
        self.replay_buffer = KeyReplayBuffer()

        Keymap._instance = self

    # ------------------------------------------------------------------
    # Configuration

    def configure(self) -> None:
        """Load (or reload) the configuration file and rebuild the keymap."""

        with self._lock:

            if self._vk_mod_map:
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
            self._vk_mod_map = dict(get_key_names().modifier_vk_map)

            logger.info("Loading configuration script.")

            os.makedirs(os.path.join(os.path.dirname(self._config_path), "extensions"), exist_ok=True)

            try:
                self.config = Config(self._config_path, self._template_path)
                self.config.call("configure", self)
            except Exception:
                print()
                logger.error(f"Loading configuration script failed:\n{traceback.format_exc()}")
                return

            self._warn_unreachable_modifiers()

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
        """Replace a key with a different key (pre-keytable substitution)."""
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
        """Define a user modifier key (User0..User3, or add keys to standard
        modifiers)."""
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
        class_name / custom_condition_func) the table activates automatically
        when the condition is met.  With no condition, the table is detached
        and can be assigned to a key to form a multi-stroke table.
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
        """Get a key input context to send a batch of virtual key events."""
        return InputContext(self, replay)

    # ------------------------------------------------------------------
    # Hook entry points (called synchronously on the event-loop thread)

    def on_key_event(self, event: KeyEvent) -> bool:
        """InputHook on_key callback.  Returns True to consume the event."""
        with self._lock:
            if event.down:
                return bool(self._on_key_down(event.vk))
            else:
                return bool(self._on_key_up(event.vk))

    def on_hook_restored(self) -> None:
        """InputHook on_restored callback."""
        with self._lock:
            logger.warning("Key hook timed out and has been restored.")
            # Modifier key state is not reliable anymore. Resetting.
            self._modifier = 0

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
        """Portable snapshot of the current keyboard focus."""
        return self._focus

    def app_control_running_apps(self):
        """[(app_name, pid)] via the platform (empty when unavailable)."""
        if self.platform == "mac":
            from keyhac.platform.mac.uielement import UIElement
            return UIElement.get_running_applications()
        return []

    @property
    def clipboard_history(self):
        """The ClipboardHistory object (None while running without one)."""
        return self._clipboard_history
