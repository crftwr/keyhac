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
        self._lines: deque[tuple[str, int]] = deque(maxlen=Console.max_lines)
        self._pending: list[tuple[str, int]] = []  # lines the UI has not pulled yet
        self._texts: dict[str, str] = {}
        self.log_level = logging.INFO
        self.mirror_stderr = True

    def write(self, s: str, log_level: int = 100) -> None:
        """Append plain text (no ANSI codes) with a logging level; the stderr
        mirror colors by level, the console window styles by level."""
        if log_level < self.log_level:
            return
        with self._lock:
            for line in s.splitlines():
                self._lines.append((line, log_level))
                self._pending.append((line, log_level))
        if self.mirror_stderr:
            try:
                color = _COLORS.get(log_level, "")
                reset = _RESET if color else ""
                sys.stderr.write(f"{color}{s}{reset}")
                if not s.endswith("\n"):
                    sys.stderr.write("\n")
            except Exception:
                pass

    def lines(self) -> list[tuple[str, int]]:
        with self._lock:
            return list(self._lines)

    def pull_lines(self) -> list[tuple[str, int]]:
        """Return and clear the lines appended since the last pull (single
        consumer: the console window's refresh tick)."""
        with self._lock:
            pending = self._pending
            self._pending = []
        return pending

    def set_text(self, name: str, text: str) -> None:
        with self._lock:
            self._texts[name] = text

    def get_text(self, name: str) -> str:
        with self._lock:
            return self._texts.get(name, "")


class _ConsoleLoggingHandler(logging.Handler):

    def emit(self, record):
        try:
            Console.get_instance().write(self.format(record), record.levelno)
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
