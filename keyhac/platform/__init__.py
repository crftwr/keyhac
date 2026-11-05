"""Platform selection for the few things that are not behind an object.

`keyhac/platform/base.py` defines the interfaces main() instantiates per OS.
This module is for the handful of operations that belong to the platform but
have no instance to hang off - a thread announcing itself to the OS, and
whatever else has to happen once per thread rather than once per app.
"""

import contextlib
import sys


@contextlib.contextmanager
def worker_thread_context():
    """Prepare the calling thread for platform calls, and undo it on the way out.

    Wrapped around the body of any worker that reaches into the accessibility
    APIs, so that `keyhac/ui/` can do the right thing per OS without knowing
    what the right thing is.

    On Windows a worker needs its own COM apartment
    (`keyhac.platform.win.uielement.com_worker_thread` says what for). On
    macOS there is nothing to do: an autorelease pool was the obvious
    candidate and measurement said no - a worker walking the AX tree with no
    pool of its own held its RSS flat, because PyObjC wraps each call in one.
    """
    if sys.platform == "win32":
        from keyhac.platform.win import worker_thread_context as win_context
        with win_context():
            yield
    else:
        yield


def native_sampler():
    """What records this process's native stacks, for keyhac.core.profiler:
    ``sampler(stop_event, limit_seconds, path)`` returning what went wrong or
    None. None if this OS has nothing built in that can."""
    if sys.platform == "darwin":
        from keyhac.platform.mac.diagnostics import sample_native
        return sample_native
    return None


def process_memory():
    """What the OS says this process uses, as a `base.ProcessMemory`."""
    if sys.platform == "darwin":
        from keyhac.platform.mac.diagnostics import process_memory as read
    elif sys.platform == "win32":
        from keyhac.platform.win.diagnostics import process_memory as read
    else:
        from keyhac.platform.base import ProcessMemory
        return ProcessMemory()
    return read()


def native_memory_report():
    """What writes the OS's own breakdown of this process's memory, for
    keyhac.core.memory_report: ``report(path)`` returning what went wrong or
    None. None if this OS has nothing built in that can."""
    if sys.platform == "darwin":
        from keyhac.platform.mac.diagnostics import write_footprint
        return write_footprint
    return None


def native_memory_categories():
    """What reads the report `native_memory_report` wrote as {category: bytes},
    so that two reports can be compared. None where that has no report."""
    if sys.platform == "darwin":
        from keyhac.platform.mac.diagnostics import footprint_categories
        return footprint_categories
    return None
