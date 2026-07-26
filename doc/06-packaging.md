# 06 — Packaging, launchers, distribution

## Languages: the complete answer

| Layer | Language | Size | Why |
|---|---|---|---|
| Application (engine, UI, platform bindings) | Python 3.13 | ~everything | ctypes covers Win32 (PuiKit's Windows backend proves it incl. COM/Direct2D); PyObjC covers Quartz/AX/AppKit |
| Embedding launcher, Windows (`keyhac.exe`) | C++ | ~150 lines | Port of keyhac-win `main.cpp`: PEP 587 `PyConfig` isolated init, module search paths, import `keyhac_boot`. Also: exe icon, DPI manifest. |
| Embedding launcher, macOS (`Keyhac.app` main) | C or minimal Swift | ~150 lines | Same PEP 587 pattern (keyhac-mac's `PythonBridge.cpp` shows it on macOS). A real bundle executable is **required** so Accessibility permission attaches to a stable identity — running under a generic `python3` binary would grant the permission to that interpreter, not to Keyhac. |
| Build scripts | Python | | port of `makefile.py` (win) + `copy_python_stdlibs.py` (mac) |

No ckit, no pyauto, no compiled extension of our own. The two launchers can share most
of their source (a single `launcher.c` with small per-OS `#ifdef`s is realistic since
both predecessors already use the same PyConfig API).

Dependencies (runtime): `puikit`, `pyobjc-framework-{Cocoa,Quartz,ApplicationServices}`
(mac), `pillow` (via puikit). Windows: no binary deps beyond CPython + puikit.

## Windows layout (inherits keyhac-win's proven scheme)

```
Keyhac/
  keyhac.exe            # launcher (PyConfig isolated; paths → modules/)
  modules/
    keyhac/             # our package, byte-compiled (optimize=2)
    Lib/                # trimmed CPython stdlib + site-packages (puikit, PIL)
    DLLs/               # python313.dll, *.pyd
  extension/            # user drop-in dir (on sys.path)
  _config.py            # template
  doc/
```

- Byte-compile + trim as `makefile.py` does today (exclude test/idlelib/etc.).
- Portable mode preserved: if `config.py` exists next to `keyhac.exe`, data path = exe
  dir; else `~/.keyhac/` (**change from keyhac-win's `%APPDATA%\Keyhac`** — unified with
  macOS; first-run migration: if `%APPDATA%\Keyhac` exists and `~/.keyhac` doesn't,
  offer to copy `config.py` and keep going).
- Distribution: zip (as today). Optional later: signed installer.

## macOS bundle (inherits keyhac-mac's proven scheme)

```
Keyhac.app/Contents/
  MacOS/Keyhac                  # launcher binary
  Frameworks/Python             # embedded Python.framework dylib (@rpath rewrite)
  Resources/
    keyhac/                     # our package
    PythonLibs/python3.13/      # trimmed stdlib (copy_python_stdlibs.py approach)
    site/                       # puikit + pyobjc
    _config.py
  Info.plist                    # LSUIElement=YES (agent app), CFBundleIdentifier crftwr.Keyhac2 (TBD)
  entitlements: no sandbox, hardened runtime, disable-library-validation
```

- `install_name_tool` rewrite + isolated `PyConfig` with bundled paths — copy the
  working recipe from keyhac-mac's Xcode run-script phase, but drive it from a plain
  `Makefile`/script (no Xcode project needed once the Swift app layer is gone; the
  launcher can be built with `clang` directly).
- Codesign + notarization required for distribution (hook + AX permission make an
  unsigned app painful). Decide Developer ID signing in M5.
- PyObjC and puikit vendored into `Resources/site` (pip install --target at build).

## Data & config paths (unified)

| Item | Path (both OSes) |
|---|---|
| Config | `~/.keyhac/config.py` (template copied on first run) |
| Extensions | `~/.keyhac/extensions/` (on sys.path) |
| Clipboard history | `~/.keyhac/clipboard.json` |
| App settings | `~/.keyhac/settings.json` |
| Logs (optional file log) | `~/.keyhac/log/` |
| Windows portable mode | everything next to `keyhac.exe` |

## Docs pipeline

- User guide + API reference in `doc/` (markdown), API reference generated from
  docstrings with lazydocs (adopt keyhac-mac's `generate_api_reference.py` +
  `DocumentSource` stub-file trick for anything not introspectable).
- en/ja: hand-maintained translations of the guide (win precedent), en-only API ref.

## Versioning & release

- Single version for both OSes: start at `2.0.0-alpha1`.
- Release artifacts: `keyhac_win_<ver>.zip`, `Keyhac_mac_<ver>.dmg` (or zip) + checksums.
- PuiKit is versioned/released independently on PyPI; Keyhac2 pins a minimum.
