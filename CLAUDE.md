# CLAUDE.md — Keyhac2

## What this project is

Keyhac2 is the unification of **Keyhac for Windows** (`../keyhac-win`, v1.83) and
**Keyhac for macOS** (`../keyhac-mac`, v1.68): a keyboard customization / macro tool
whose behavior is scripted by the user in Python (`~/.keyhac/config.py`). It has **one
shared Python codebase** for both OSes, with thin per-OS platform modules. All UI is
built on **PuiKit** (`../puikit`), the author's portable Python UI toolkit.

Documentation:

- End-user docs: [README.md](README.md), [doc/](doc/) —
  [installation](doc/installation.md), [configuration](doc/configuration.md),
  [API reference](doc/config-api.md) (generated), migration guides from
  [keyhac-mac](doc/migration-from-keyhac-mac.md) /
  [keyhac-win](doc/migration-from-keyhac-win.md).
- Developer docs: [doc/dev/](doc/dev/) — [overview](doc/dev/overview.md),
  [architecture](doc/dev/architecture.md),
  [platform-layer](doc/dev/platform-layer.md),
  [design-notes](doc/dev/design-notes.md), [puikit](doc/dev/puikit.md),
  [packaging](doc/dev/packaging.md), [testing](doc/dev/testing.md).
- Remaining tasks and open decisions live in the **GitHub issues**
  (`gh issue list`), not in the docs.

## Sibling repositories (read-only references)

| Repo | What it is | What to learn from it |
|---|---|---|
| `../keyhac-win` | Python 3.13 + thin C++ launcher. UI via `ckit`, input via `pyauto` (external C++ extension repos, **not present in this checkout**). | Synchronous `WH_KEYBOARD_LL` hook semantics, modifier-state engine, one-shot/multi-stroke logic, hook-recovery, clipboard history, list window UX, full feature set. |
| `../keyhac-mac` | Swift/SwiftUI menu-bar app embedding CPython 3.13 via a C++ bridge. | CGEventTap semantics (re-enable, event-source filtering, deferred-event reordering), AX focus paths, the **modern snake_case config API that Keyhac2 adopted as its baseline**, ThreadedAction model. |
| `../puikit` | Pure-Python UI toolkit, PyPI `puikit`. Backends: curses / macOS (PyObjC+AppKit) / Windows (ctypes+Direct2D) / web / memory. | The UI layer for Keyhac2. Its `CLAUDE.md` documents a strict additive API-compatibility policy — all Keyhac2-driven extensions must follow it. |

Feature reference is frozen at win 1.83 / mac 1.68; changes upstream after that are
reviewed one-off.

## Key decisions (rationale in doc/dev/)

1. **Languages**: Python 3.14 for everything at runtime. Platform bindings via
   **ctypes** (Windows) and **PyObjC** (macOS) — no custom compiled extension modules.
   The only non-Python code is a tiny PEP 587 embedding **launcher** per OS, needed
   for packaging and a stable app identity (macOS Accessibility permission is granted
   per bundle). See [doc/dev/packaging.md](doc/dev/packaging.md).
2. **Key hook**: both OS hooks are *synchronous consume-decisions* running on the main
   thread; the differences (tap re-enable + injected/real event reordering on macOS;
   silent-unhook recovery on Windows) are encapsulated behind one `InputHook`
   interface. See [doc/dev/platform-layer.md](doc/dev/platform-layer.md).
3. **Config API**: keyhac-mac's snake_case API is the base, extended with portable
   focus conditions (`app=`, `title=`) and the keyhac-win features it lacked. One
   `config.py` runs on both OSes; keyhac-win configs require migration.
4. **PuiKit is extended additively** per its compatibility policy; everything Keyhac2
   needs shipped in puikit ≥ 1.0.8 (PyPI). See [doc/dev/puikit.md](doc/dev/puikit.md).
5. **Single process, main-thread rule**: the native event loop on the main thread
   services the hook *and* all PuiKit windows. Slow work goes to `ThreadedAction`;
   results come back via `call_on_main_thread`.

## Source layout

```
keyhac/
  core/        # OS-independent: keymap engine, key expressions, input context,
               # actions, clipboard history, replay, config loader, settings, logging
  actions.py   # action objects needing platform/UI wiring (MoveWindow, choosers, ...)
  platform/    # base.py interface definitions + fake.py test doubles
    win/       # ctypes: WH_KEYBOARD_LL/WH_MOUSE_LL, SendInput, UIA, Win32 windows
    mac/       # PyObjC: CGEventTap, CGEventPost, AXUIElement, NSWorkspace, NSPasteboard
  ui/          # PuiKit-based: console, chooser, balloon, tray, runtime (backend holder)
  main.py      # bootstrap: loop setup, hook install, config load
  _config.py   # the config.py template copied on first run
windows_app/   # Keyhac.exe launcher + bundle build (build.ps1)
macos_app/     # Keyhac.app launcher + bundle build (build.sh, create_dmg.sh)
art/           # hand-maintained SVG icon sources (rendered by tools/make_icons.py)
tools/         # icon pipeline, release scripts, hook_echo diagnostic
```

