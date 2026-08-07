"""First-run offer to bring a Keyhac-for-Windows (1.x) config.py forward.

1.x kept its configuration in ``%APPDATA%\\Keyhac``; Keyhac 2 reads
``~/.keyhac/config.py`` (see ``keyhac/core/paths.py``).  An upgrading user who
just installs Keyhac 2 therefore meets the stock template and their years-old
config sitting somewhere they have no reason to look.

So on a first run only - no ``~/.keyhac/config.py`` yet, a
``%APPDATA%\\Keyhac\\config.py`` there - Keyhac offers to copy it across.  The
copy is deliberately *not* silent, because the two configs are not
interchangeable: 1.x's camelCase API has no compatibility shim in Keyhac 2, so
the copied file needs a translation pass before it loads (the console shows
the error until it gets one, and the previous keymap - none, on a first run -
stays active).  ``doc/migration-from-keyhac-win.md`` prescribes exactly this
move, "copy your config there before translating"; the dialog names it and
declining leaves the stock template in place.

A message box, not a PuiKit dialog: this runs before the console window
exists, and the same MessageBoxW fallback already serves the single-instance
notice (``instance.py``).
"""

import ctypes
import os
import shutil
import sys

from keyhac.core import log, paths

logger = log.getLogger("Migrate")

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    # Mandatory on 64-bit: the default c_int restype truncates HWND/HANDLE.
    user32.MessageBoxW.argtypes = (wintypes.HWND, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, wintypes.UINT)
    user32.MessageBoxW.restype = ctypes.c_int

    MB_YESNO = 0x04
    MB_ICONQUESTION = 0x20
    IDYES = 6

_PROMPT = """\
A Keyhac 1.x configuration was found at:

    {source}

Keyhac 2 reads {target} instead.

Copy it there now?

Note: Keyhac 2 uses a different (snake_case) configuration API, so the copied
file will need a translation pass before it loads - see
doc/migration-from-keyhac-win.md. Choosing No keeps the stock template, which
works as-is."""


def offer_config_migration(target_config_path: str) -> bool:
    """Offer to copy a 1.x ``config.py`` to ``target_config_path``.  Returns
    True if one was copied.

    A no-op unless this really is a first run with something to migrate: the
    target must not exist, and ``%APPDATA%\\Keyhac\\config.py`` must.  Any
    failure is logged and swallowed - a migration that cannot happen must not
    stop Keyhac from starting with the stock template.
    """
    if sys.platform != "win32":
        return False
    if os.path.exists(target_config_path):
        return False

    legacy_dir = paths.legacy_windows_data_dir()
    if legacy_dir is None:
        return False
    source = os.path.join(legacy_dir, paths.CONFIG_NAME)
    if not os.path.isfile(source):
        return False

    answer = user32.MessageBoxW(
        None,
        _PROMPT.format(source=source, target=target_config_path),
        "Keyhac - import your Keyhac 1.x configuration?",
        MB_YESNO | MB_ICONQUESTION)
    if answer != IDYES:
        logger.info(f"Keyhac 1.x config at {source} left in place "
                    "(starting from the template).")
        return False

    try:
        os.makedirs(os.path.dirname(target_config_path), exist_ok=True)
        shutil.copyfile(source, target_config_path)
    except OSError as e:
        logger.error(f"Could not copy {source} to {target_config_path}: {e}")
        return False

    logger.info(f"Copied the Keyhac 1.x config from {source}. It uses the 1.x "
                "API and needs migrating - see doc/migration-from-keyhac-win.md.")
    return True
