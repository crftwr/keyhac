"""InputContext - batched virtual key output.

Ported from keyhac-mac keyhac_input.py.  Differences:
- serializes against the hook via the Keymap engine lock (RLock) instead of a
  native Hook.acquire_lock
- flushes through the platform InputHook.send() batch API
"""

from keyhac.core.const import *
from keyhac.core.key import KeyCondition
from keyhac.core.vk import KeyNames, get_key_names


class InputContext:
    """A context manager to send virtual key strokes.

    Key events are accumulated and sent as one batch when the context exits.
    Physically held modifiers are released around the batch and restored
    afterwards, so ``ctx.send_key("Ctrl-C")`` works even while the modifiers
    of the binding that triggered it are still down.

    ``keymap.get_input_context()`` creates one.  It is safe to use from a
    ThreadedAction worker thread.

    ```python
    with keymap.get_input_context() as ctx:
        ctx.send_key("Cmd-Left")
        ctx.send_key("Cmd-Shift-Right")
    ```
    """

    def __init__(self, keymap, replay: bool = False):
        """Created by keymap.get_input_context().

        lazydocs: ignore
        """
        self._keymap = keymap
        self._replay = replay
        self._entered = False
        self._input_seq: list[tuple[int, bool]] = []  # (vk, down)

    def __enter__(self):
        self._keymap._lock.acquire()
        self._entered = True

        # Need to read the modifier state after taking the lock
        self._real_modifier = self._keymap._modifier
        self._virtual_modifier = self._keymap._modifier
        self._vk_mod_map = self._keymap._vk_mod_map
        return self

    def __exit__(self, exc_type, exc_value, tb):
        # Flush even when an exception occurred, so partially-sent modifier
        # state is always restored (keyhac-mac behavior).
        try:
            self._flush()
        finally:
            self._entered = False
            self._keymap._lock.release()

    def __str__(self):
        return str(self._input_seq)

    def send_key(self, s: str) -> None:
        """Send a key stroke from a key expression.

        Args:
            s: A key expression, e.g. "Cmd-Left", "D-Shift" (key down only)
                or "U-Shift" (key up only).  Modifiers go out as their
                left-side keys.

        Raises:
            ValueError: Used outside the context, or the expression names an
                unknown modifier or key.
        """
        if not self._entered:
            raise ValueError("Not in the context.")

        names = get_key_names()
        s = s.upper()

        mod = 0
        down = None

        token_list = s.split("-")

        for token in token_list[:-1]:
            token = token.strip()
            try:
                # Output always resolves to the physical left-side modifier
                mod |= KeyNames.str_to_mod(token, force_LR=True)
            except ValueError:
                if token == "D":
                    down = True
                elif token == "U":
                    down = False
                else:
                    raise ValueError(f"Unknown modifier: {token}") from None

        vk = names.str_to_vk(token_list[-1].strip())

        self.send_modifier_keys(mod)

        if down is True:
            self._input_seq.append((vk, True))
        elif down is False:
            self._input_seq.append((vk, False))
        else:
            self._input_seq.append((vk, True))
            self._input_seq.append((vk, False))

    def send_key_by_vk(self, vk: int, down: bool = True) -> None:
        """Send a key stroke by virtual key code.

        Args:
            vk: Virtual key code.
            down: True for key down, False for key up.

        Raises:
            ValueError: Used outside the context.
        """
        if not self._entered:
            raise ValueError("Not in the context.")
        self._input_seq.append((vk, down))

    def send_text(self, s: str) -> None:
        """Type a literal string, whatever characters it holds.

        Like an unmodified send_key, held modifiers are released first (and
        restored when the context exits) - otherwise e.g. a physically held Fn
        turns the injected keystrokes into macOS system shortcuts (Fn/Globe-A
        opens the Dock).

        Args:
            s: The text to type.

        Raises:
            ValueError: Used outside the context.
        """
        if not self._entered:
            raise ValueError("Not in the context.")
        self.send_modifier_keys(0)
        self._input_seq.append(("text", s))

    def send_mouse_move(self, dx: int, dy: int) -> None:
        """Move the mouse cursor by a relative offset.

        Injected as an absolute position, so pointer acceleration cannot
        distort the distance.  Unlike buttons and wheels, held modifiers stay
        held (keyhac-win behavior).

        Args:
            dx: Horizontal offset in pixels, positive = right.
            dy: Vertical offset in pixels, positive = down.

        Raises:
            ValueError: Used outside the context.
        """
        if not self._entered:
            raise ValueError("Not in the context.")
        self._input_seq.append(("mouse", ("move", dx, dy)))

    def send_mouse_button(self, button: str = "left",
                          down: bool | None = None) -> None:
        """Press, release or click a mouse button.

        Held modifiers are released first and restored when the context exits,
        so a modifier-bound click does not turn into a modified click
        (keyhac-win behavior).

        Args:
            button: "left", "right" or "middle".
            down: True to press, False to release, None to click.

        Raises:
            ValueError: Used outside the context, or an unknown button name.
        """
        if not self._entered:
            raise ValueError("Not in the context.")
        if button not in ("left", "right", "middle"):
            raise ValueError(f'Mouse button must be "left", "right" or '
                             f'"middle", not {button!r}')
        self.send_modifier_keys(0)
        if down is None:
            self._input_seq.append(("mouse", (button, True)))
            self._input_seq.append(("mouse", (button, False)))
        else:
            self._input_seq.append(("mouse", (button, down)))

    def send_mouse_wheel(self, notches: float) -> None:
        """Turn the vertical mouse wheel.

        Held modifiers are released first, like send_mouse_button.

        Args:
            notches: Wheel notches; positive = away from you, 1.0 = one notch.

        Raises:
            ValueError: Used outside the context.
        """
        if not self._entered:
            raise ValueError("Not in the context.")
        self.send_modifier_keys(0)
        self._input_seq.append(("mouse", ("wheel", notches)))

    def send_mouse_horizontal_wheel(self, notches: float) -> None:
        """Turn the horizontal mouse wheel.

        Args:
            notches: Wheel notches; positive = right, 1.0 = one notch.

        Raises:
            ValueError: Used outside the context.
        """
        if not self._entered:
            raise ValueError("Not in the context.")
        self.send_modifier_keys(0)
        self._input_seq.append(("mouse", ("hwheel", notches)))

    def send_modifier_keys(self, mod: int) -> None:
        """Emit modifier key downs/ups so the virtual modifier state matches
        the target state `mod`.

        lazydocs: ignore
        """

        # Key down modifiers that are missing
        for vk, modkey in self._vk_mod_map.items():
            # User modifiers are never physically emitted (except in replay
            # mode, where the original key must be reproduced)
            if (modkey & MODKEY_USER_ALL) and not self._replay:
                continue
            if not (modkey & self._virtual_modifier) and (modkey & mod):
                self._input_seq.append((vk, True))
                self._virtual_modifier |= modkey

        # Key up modifiers that must not be held
        for vk, modkey in self._vk_mod_map.items():
            if (modkey & MODKEY_USER_ALL) and not self._replay:
                continue
            if (modkey & self._virtual_modifier) and not (modkey & mod):
                self._input_seq.append((vk, False))
                self._virtual_modifier &= ~modkey

    def _flush(self):
        self.send_modifier_keys(self._real_modifier)
        seq, self._input_seq = self._input_seq, []

        # Consecutive items of one kind go out as one platform batch; a kind
        # change flushes, so overall ordering is preserved across the key /
        # text / mouse channels.
        keys = []
        mouse = []

        def flush_keys():
            if keys:
                self._keymap._hook.send(list(keys), replay=self._replay)
                keys.clear()

        def flush_mouse():
            if mouse:
                self._keymap._hook.send_mouse(list(mouse), replay=self._replay)
                mouse.clear()

        for item in seq:
            if item[0] == "text":
                flush_keys()
                flush_mouse()
                self._keymap._hook.send_text(item[1])
            elif item[0] == "mouse":
                flush_keys()
                mouse.append(item[1])
            else:
                flush_mouse()
                keys.append(item)
        flush_keys()
        flush_mouse()
