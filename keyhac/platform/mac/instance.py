"""Single-instance guard - flock on a lock file, plus activating the running app.

Two Keyhac processes would each install a CGEventTap and both would act on
(and possibly re-inject) every key, so a second launch must not get as far as
installing one.  LaunchServices already refuses to launch the same .app bundle
twice, but that covers only Finder/Dock launches of the *same* bundle - a
`python -m keyhac` dev run beside the app, or two different builds, get
through.  The guard is an flock(LOCK_EX | LOCK_NB) on a file under ~/.keyhac:
the kernel drops the lock however the process dies, so there is no stale-lock
case (the file itself is left behind, holding nothing).

STATUS: written to spec; needs a live macOS pass.
"""

import os

from keyhac.core import log, permissions

logger = log.getLogger("MacInstance")

_LOCK_PATH = os.path.expanduser("~/.keyhac/instance.lock")


class InstanceLock:
    """Holds the locked file object for the process lifetime; the kernel
    drops the lock on exit.  release() exists for tests."""

    def __init__(self, file):
        self._file = file

    def release(self) -> None:
        if self._file is not None:
            self._file.close()  # closing drops the flock
            self._file = None


def acquire_instance_lock(path: str = _LOCK_PATH):
    """Try to become this user's Keyhac instance.  Returns an InstanceLock to
    keep referenced for the process lifetime, or None if another instance
    holds the lock."""
    import fcntl
    try:
        permissions.ensure_private_dir(os.path.dirname(path))
        # "a", not "w": mode "w" truncates on open, i.e. before the flock
        # attempt - a losing second instance would blank the holder's pid note.
        f = permissions.open_private(path, "a")
    except OSError as e:
        # Not being able to create ~/.keyhac is not evidence of another
        # instance; fail open rather than refuse to start.
        logger.warning(f"Cannot open {path}: {e}; "
                       "skipping the single-instance check.")
        return InstanceLock(None)
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    # Purely informational (the flock is the lock; this just aids debugging).
    f.truncate(0)
    f.write(str(os.getpid()))
    f.flush()
    return InstanceLock(f)


def notify_already_running() -> None:
    """Bring the running instance's app to the front (best-effort).  Only a
    bundled instance is discoverable this way; for a dev run the stderr
    message from main() is the feedback."""
    try:
        from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
        bundle_id = os.environ.get("KEYHAC_BUNDLE_ID", "crftwr.Keyhac2")
        apps = NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
        if apps:
            apps[0].activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
    except Exception as e:
        logger.debug(f"Could not activate the running instance: {e}")