## Conventions

- Public API is snake_case (keyhac-mac style). No new camelCase API.
- `keyhac/core/` must not import OS modules (`ctypes.windll`, `AppKit`, `Quartz`,
  Win32 constants). All OS access goes through `keyhac/platform/` interfaces.
- UI code talks to PuiKit only; no direct AppKit/Win32 in `keyhac/ui/`.
- Windows ctypes: every prototype (`argtypes`/`restype`) is declared explicitly —
  mandatory on 64-bit, where the default c_int restype truncates handles (this broke
  `SetWindowsHookExW` with error 126 once).
- Docstrings on the config-facing API are **user documentation**: Google style
  with `Args:`/`Returns:`, no porting history (that goes in comments).
  `doc/config-api.md` is generated from them and committed — run
  `make api-reference` after changing one, `make api-reference-check` verifies
  the two have not drifted. Members that are public only in the naming sense
  (hook callbacks, wiring called by `main()`) carry a `lazydocs: ignore` line;
  `tools/generate_api_reference.py` explains the rules, including which
  docstring shapes lazydocs reads structurally.
- Tests: pytest (`.venv/bin/python -m pytest`). The engine is tested with scripted
  fakes (no OS needed); UI against PuiKit's `MemoryBackend`; live platform tests and
  harness patterns are described in [doc/dev/testing.md](doc/dev/testing.md).
- **Subtle inherited behaviors are load-bearing** — do not "simplify" them. The list
  (L/R-agnostic `KeyCondition.__eq__`, `force_LR` output, never-emitted user
  modifiers, unmatched-consume in multi-stroke, pass-through-on-error, the three
  keyhac-mac hook bugs fixed in `platform/mac/hook.py`'s docstring) is in
  [doc/dev/design-notes.md](doc/dev/design-notes.md).
- PuiKit changes are developed in `../puikit` and must respect its additive API
  policy (new capability flags default off; new `Backend` methods get base
  no-op/raise). **Always on a feature branch with a pull request — never commit
  directly to its main.** Keyhac2 depends on `puikit>=1.0.10` from PyPI; an editable
  checkout is for puikit development only (set `PUIKIT_DIR` in gitignored
  `Makefile.local`; `make install-puikit` switches an existing venv).
- `../keyhac-win` and `../keyhac-mac` are **read-only** references. Never modify them.
- Build/release infra follows XeFM's Makefile/bundle system (the author's shipped
  PuiKit app) — port from it rather than inventing new schemes.

## Status

**Released as 2.0.0 stable** (version in `keyhac/__init__.py`).
The engine, both platform layers, all UI (console / chooser / balloon / tray), the
config API surface, clipboard history, macros, window actions and mouse output are
implemented and live-verified on both macOS and Windows; both native launchers build,
and the macOS bundle is signed/notarized and verified end-to-end. The live
verification record — including which passes caught which real bugs — is in
[doc/dev/testing.md](doc/dev/testing.md).

**AI integration is officially supported** — the MCP endpoint (`keyhac/mcp/`),
the action API (`keymap.ui`, `UINode`) and the authoring skill. It is off unless
the user ticks **AI Integration > MCP Server** in the console or tray menu
(persisted in `settings.json`; there is deliberately no config API), and it
carries the same additive-only expectation as the rest of the public surface.

Two properties of `UINode` are what that expectation is really about — they are
the ceiling on action expressiveness, and changing either breaks every action
already written:

- **Element identity.** Address by `identifier` (DOM id / AutomationId) where
  there is one, since it survives relabelling and localisation; then by role
  plus name or text. `identity_key()` is a different thing — the raw platform
  ref, used only for the DAG dedupe in `get_ui_tree`, and not public shape.
- **Handle lifetime is snapshot.** A `UINode` records what an element *was*; the
  screen moves on and the node does not notice. `reread()` refreshes one
  deliberately. Nodes that quietly re-read themselves were rejected: that hides
  exactly the change per-step preconditions exist to catch. `StaleElement`
  distinguishes *the screen moved* (re-findable) from *the selector is wrong*
  (regenerate the action).

Both are settled. They change only in a major release.

What remains is tracked in the GitHub issues: the deferred features (themes/fonts,
i18n, migemo, rich clipboard formats, macOS ISO layout, balloon help UI). The
genuinely-interactive verification passes are through (issue #10, closed), but
they are a **standing pre-release routine**, not a finished backlog — what to
repeat, and what each check is looking for, is in
[doc/dev/testing.md](doc/dev/testing.md).
