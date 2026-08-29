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
