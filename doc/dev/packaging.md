# Packaging, launchers, distribution

## Languages: the complete answer

| Layer | Language | Size | Why |
|---|---|---|---|
| Application (engine, UI, platform bindings) | Python 3.14 | ~everything | ctypes covers Win32 (PuiKit's Windows backend proves it incl. COM/Direct2D); PyObjC covers Quartz/AX/AppKit |
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
- Distribution: `Keyhac-<version>-win64.zip` via `make windows-zip`, and the
  Microsoft Store as an MSIX (below).

### Microsoft Store (MSIX)

Ported from XeFM's pipeline (`../xefm/windows_app/build_msix.ps1`,
`doc/dev/WINDOWS_STORE_MSIX_PLAN.md` there has the full background). The Store
route exists because it makes Windows code signing free: the package is uploaded
**unsigned** and Microsoft re-signs it during certification — no certificate to
buy, no SmartScreen warning, and a `winget install --source msstore` line for free.

- `build_msix.ps1` — wraps the `windows_app\build\Keyhac` bundle into
  `build\Keyhac-<version>.0-x64.msix` (the Store requires 4-part versions with
  major ≥ 1 and revision 0, so `__version__` 2.0.0 packages as 2.0.0.0, derived
  not hardcoded). Emits `AppxManifest.xml` at pack time: `runFullTrust`
  (classic Win32 desktop app — the hook, `SendInput` and UIA all work
  unchanged), plus a `windows.startupTask` extension — the MSIX-native
  replacement for the manual `shell:startup` shortcut, on by default and
  user-toggleable under Settings → Apps → Startup.
- Store tiles (`windows_app/resources/Assets/*.png`) are **committed**,
  rendered from `art/icon.svg` by `tools/make_icons.py` like every other icon
  target (`icons-check` guards drift) — unlike XeFM, which generates them at
  pack time.
- Identity (`Package/Identity/Name`, `Publisher`, display name) comes from the
  gitignored `windows_app/store.env` (copy `store.env.example`), values copied
  verbatim from Partner Center → product → *Product identity*. Without it the
  pack warns and uses a `Keyhac.Prototype` identity that sideloads but cannot
  be submitted.
- MSIX installs are read-only (`C:\Program Files\WindowsApps\...`); Keyhac is
  already safe: all state including the launcher's crash log lives under
  `~/.keyhac`, bytecode is precompiled, and nothing writes next to the exe.
- Targets: `make windows-msix` (unsigned pack; `SIGN=1` to self-sign),
  `make install-windows-msix` / `uninstall-windows-msix` (self-signed local
  sideload test; trusting/untrusting the throwaway cert elevates via UAC), and
  `make release-windows-msix` (repack unsigned + submit to the Store listing
  via the `msstore` CLI — needs `msstore reconfigure` once, and
  `KEYHAC_STORE_PRODUCT_ID` in `store.env`).
- The Store listing links `PRIVACY.md` (repo root) as the privacy policy.
- Live listing: <https://apps.microsoft.com/detail/9P8H1PG6PRHH>.

## macOS: `macos_app/`

- `src/main.m` + `KeyhacAppDelegate.m` — NSApplication + embedded Python.framework.
- `build.sh` — framework embed, delocate, signing/notarization (credentials in
  gitignored `signing.env`); `create_dmg.sh` → `Keyhac-<version>-macos.dmg`.
- `resources/{Info.plist.template,entitlements.plist,sitecustomize.py}`;
  `LSUIElement=YES` (agent app), hardened runtime, no sandbox,
  disable-library-validation.
