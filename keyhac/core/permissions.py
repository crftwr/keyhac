"""Keeping the data directory readable by its owner and nobody else.

Everything Keyhac writes beside ``config.py`` is private by nature.
``clipboard.json`` is the sharpest case - it is a verbatim record of what the
operator copied, which on any given day includes a password pulled out of a
manager - but ``config.py`` holds whatever they wrote into it, and
``mcp.json`` holds a bearer token.  None of it is anyone else's business.

Left to the default umask (022 on both stock macOS and most Linux desktops),
each of those files is created 0644 and the directory 0755.  On macOS that is
not merely theoretical: ``$HOME`` is 0750 owned by group ``staff``, and every
local account is in ``staff`` by default, so a second user on the machine
could read the clipboard history.

Two halves, and the second is the one that matters:

* **Creation.** :func:`ensure_private_dir`, :func:`open_private` and
  :func:`copy_private` make new files 0600 and new directories 0700 *at
  creation*, never by a chmod afterwards - the window between the two is
  exactly when a credential would be world-readable.  (``mcp.json`` has been
  written this way since the endpoint shipped; these helpers generalise what
  it was already doing.)
* **The start-up sweep.** :func:`harden_data_dir` tightens what is already on
  disk, every run.  Creation modes alone would protect nobody who has run
  Keyhac before, since their directory already exists - and file modes do not
  stay put.  A tool that saves by writing a temporary file and renaming it
  over the original leaves the *new* file's mode behind, i.e. 0644 again; so
  the per-file 0600 is defence in depth and the directory's 0700 is the
  guarantee.

Windows has no POSIX mode bits.  ``os.chmod`` there only toggles the
read-only attribute, so a sweep would clear nothing it was asked to clear and
could mark files read-only; access is governed instead by the ACL inherited
from ``%USERPROFILE%``, which already excludes other users.  Every function
here is therefore a plain create with no mode work on Windows, so callers
need no platform branch of their own.
"""

import glob
import os
import shutil
import stat

from keyhac.core import log, paths

logger = log.getLogger("Permissions")

#: Cleared from every path the sweep touches: group and other, all access.
#: Owner bits are left alone, so an execute bit someone set on purpose stays.
_SHARED_BITS = 0o077

#: Mode for a directory Keyhac creates, and for a file it creates.
_DIR_MODE = 0o700
_FILE_MODE = 0o600

#: True where mode bits mean what POSIX says they mean.  See the module
#: docstring for why Windows opts out rather than doing something weaker.
_POSIX_MODES = os.name != "nt"

#: Files in the data directory the sweep fixes by name.  A list, not a walk of
#: everything present: with ``--config PATH`` the "data directory" is wherever
#: the operator pointed, which may be a source tree holding files that are not
#: ours to re-permission.
_PRIVATE_FILES = (
    paths.CONFIG_NAME,   # whatever the operator wrote, secrets included
    "clipboard.json",    # verbatim copied text - the sharpest of these
    "settings.json",
    "mcp.json",          # port + bearer token
    "instance.lock",
    "keyhac-error.log",
)

#: Backups the MCP write tools leave beside what they rewrite
#: (``config.py.bak-20260904-213500``), one generation per write.
_BACKUP_GLOB = "*.bak-*"

#: Subdirectory swept whole.  Unlike the data directory it is unambiguously
#: Keyhac's - Keyhac creates it - so its modules, and their backups, are all
#: ours to tighten.
_EXTENSIONS_DIRNAME = "extensions"


def ensure_private_dir(path: str) -> None:
    """Create ``path`` and any missing parents, owner-only, if it is absent.

    Nothing is done to a directory that already exists; the start-up sweep is
    what fixes those.  An empty ``path`` (``dirname`` of a bare filename) is a
    no-op rather than an error.
    """
    if not path:
        return
    os.makedirs(path, mode=_DIR_MODE, exist_ok=True)


def open_private(path: str, mode: str = "w", encoding: str | None = "utf-8"):
    """Open ``path`` for writing, creating it owner-only if it is absent.

    A drop-in for ``open(path, "w")``.  The mode applies at creation only, so
    an existing file keeps whatever it has - which is what the sweep is for.

    Args:
        path: File to write.
        mode: ``"w"`` (truncate), ``"a"`` (append), either with ``"b"``.
        encoding: Text encoding; ignored for a binary mode.
    """
    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if "a" in mode else os.O_TRUNC
    if "b" in mode:
        encoding = None
    descriptor = os.open(path, flags, _FILE_MODE)
    return os.fdopen(descriptor, mode, encoding=encoding)


def copy_private(src: str, dst: str) -> None:
    """Copy ``src`` onto ``dst``, creating ``dst`` owner-only if it is absent.

    ``shutil.copyfile`` copies the bytes and not the mode, so its destination
    is born at the umask's mercy - which is how a 0600 ``config.py`` ended up
    with a 0644 copy of itself sitting next to it after every MCP write.
    """
    with open(src, "rb") as source, open_private(dst, "wb") as target:
        shutil.copyfileobj(source, target)


def harden_data_dir(data_dir: str) -> None:
    """Clear group and other bits from the data directory and its contents.

    Called once per run, before anything reads or writes there.  Idempotent:
    after the first run that fixes an existing install it finds nothing to do
    and says nothing.  Failures are logged and skipped - a directory whose
    modes cannot be changed is not a reason to refuse to start.
    """
    if not _POSIX_MODES:
        return

    tightened = []

    # The directory itself only when it is ~/.keyhac, which is Keyhac's and
    # nothing else's.  A directory named by --config belongs to the operator;
    # tightening a source tree they pointed at would be a surprise, and the
    # files below are the sensitive part in any case.
    if os.path.abspath(data_dir) == os.path.abspath(paths.default_data_dir()):
        _tighten(data_dir, tightened)

    for name in _PRIVATE_FILES:
        _tighten(os.path.join(data_dir, name), tightened)
    for backup in glob.glob(os.path.join(data_dir, _BACKUP_GLOB)):
        _tighten(backup, tightened)

    extensions_dir = os.path.join(data_dir, _EXTENSIONS_DIRNAME)
    if os.path.isdir(extensions_dir):
        _tighten(extensions_dir, tightened)
        # followlinks stays off: a symlinked subdirectory leads out of the
        # tree we are entitled to re-permission.
        for root, dirnames, filenames in os.walk(extensions_dir):
            for entry in dirnames + filenames:
                _tighten(os.path.join(root, entry), tightened)

    if tightened:
        # One line, not one per path: an install carrying a few dozen
        # extensions and their backups would otherwise open its first upgraded
        # run with a wall of them.  The paths are there at debug level.
        logger.info(f"Made {len(tightened)} path(s) in {data_dir} "
                    "readable by their owner only.")


def _tighten(path: str, tightened: list) -> None:
    """Drop group/other bits from one path, if it exists and has any."""
    try:
        info = os.lstat(path)
    except OSError:
        return  # absent, or a directory we cannot stat through - nothing to do

    if stat.S_ISLNK(info.st_mode):
        # A symlink's own mode is not its target's, and chmod would follow it:
        # a config.py symlinked into a dotfiles repo is the operator's file
        # living at their chosen permissions, not ours to change.
        return

    mode = stat.S_IMODE(info.st_mode)
    wanted = mode & ~_SHARED_BITS
    if wanted == mode:
        return

    try:
        os.chmod(path, wanted)
    except OSError as e:
        logger.warning(f"Could not tighten permissions of {path}: {e}")
    else:
        tightened.append(path)
        logger.debug(f"Tightened {path}: {mode:04o} -> {wanted:04o}.")
