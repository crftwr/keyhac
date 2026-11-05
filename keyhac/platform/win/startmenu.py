"""The applications the Start menu lists, by display name.

`launch="XeFM"` has to find a Microsoft Store application, whose install path
is impractical to write, and has to prefer it over a same-named command on
PATH (issue #319).  The shell's virtual Applications folder (`shell:AppsFolder`)
lists what the Start menu lists, packaged and desktop alike, and each entry's
parsing name is what `shell:AppsFolder\\<name>` launches.  No API goes from a
display name to an entry, so the folder has to be enumerated.

Enumerating costs too much to do on every press, though not so much that it
needs anything clever.  Measured on 74 entries in an emulated x64 Python on
Windows on Arm: 436 ms the first time in a process, about 150 ms after that
(`Get-StartApps`, which says the same, 5.6 s).  The listing is therefore kept
in memory for the life of the process and never expires, and the two ways it
can go wrong repair themselves: a name it does not hold scans again, which is
what finds an application installed since, and an entry that will not launch
is reported by the caller through forget_start_menu_apps().  Only launch()
gets here and it runs on a ThreadedAction's worker, so a scan never holds up
the keyboard hook.

COM through vtable slots, as in uielement.py.  The slot numbers come from
ShObjIdl_core.h; tests/test_win_start_menu.py checks the result against
Get-StartApps.
"""

from __future__ import annotations

import ctypes
import sys
import threading

from keyhac.core import log

logger = log.getLogger("WinStartMenu")

#: What marks a name as a file for the shell to find rather than a Start menu
#: entry to look up.  A list rather than "has a suffix", because display names
#: have dots of their own: "Python 3.14 (64-bit)", "Node.js".
_FILE_SUFFIXES = (".exe", ".bat", ".cmd", ".com", ".lnk", ".msc")

_lock = threading.Lock()
_apps: dict[str, list[str]] | None = None

if sys.platform == "win32":
    from keyhac.platform.win.uielement import (
        GUID, _raw_com_call, _release, com_worker_thread)

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.SHGetKnownFolderItem.argtypes = [
        ctypes.POINTER(GUID), ctypes.c_uint32, ctypes.c_void_p,
        ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetKnownFolderItem.restype = ctypes.c_long

    _FOLDERID_APPS_FOLDER = "{1e87508d-89c2-42f0-8a7e-645a0f50ca58}"
    _IID_ISHELLITEM = "{43826d1e-e718-42ee-bc55-a1e261c37bfe}"
    _IID_IENUMSHELLITEMS = "{70629033-e363-4a28-a567-0db78006e6d7}"
    _BHID_ENUM_ITEMS = "{94f60519-2850-4924-aa5a-d15e84868039}"

    # IShellItem
    _BIND_TO_HANDLER = 3
    _GET_DISPLAY_NAME = 5
    # IEnumShellItems
    _NEXT = 3

    _SIGDN_NORMALDISPLAY = 0
    _SIGDN_PARENTRELATIVEPARSING = 0x80018001


def _display_name(item, sigdn: int) -> str | None:
    out = ctypes.c_void_p()
    hr = _raw_com_call(item, _GET_DISPLAY_NAME, ctypes.c_long,
                       [ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)],
                       sigdn, ctypes.byref(out))
    if hr != 0 or not out:
        return None
    try:
        return ctypes.wstring_at(out)
    finally:
        ole32.CoTaskMemFree(out)


def _enumerate_apps_folder() -> list[tuple[str, str]]:
    folder = ctypes.c_void_p()
    hr = shell32.SHGetKnownFolderItem(
        ctypes.byref(GUID(_FOLDERID_APPS_FOLDER)), 0, None,
        ctypes.byref(GUID(_IID_ISHELLITEM)), ctypes.byref(folder))
    if hr != 0:
        raise OSError(f"SHGetKnownFolderItem: 0x{hr & 0xFFFFFFFF:08x}")
    enumerator = ctypes.c_void_p()
    try:
        hr = _raw_com_call(
            folder, _BIND_TO_HANDLER, ctypes.c_long,
            [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(GUID),
             ctypes.POINTER(ctypes.c_void_p)],
            None, ctypes.byref(GUID(_BHID_ENUM_ITEMS)),
            ctypes.byref(GUID(_IID_IENUMSHELLITEMS)), ctypes.byref(enumerator))
        if hr != 0:
            raise OSError(f"BindToHandler: 0x{hr & 0xFFFFFFFF:08x}")
        entries = []
        while True:
            item = ctypes.c_void_p()
            fetched = ctypes.c_ulong()
            hr = _raw_com_call(
                enumerator, _NEXT, ctypes.c_long,
                [ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p),
                 ctypes.POINTER(ctypes.c_ulong)],
                1, ctypes.byref(item), ctypes.byref(fetched))
            if hr != 0 or not fetched.value:  # S_FALSE: the end of the list
                return entries
            try:
                name = _display_name(item, _SIGDN_NORMALDISPLAY)
                parsing = _display_name(item, _SIGDN_PARENTRELATIVEPARSING)
            finally:
                _release(item)
            if name and parsing:
                entries.append((name, parsing))
    finally:
        _release(enumerator)
        _release(folder)


def scan_start_menu() -> list[tuple[str, str]]:
    """Every entry of the Applications folder as (display name, parsing name).

    Raises:
        OSError: The folder could not be opened or enumerated.
    """
    with com_worker_thread():
        return _enumerate_apps_folder()


def _index(entries) -> dict[str, list[str]]:
    apps: dict[str, list[str]] = {}
    for name, parsing in entries:
        apps.setdefault(name.casefold(), []).append(parsing)
    for targets in apps.values():
        # The Store application first.  A packaged entry's parsing name is
        # Family_hash!AppId; nothing else has a "!" in it.  sort() is stable,
        # so ties stay in listing order.
        targets.sort(key=lambda parsing: "!" not in parsing)
    return apps


def _could_be_entry_name(name: str) -> bool:
    """False for what only the shell can resolve - a path, a URL, `foo.exe` -
    which must not cost a scan and must keep meaning what it has always meant."""
    if any(mark in name for mark in ("\\", "/", ":")):
        return False
    return bool(name) and not name.lower().endswith(_FILE_SUFFIXES)


def find_start_menu_app(name: str) -> str | None:
    """The parsing name of the Start menu entry called `name`, or None.

    The whole display name, ignoring case: no wildcards, which could launch
    something the user did not mean.  Same name twice: the Store application,
    and a warning, because the user may have meant the other one.
    """
    global _apps
    if not _could_be_entry_name(name):
        return None
    key = name.casefold()
    with _lock:
        if _apps is None or key not in _apps:
            try:
                _apps = _index(scan_start_menu())
            except OSError as e:
                logger.warning(f"Could not read the Start menu's applications: {e}")
                return None
        targets = _apps.get(key)
    if not targets:
        return None
    if len(targets) > 1:
        logger.warning(f"The Start menu has {len(targets)} entries called "
                       f"{name!r}; launching {targets[0]}.")
    return targets[0]


def forget_start_menu_apps() -> None:
    """Drop the kept listing: an entry from it would not launch."""
    global _apps
    with _lock:
        _apps = None
