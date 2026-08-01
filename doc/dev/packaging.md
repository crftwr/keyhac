# Packaging, launchers, distribution

## Languages: the complete answer

| Layer | Language | Size | Why |
|---|---|---|---|
| Application (engine, UI, platform bindings) | Python 3.13 | ~everything | ctypes covers Win32 (PuiKit's Windows backend proves it incl. COM/Direct2D); PyObjC covers Quartz/AX/AppKit |
| Embedding launcher, Windows (`Keyhac.exe`) | C | ~150 lines | PEP 587 `PyConfig` isolated init, module search paths. Also: exe icon, DPI manifest. |
| Embedding launcher, macOS (`Keyhac.app` main) | Objective-C | ~150 lines | Same PEP 587 pattern. A real bundle executable is **required** so the Accessibility permission attaches to a stable identity — running under a generic `python3` would grant the permission to that interpreter, not to Keyhac. |
| Build scripts | PowerShell / shell / Python | | `windows_app/build.ps1`, `macos_app/build.sh`, `tools/` |

No ckit, no pyauto, no compiled extension of our own. Runtime dependencies: `puikit`,
`pyobjc-framework-{Cocoa,Quartz,ApplicationServices}` (macOS only), `pillow` (via
puikit). The pipeline is a port of XeFM's Makefile/bundle system — the author's
shipped PuiKit app, the standard to follow for build infra.

## Windows: `windows_app/`

- `src/launcher.c` — C, GUI subsystem, static CRT, delay-loaded `python3XX.dll`,
  PEP 587 explicit search paths.
- `resources/Keyhac.rc` + `Keyhac.manifest` — icon, version info, Per-Monitor-V2 DPI.
- `build.ps1` — embeddable CPython download → bundle assembly → dependency collection
  → third-party notices → `compileall` → `cl.exe`. The stdlib comes from the
  python.org *embeddable* package under `runtime\`; `_pth` is deleted and paths come
  from PyConfig alone. `install_zip.ps1` installs a built zip.
- Layout: `Keyhac.exe` + `runtime\` (CPython) + `Lib\site-packages\` +
  `app\{keyhac,puikit}`.
- Distribution: `Keyhac-<version>-win64.zip` via `make windows-zip`.

## macOS: `macos_app/`

- `src/main.m` + `KeyhacAppDelegate.m` — NSApplication + embedded Python.framework.
- `build.sh` — framework embed, delocate, signing/notarization (credentials in
  gitignored `signing.env`); `create_dmg.sh` → `Keyhac-<version>-macos.dmg`.
- `resources/{Info.plist.template,entitlements.plist,sitecustomize.py}`;
  `LSUIElement=YES` (agent app), hardened runtime, no sandbox,
  disable-library-validation.
- Bundle id defaults to `crftwr.Keyhac2`, overridable with `BUNDLE_ID=crftwr.Keyhac`
  (reusing the 1.x id carries the Accessibility permission over — decision tracked
  in the issue tracker).
- Known fat to trim: `Resources/python_packages` ships all of PyObjC incl.
  PyObjCTest — pruning would cut bundle size and per-.so signing time noticeably
  (issue tracker).

## Shared tooling

- `tools/collect_dependencies.py` — runtime closure of pyproject.toml's
  `[project]` dependencies; markers gate pyobjc off Windows.
- `tools/generate_third_party_notices.py` — license aggregation; fails on a bundled
  dist without discoverable license text.
- Makefile targets: `windows-app` / `windows-zip` / `macos-app` / `macos-dmg`,
  `install-*` / `uninstall-*`, `clean-*`.

## Release pipeline

`make tag` → `make release-github` → `make release-macos-dmg` (on macOS) /
`make release-windows-zip` (on Windows) → `make release-whl`; `make release-status`
shows where a version stands. Supporting scripts:
`tools/{release_preflight,bump_version,_version_source}.py`. The version lives in
`keyhac/__init__.py` (`__version__`), one number for both OSes.

Release artifacts per version: `Keyhac-<ver>-macos.dmg`, `Keyhac-<ver>-win64.zip`
(attached to the GitHub Release), and the `keyhac` wheel on PyPI. PuiKit is
versioned/released independently on PyPI; Keyhac2 pins a minimum (`puikit>=1.0.8`).

## Data & config paths (unified)

| Item | Path (both OSes) |
|---|---|
| Config | `~/.keyhac/config.py` (template copied on first run) |
| Extensions | `~/.keyhac/extensions/` (on sys.path) |
| Clipboard history | `~/.keyhac/clipboard.json` |
| App settings | `~/.keyhac/settings.json` |

With `--config PATH`, clipboard.json and settings.json live beside the config
(sandbox isolation). Windows portable mode (everything next to `Keyhac.exe`) is not
implemented yet — issue tracker.
