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

## Status: IMPLEMENTED — as a port of XeFM's pipeline, not the sketches below

Both launchers and bundle builds exist, ported from XeFM (the author's shipped
PuiKit app, whose pipeline post-dates and supersedes the keyhac-win/keyhac-mac
schemes sketched in the sections below; those remain as historical reference):

- `windows_app/`: `src/launcher.c` (C, GUI subsystem, static CRT, delay-loaded
  `python3XX.dll`, PEP 587 explicit search paths), `resources/Keyhac.rc` +
  `Keyhac.manifest` (icon, version info, Per-Monitor-V2 DPI), `build.ps1`
  (embeddable CPython download → bundle assembly → deps collection → notices →
  compileall → cl.exe), `install_zip.ps1`. Layout: `Keyhac.exe` +
  `runtime\` (CPython) + `Lib\site-packages\` + `app\{keyhac,puikit}`.
  **Built and smoke-tested on Windows** (embedded runtime imports keyhac +
  puikit + numpy; full interactive run pending).
- `macos_app/`: `src/main.m` + `KeyhacAppDelegate.m` (NSApplication +
  embedded Python.framework), `build.sh` (framework embed, delocate,
  signing/notarization via gitignored `signing.env`), `create_dmg.sh`,
  `resources/{Info.plist.template,entitlements.plist,sitecustomize.py}`.
  `LSUIElement=YES`; bundle id defaults to `crftwr.Keyhac2`, overridable with
  `BUNDLE_ID=crftwr.Keyhac` (open decision 07-roadmap #4 — reusing the 1.x id
  carries the Accessibility permission over). **Written to spec; needs a live
  macOS pass.**
- Shared: `tools/collect_dependencies.py` (runtime closure of pyproject.toml's
  `[project]` dependencies; markers gate pyobjc off Windows) and
  `tools/generate_third_party_notices.py` (license aggregation, fails on a
  bundled dist without discoverable license text).
- Makefile: `windows-app` / `windows-zip` / `macos-app` / `macos-dmg`,
  `install-*` / `uninstall-*`, `clean-*`, and `release-windows-zip` /
  `release-macos-dmg` wired into the tag → release-github → release-*
  pipeline.

Differences from the sketches below, all inherited deliberately from XeFM:
the Windows stdlib comes from the python.org *embeddable* package under
`runtime\` (not a hand-trimmed `modules/Lib`); portable mode and the
`extension/` drop-in dir are not implemented yet; `_pth` is deleted and paths
come from PyConfig alone.

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
