"""Where Keyhac keeps config.py and the state files that live beside it.

One directory holds everything: ``config.py``, ``extensions/``,
``clipboard.json``, ``settings.json``.  Three ways it is chosen, first match
winning:

1. **An explicit** ``--config PATH`` — the state files sit beside the named
   config, so a sandboxed or experimental run cannot touch the real
   ``~/.keyhac``.
2. **Portable mode (Windows)** — a ``config.py`` sitting next to
   ``Keyhac.exe`` makes the bundle's own directory the data directory.  This
   is keyhac-win 1.x's portable mode, unchanged in spirit: Keyhac on a USB
   stick carries its configuration and its clipboard history with it and
   writes nothing into the user profile.  Dropping a ``config.py`` next to the
   exe is the whole opt-in; deleting it reverts to ``~/.keyhac``.
3. ``~/.keyhac`` — the default on both OSes.

Portable mode has no macOS counterpart: an ``.app`` bundle is a signed,
read-only artifact that Gatekeeper re-validates, so writing state inside it is
not an option there.

Path resolution only — no OS calls.  The Windows-only first-run migration
offer that reads :func:`legacy_windows_data_dir` lives in
``keyhac/platform/win/migrate.py``, since it needs a message box.
"""

import os
import sys

#: The configuration script's filename, in every mode.
CONFIG_NAME = "config.py"

#: Directory the Windows launcher assembles Keyhac's own code into, relative to
#: the bundle root (windows_app/build.ps1 step 4; see launcher.c's layout map).
#: Its presence is what identifies a bundle root — see :func:`bundle_dir`.
_BUNDLE_APP_MARKER = os.path.join("app", "keyhac")

#: keyhac-win 1.x's data directory, under %APPDATA%.
_LEGACY_WIN_DIRNAME = "Keyhac"


class Paths:
    """The resolved config path and the directory holding it."""

    def __init__(self, config_path: str, portable: bool = False):
        self.config_path = os.path.abspath(config_path)
        self.data_dir = os.path.dirname(self.config_path)
        self.portable = portable

    def state_file(self, name: str) -> str:
        """A state file (clipboard.json, settings.json) beside the config."""
        return os.path.join(self.data_dir, name)

    def __repr__(self) -> str:
        return (f"Paths(config_path={self.config_path!r}, "
                f"portable={self.portable!r})")


def default_data_dir() -> str:
    """``~/.keyhac`` — where Keyhac 2 keeps everything unless told otherwise."""
    return os.path.expanduser(os.path.join("~", ".keyhac"))


def bundle_dir() -> str | None:
    """The directory holding ``Keyhac.exe`` when running from the Windows
    bundle, else None.

    Identified by the bundle's *layout* (``<root>\\app\\keyhac\\``) rather than
    by the executable's name: a user who renamed ``Keyhac.exe`` still gets
    portable mode, and running from source — where ``sys.executable`` is a
    plain (possibly venv) ``python.exe`` — never mistakes the interpreter's
    directory for a bundle root.
    """
    if sys.platform != "win32":
        return None
    executable = getattr(sys, "executable", "") or ""
    if not executable:
        return None
    root = os.path.dirname(os.path.abspath(executable))
    if os.path.isdir(os.path.join(root, _BUNDLE_APP_MARKER)):
        return root
    return None


def portable_dir() -> str | None:
    """The bundle directory when it holds a ``config.py`` (portable mode is
    on), else None.  Presence of that file is the entire opt-in."""
    root = bundle_dir()
    if root is None:
        return None
    if os.path.isfile(os.path.join(root, CONFIG_NAME)):
        return root
    return None


def legacy_windows_data_dir() -> str | None:
    """``%APPDATA%\\Keyhac`` — keyhac-win 1.x's data directory — when it
    exists, else None.  Keyhac 2 never reads from it; it is only the source
    offered on a first run (see ``platform/win/migrate.py``)."""
    if sys.platform != "win32":
        return None
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    legacy = os.path.join(appdata, _LEGACY_WIN_DIRNAME)
    return legacy if os.path.isdir(legacy) else None


def resolve(config_arg: str | None = None) -> Paths:
    """Decide where this run reads and writes.  ``config_arg`` is ``--config``
    as given on the command line (None when it was not)."""
    if config_arg:
        return Paths(config_arg)
    portable = portable_dir()
    if portable is not None:
        return Paths(os.path.join(portable, CONFIG_NAME), portable=True)
    return Paths(os.path.join(default_data_dir(), CONFIG_NAME))
