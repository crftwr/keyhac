"""Persistent app settings - the keyhac2 analog of keyhac-win's keyhac.ini.

A flat JSON dict of small UI-state values (the console window's visibility,
the size a chooser resize ended on; PuiKit's frame autosave owns the console's
own *geometry* separately, and the chooser has none to autosave - it is a new
window every time it opens).
Writes are write-through: the file is tiny and changes are rare, so every
changed set() rewrites it immediately - the state survives however the
process later dies, which matters because quitting from the tray is not the
only way Keyhac ends.

Not for user configuration - that is config.py.  This file is state the app
itself remembers between runs.
"""

import json
import os

from keyhac.core import log, permissions

logger = log.getLogger("Settings")

_MISSING = object()  # sentinel: "no stored value" (None is a storable value)


class Settings:
    """Load-once, write-through JSON settings store."""

    def __init__(self, filename: str = None):
        self.filename = filename or os.path.expanduser("~/.keyhac/settings.json")
        self._data: dict = {}
        self._load()

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        """Set and persist immediately; a no-op when the value is unchanged."""
        if self._data.get(key, _MISSING) == value:
            return
        self._data[key] = value
        self._save()

    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            with open(self.filename, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._data = data
            else:
                logger.warning(f"{self.filename}: not a JSON object; ignoring it.")
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            # A corrupt/unreadable file costs the remembered state, not the app.
            logger.warning(f"Could not read {self.filename}: {e}")

    def _save(self) -> None:
        try:
            permissions.ensure_private_dir(os.path.dirname(self.filename))
            with permissions.open_private(self.filename) as f:
                json.dump(self._data, f, indent=2)
        except OSError as e:
            logger.warning(f"Could not write {self.filename}: {e}")
