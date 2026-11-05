"""What the macOS system tools can say about this process that Python cannot.

They are Apple-signed with the right to inspect another process, so unlike
py-spy they work on the hardened-runtime bundle. Not every such tool is harmless
to a process running the keyboard hook: doc/dev/design-notes.md ("Developer
reports") has the measurements.
"""

import os
import signal
import subprocess
import threading

_SAMPLE = "/usr/bin/sample"

#: Milliseconds between `sample`'s stacks. Its default of 1 stopped a test
#: process for up to 18 ms at a time; at 10 it was up to 7.5 ms, which is what
#: a keystroke can meet during a capture.
_SAMPLE_INTERVAL_MS = 10


def sample_native(stop: threading.Event, limit: float, path: str) -> str | None:
    """Write this process's native stacks to ``path`` until ``stop`` is set or
    ``limit`` seconds have passed.

    Returns:
        What went wrong, or None.
    """
    try:
        process = subprocess.Popen(
            [_SAMPLE, str(os.getpid()), str(max(1, int(limit))),
             str(_SAMPLE_INTERVAL_MS), "-file", path],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
    except OSError as e:
        return str(e)
    stop.wait(limit)
    # An interrupted sample writes what it has and exits 0.
    process.send_signal(signal.SIGINT)
    try:
        out, err = process.communicate(timeout=60)
    except subprocess.TimeoutExpired as e:
        process.kill()
        process.communicate()
        return str(e)
    if process.returncode != 0:
        return (err or out).strip()[:500] or f"exit {process.returncode}"
    return None
