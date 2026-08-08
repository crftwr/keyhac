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
odd one out: it submits to the Microsoft Store, not the GitHub Release. Supporting scripts:
`tools/{release_preflight,bump_version,_version_source}.py`. The version lives in
`keyhac/__init__.py` (`__version__`), one number for both OSes.

Release artifacts per version: `Keyhac-<ver>-macos.dmg`, `Keyhac-<ver>-win64.zip`,
`keyhac-action-authoring-skill.zip` (attached to the GitHub Release), and the
`keyhac` wheel on PyPI. PuiKit is
versioned/released independently on PyPI; Keyhac2 pins a minimum (`puikit>=1.0.8`).

**`release-skill` is not optional the way it looks.** The skill bundle is the
only way a user can obtain the authoring skill — `make skill-bundle` needs the
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

With `--config PATH`, clipboard.json and settings.json live beside the config
(sandbox isolation). Windows portable mode (everything next to `Keyhac.exe`) is not
implemented yet — issue tracker.
