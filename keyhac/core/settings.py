"""Persistent app settings - the keyhac2 analog of keyhac-win's keyhac.ini.

A flat JSON dict of small UI-state values (currently the console window's
visibility; PuiKit's frame autosave owns the window *geometry* separately).
Writes are write-through: the file is tiny and changes are rare, so every
changed set() rewrites it immediately - the state survives however the
process later dies, which matters because quitting from the tray is not the
only way Keyhac ends.

Not for user configuration - that is config.py.  This file is state the app
itself remembers between runs.
"""

import json
import os

from keyhac.core import log

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
            os.makedirs(os.path.dirname(self.filename), exist_ok=True)
            with open(self.filename, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError as e:
            logger.warning(f"Could not write {self.filename}: {e}")
