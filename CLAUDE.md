# CLAUDE.md — Keyhac2

## What this project is

Keyhac2 is a ground-up unification of **Keyhac for Windows** (`../keyhac-win`, v1.83) and
**Keyhac for macOS** (`../keyhac-mac`, v1.68): a keyboard customization / macro tool whose
behavior is scripted by the user in Python (`~/.keyhac/config.py`). Keyhac2 has **one shared
Python codebase** for both OSes, with thin per-OS platform modules. All UI is built on
**PuiKit** (`../puikit`), the author's portable Python UI toolkit.

Detailed design documents live in [doc/](doc/) — start with
[doc/00-overview.md](doc/00-overview.md).

## Sibling repositories (read-only references)

| Repo | What it is | What to learn from it |
|---|---|---|
| `../keyhac-win` | Python 3.13 + thin C++ launcher (`main.cpp`). UI via `ckit`, input via `pyauto` (external C++ extension repos, **not present in this checkout**). | Synchronous `WH_KEYBOARD_LL` hook semantics, modifier-state engine, one-shot/multi-stroke logic, hook-recovery (sanity check), `hookCall` sentinel trick, clipboard history, list window UX, full feature set. |
| `../keyhac-mac` | Swift/SwiftUI menu-bar app embedding CPython 3.13 via a C++ bridge. | CGEventTap semantics (re-enable, event-source filtering, deferred-event reordering), AX focus paths, the **modern snake_case config API that Keyhac2 adopts as its baseline**, ThreadedAction model. |
| `../puikit` | Pure-Python UI toolkit, v1.0.3, PyPI `puikit`. Backends: curses / macOS (PyObjC+AppKit) / Windows (ctypes+Direct2D) / web / memory. | The UI layer for Keyhac2. Its `CLAUDE.md` documents a strict additive API-compatibility policy — all Keyhac2-driven extensions must follow it. |

## Key decisions (details and rationale in doc/)

1. **Languages**: Python 3.13 for everything at runtime. Platform bindings via **ctypes**
   (Windows) and **PyObjC** (macOS) — no custom compiled extension modules. The only
   non-Python code is a tiny PEP 587 embedding **launcher** per OS (C/C++, no app logic),
   needed for packaging and for a stable app identity (macOS Accessibility permission is
   granted per bundle). See [doc/06-packaging.md](doc/06-packaging.md).
2. **Key hook**: both OS hooks are *synchronous consume-decisions* running on the main
   thread; the differences (tap re-enable + injected/real event reordering on macOS;
   silent-unhook recovery on Windows) are encapsulated behind one `InputHook` interface.
   See [doc/02-platform-layer.md](doc/02-platform-layer.md).
3. **Config API**: keyhac-mac's snake_case API is the base, extended with portable focus
   conditions (`app=`, `title=`) and the keyhac-win features it lacks (mouse output, window
   actions, shell execute, balloons). One `config.py` runs on both OSes; keyhac-win
   configs require migration (guide in [doc/03-config-api.md](doc/03-config-api.md)).
4. **PuiKit must be extended** — additively, per its compatibility policy: secondary
   windows, frameless/topmost/no-activate styles, agent-app mode, system tray / menu-bar
   extra, screen geometry, `call_later`. See [doc/04-puikit.md](doc/04-puikit.md).
5. **Single process, main-thread rule**: the native event loop on the main thread services
   the hook *and* all PuiKit windows. Slow work goes to `ThreadedAction`; results come
   back via `call_on_main_thread`.

## Planned source layout

```
keyhac/
  core/        # OS-independent: keymap engine, key expressions, input context,
               # actions, clipboard history, config loader, logging  (pure, unit-testable)
  platform/    # interface definitions (InputHook, Injector, FocusProvider, ...)
    win/       # ctypes: WH_KEYBOARD_LL, SendInput, Win32 focus/window, clipboard listener
    mac/       # PyObjC: CGEventTap, CGEventPost, AXUIElement, NSWorkspace, NSPasteboard
  ui/          # PuiKit-based: console window, chooser, balloon/toast, tray icon
  main.py      # bootstrap: loop setup, hook install, config load
```

## Documents (doc/)

- [00-overview.md](doc/00-overview.md) — goals, background, answers to the founding questions
- [01-architecture.md](doc/01-architecture.md) — layers, event loop, threading, key-event lifecycle
- [02-platform-layer.md](doc/02-platform-layer.md) — OS-specific analysis: hooks, injection, focus, clipboard, permissions
- [03-config-api.md](doc/03-config-api.md) — user-facing API spec + compatibility/migration
- [04-puikit.md](doc/04-puikit.md) — what PuiKit provides, gaps, extension plan
- [05-features.md](doc/05-features.md) — feature parity matrix and per-feature design notes
- [06-packaging.md](doc/06-packaging.md) — launchers, embedding, bundles, data paths
- [07-roadmap.md](doc/07-roadmap.md) — milestones, spikes, risks, open decisions

## Conventions

- Public API is snake_case (keyhac-mac style). No new camelCase API.
- `keyhac/core/` must not import OS modules (`ctypes.windll`, `AppKit`, `Quartz`, Win32
  constants). All OS access goes through `keyhac/platform/` interfaces.
- UI code talks to PuiKit only; no direct AppKit/Win32 in `keyhac/ui/`.
- Tests: pytest. Keymap engine is tested with a scripted fake `InputHook` (no OS needed);
  UI is tested against PuiKit's `MemoryBackend`.
- PuiKit changes are developed in `../puikit` and must respect its additive API policy
  (new capability flags default off; new `Backend` methods get base no-op/raise).

## Status

Planning stage — design docs written, no implementation yet.
Next step: **M0 feasibility spikes** (hook latency from pure Python on both OSes, PuiKit
multi-window prototype) — see [doc/07-roadmap.md](doc/07-roadmap.md).
