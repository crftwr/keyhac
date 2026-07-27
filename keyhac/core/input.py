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
    Real modifier state is released on entry as needed and restored on exit.

    usage:
        with keymap.get_input_context() as ctx:
            ctx.send_key("Cmd-Left")
            ctx.send_key("Cmd-Shift-Right")
    """

    def __init__(self, keymap, replay: bool = False):
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
        """Send a key stroke from a string expression (e.g. "Cmd-Left",
        "D-Shift", "U-Shift")."""
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
        """Send a key stroke by virtual key code."""
        if not self._entered:
            raise ValueError("Not in the context.")
        self._input_seq.append((vk, down))

    def send_text(self, s: str) -> None:
        """Type a literal string.  Like an unmodified send_key, held modifiers
        are released first (and restored when the context exits) - otherwise
        e.g. a physically held Fn turns the injected keystrokes into macOS
        system shortcuts (Fn/Globe-A opens the Dock)."""
        if not self._entered:
            raise ValueError("Not in the context.")
        self.send_modifier_keys(0)
        self._input_seq.append(("text", s))

    def send_modifier_keys(self, mod: int) -> None:
        """Emit modifier key downs/ups so the virtual modifier state matches
        the target state `mod`."""

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
        batch = []
        for item in seq:
            if item[0] == "text":
                if batch:
                    self._keymap._hook.send(batch, replay=self._replay)
                    batch = []
                self._keymap._hook.send_text(item[1])
            else:
                batch.append(item)
        if batch:
            self._keymap._hook.send(batch, replay=self._replay)