- The embedded interpreter has to work as a *program*, not only as the
  launcher's library: `Resources/bin/keyhac-mcp-bridge` execs it. `build.sh`'s
  `relocate_embedded_interpreter` is what makes that true, and it fails the
  build if anything under the framework's `bin/` still loads a library from
  outside the bundle — a framework's `bin/pythonX.Y` is only the GUI stub for
  the `Python.app` this build drops, and every binary copied in still names the
  Homebrew path it was linked against. 2.4.1 shipped exactly that (#140).
- The macOS floor is `MACOS_MIN_VERSION` in `build.sh` (12.0), and it is a
  decision, not a property of the build machine. 12 is the oldest macOS
  Keyhac has been run on (#270, a Tart VM; the record is in
  [testing.md](testing.md#live-verification-record)). 11 was the floor
  until then, but Apple silicon cannot virtualize it, so nobody had run
  it, and the floor was raised to what had been run. Without
  `-mmacosx-version-min`, clang targets whatever macOS compiles it: 2.5.0 was
  built on macOS 27 and macOS 26 refused to launch it, while Info.plist still
  said 11.0. `build.sh` now compiles the launcher for the floor, writes the same
  value into `LSMinimumSystemVersion`, and fails the build if any Mach-O in
  the finished bundle declares a newer minimum. It also starts from an empty
  bundle every time, since leftovers from earlier builds were being shipped.
- The bundle is single-architecture: whatever the launcher was compiled for.
  python.org's framework and the PyObjC wheels arrive universal2, and the
  launcher (no `-arch`) is built for the build machine alone, so 2.6.0 shipped
  106 universal binaries and an x86_64-only `pythonX.Y-intel64` none of which
  could ever run (#273). `build.sh` drops the `-intel64` launcher, thins every
  Mach-O to the launcher's slices (read off the launcher, so a universal
  launcher keeps a fat bundle), and fails the build if any Mach-O still
  carries an architecture the launcher lacks. Intel Macs are not supported
  either way — the Homebrew cask declares `depends_on arch: :arm64`.
- Nothing may write bytecode into the bundle at run time: it is signed, and a
  `.pyc` written there breaks the seal of the installed copy. Every Python tree
  in it is precompiled by `build.sh`'s `compile_bytecode` in `unchecked-hash`
  mode, because the default timestamp mode compares against source mtimes that
  `cp -R` (create_dmg.sh, `make install-macos-dmg`) does not keep — 2.5.0 had
  427 stale `.pyc` once installed, and no stdlib bytecode at all. A compile
  failure stops the build, the launcher sets `write_bytecode = 0` (as the
  Windows one does), and the MCP bridge runs with `-B`.
- Bundle id is `crftwr.Keyhac2`, settled: it is the identity TCC keys the
  Accessibility grant on (and NSUserDefaults the saved window frames), so changing
  it after 2.0.0 shipped would revoke both for everyone installed. Reusing 1.x's
  `crftwr.Keyhac` would have inherited its grant instead — that window closed with
  2.0.0, and the two versions could never coexist anyway (shared `~/.keyhac`).
  `BUNDLE_ID=` still overrides, for local experiments.

## Shared tooling

- `tools/collect_dependencies.py` — runtime closure of pyproject.toml's
  `[project]` dependencies; markers gate pyobjc off Windows.
- `tools/generate_third_party_notices.py` — license aggregation; fails on a bundled
  dist without discoverable license text.
- Makefile targets: `windows-app` / `windows-zip` / `macos-app` / `macos-dmg`,
  `install-*` / `uninstall-*`, `clean-*`.

## Release pipeline

`make tag` → `make release-github` → `make release-macos-dmg` (on macOS) /
`make release-windows-zip` (on Windows) → `make release-whl` → `make release-skill`;
`make release-status` shows where a version stands. `make release-windows-msix`
(on Windows) is the
odd one out: it submits to the Microsoft Store, not the GitHub Release. After the DMG
is attached, `brew bump-cask-pr --no-fork --version X.Y.Z crftwr/tap/keyhac` bumps the
cask in [crftwr/homebrew-tap](https://github.com/crftwr/homebrew-tap) (by hand, not
from the Makefile; the cask is only a URL and a sha256 pointing at that DMG). Supporting scripts:
`tools/{release_preflight,bump_version,_version_source}.py`. The version lives in
`keyhac/__init__.py` (`__version__`), one number for both OSes.

Release artifacts per version: `Keyhac-<ver>-macos.dmg`, `Keyhac-<ver>-win64.zip`,
the skill bundles - one zip per folder in `keyhac/skills/`, all attached to the
GitHub Release - and the `keyhac` wheel on PyPI. PuiKit is
versioned/released independently on PyPI; Keyhac2 pins a minimum in
`pyproject.toml`.

**`release-skill` is not optional the way it looks.** The bundles are the
only way a user can obtain the skills — `make skill-bundle` needs the
Makefile and `tools/`, and neither ships — so a release without it leaves
[doc/ai-integration.md](../ai-integration.md) pointing at an asset that is not
there, and leaves anyone who connects the MCP endpoint in the half-installed
state that fails *quietly*: the tools work and the actions come back full of
`sleep` and screen coordinates.

## Data & config paths (unified)

| Item | Path (both OSes) |
|---|---|
| Config | `~/.keyhac/config.py` (template copied on first run) |
| Extensions | `~/.keyhac/extensions/` (on sys.path) |
| Clipboard history | `~/.keyhac/clipboard.json` |
| App settings | `~/.keyhac/settings.json` |
| Extra libraries | `~/.keyhac/requirements.txt` → `~/.keyhac/site-packages/X.Y/` (appended to sys.path, not purged on reload) |

With `--config PATH`, clipboard.json and settings.json live beside the config
(sandbox isolation). Windows portable mode (everything next to `Keyhac.exe`) is not
implemented yet — issue tracker.

## Extra Python libraries: `requirements.txt` (#272)

The bundles are sealed, so a library Keyhac does not ship reaches `config.py`
through `~/.keyhac/requirements.txt` and a user-started sync
(`keyhac/core/python_packages.py`; the user doc is in
[configuration.md](../configuration.md#extra-python-libraries)). The design is
XeFM's (crftwr/xefm#664) and the module is a port of `xefm/python_packages.py`;
keep the two in step.

```
<data dir>/requirements.txt
<data dir>/site-packages/
    3.14/            target, appended to sys.path with site.addsitedir
        .stamp       JSON: requirements sha256, host sha256, python, platform
    .3.14.new/       staging: built by a sync, swapped in whole
    .3.14.old/       the previous target during a swap
    .pip/            pip wheel downloaded from PyPI (Windows app only)
```

- **Load**: `Keymap.configure` calls `activate()` after `_prepare_extensions`
  and before `config.py` runs, on every load. It finishes a staged swap that
  waited for a restart, appends the target unless the stamp names another
  platform, and logs a warning when a sync is due. The check is offline; it
  reads the stamp and `importlib.metadata`, nothing else.
- **Sync**: tray menu *Sync Python Packages* (`ui/tray.py`'s `PackageSync`,
  worker thread, outcome back through `call_on_main_thread`, then
  `keymap.configure()`), or `keyhac deps sync` (`keyhac/deps_cli.py`, only
  where `keyhac` is a command: the launchers hand Python a fixed argv). pip
  runs with `--target` into staging, the host's distributions as exact-version
  constraints, then those are pruned and the directory is swapped in. This is
  the only code in Keyhac that reaches the network, and doc/installation.md
  says so.
- **Reload model**: a reload after a sync sees new packages (the directory is
  already on sys.path and import caches are invalidated). A package that was
  already imported keeps its old version until restart, which the console
  says. On Windows a swap blocked by a loaded `.pyd` stays staged and completes
  at the next start.

Which interpreter and which pip (`keyhac_python()`, `pip_command()`):

| Install | Interpreter | pip |
|---|---|---|
| pip / pipx / uv, source checkout | `sys.executable` | `python -m pip`, else ensurepip's bundled wheel |
| macOS app | `sys.base_prefix/bin/python3` (the framework's; `relocate_embedded_interpreter` makes it runnable) | `python3 -m pip`: the python.org framework's `site-packages/pip`, which `build.sh` keeps |
| Windows app (zip, Store) | `<root>\runtime\python.exe` | neither pip nor ensurepip in the embeddable CPython: a pip wheel from PyPI, sha256-checked against PyPI's digest, cached in `site-packages/.pip/` |

pip runs with `PYTHONDONTWRITEBYTECODE=1`, since a `.pyc` written into the
signed macOS bundle would break its seal. pip still compiles what it installs,
into the target.

Verified on macOS (2026-10-22), against a Developer ID–signed, hardened-runtime
build: a sync run with the bundle's `python3` installed `markupsafe` from PyPI,
the same interpreter imported its unsigned `_speedups` `.so` from the target,
and the bundle gained no `.pyc`. Still open:

- The same import inside the running Keyhac process itself (the launcher
  carries `disable-library-validation`, so it is expected to load), through
  the tray menu end to end.
- Windows app and the Store package: the PyPI pip download route, launching
  `runtime\python.exe` from inside the MSIX package, and loading an unsigned
  `.pyd` from `~/.keyhac` have not been run there.
