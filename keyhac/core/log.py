"""Console logging.

M1: log records go to an in-memory ring buffer (for the future PuiKit console
window) and are mirrored to stderr.  Ported in spirit from keyhac-mac
keyhac_console.py; the SwiftTerm console is replaced by the ring buffer +
stderr until M2.
"""

import sys
import logging
import threading
from collections import deque

_COLORS = {
    logging.DEBUG: "\033[38;2;128;128;128m",
    logging.INFO: "\033[38;2;200;200;200m",
    logging.WARNING: "\033[38;2;255;255;128m",
    logging.ERROR: "\033[38;2;255;128;128m",
    logging.CRITICAL: "\033[38;2;255;64;64m",
}
_RESET = "\033[0m"


class Console:
    """Ring buffer of console lines + named text slots ("lastKey", "focusPath").

    The M2 PuiKit console window will pull from this object.
    """

    max_lines = 1000

    _instance = None

    @classmethod
    def get_instance(cls) -> "Console":
        if cls._instance is None:
            cls._instance = Console()
        return cls._instance

    def __init__(self):
        self._lock = threading.Lock()
        self._lines: deque[str] = deque(maxlen=Console.max_lines)
        self._texts: dict[str, str] = {}
        self.log_level = logging.INFO
        self.mirror_stderr = True

    def write(self, s: str, log_level: int = 100) -> None:
        if log_level < self.log_level:
            return
        with self._lock:
            for line in s.splitlines():
                self._lines.append(line)
        if self.mirror_stderr:
            try:
                sys.stderr.write(s)
                if not s.endswith("\n"):
                    sys.stderr.write("\n")
            except Exception:
                pass

    def lines(self) -> list[str]:
        with self._lock:
            return list(self._lines)

    def set_text(self, name: str, text: str) -> None:
        with self._lock:
            self._texts[name] = text

    def get_text(self, name: str) -> str:
        with self._lock:
            return self._texts.get(name, "")


class _ConsoleLoggingHandler(logging.Handler):

    def emit(self, record):
        try:
            msg = self.format(record)
            color = _COLORS.get(record.levelno, _COLORS[logging.INFO])
            Console.get_instance().write(f"{color}{msg}{_RESET}", record.levelno)
        except Exception:
            self.handleError(record)


_configured = False


def getLogger(name: str) -> logging.Logger:
    """Get a logger wired to the Keyhac console."""
    global _configured
    root = logging.getLogger("keyhac")
    if not _configured:
        handler = _ConsoleLoggingHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        root.propagate = False
        _configured = True
    return root.getChild(name)


def set_debug(enabled: bool) -> None:
    Console.get_instance().log_level = logging.DEBUG if enabled else logging.INFO
