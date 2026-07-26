# 00 — Overview

## What Keyhac is

Keyhac is a keyboard customization tool: it installs a system-wide keyboard hook and lets
the user script every behavior in Python — key-to-key remapping, app-specific keymaps,
multi-stroke keys, user-defined modifiers, one-shot modifiers, clipboard history,
launcher/candidate windows, and keyboard macros. The user writes a `config.py` that
defines `configure(keymap)`.

Two independent implementations exist today:

| | keyhac-win (v1.83) | keyhac-mac (v1.68) |
|---|---|---|
| Host / UI | Thin C++ launcher; UI in Python on **ckit** (author's Win32 C++ toolkit) | **Swift/SwiftUI** menu-bar app; C++ `PythonBridge` embeds CPython |
| Input layer | **pyauto** (author's C++ extension): `WH_KEYBOARD_LL`, `SendInput`, Win32 window API | Swift: `CGEventTap`, `CGEventPost`, AXUIElement |
| Python | 3.13 embedded (PEP 587, isolated) | 3.13 embedded (PEP 587, isolated) |
| Config API | camelCase (`defineWindowKeymap`, `InputKeyCommand`, …) | snake_case (`define_keytable`, `ThreadedAction`, …) — newer, cleaner |
| Feature set | Larger (mouse, macro, balloons, themes, migemo, tray menu…) | Smaller but modernized (UIElement/AX automation, focus paths, chooser) |

## Why Keyhac2

- **Two divergent codebases** implement the same product idea twice, with different APIs.
  Every feature and fix is done twice or drifts.
- keyhac-win depends on **ckit + pyauto**, C++ extension projects that predate modern
  Windows APIs and require MSVC builds; the UI layer duplicates what PuiKit now does
  portably and better (DPI, IME, themes, testable MemoryBackend).
- keyhac-mac put large parts of the product (console, chooser, menu bar) in **Swift**,
  which cannot be shared with Windows at all.
- **PuiKit now exists** (v1.0.3): a pure-Python, capability-based UI toolkit with real
  AppKit (PyObjC) and Direct2D (ctypes) backends, built by the same author. It removes
  the historical reason for ckit and for the Swift UI layer.

Keyhac2's premise: **everything that can be shared, is shared** — one keymap engine, one
config API, one UI codebase on PuiKit — and the per-OS surface shrinks to the smallest
possible layer: the low-level hook, event injection, focus/window queries, and packaging.

## Goals

1. One repository, one Python codebase, producing a native app for Windows 10/11 (x64)
   and macOS 15+.
2. One documented user-facing config API; a single `config.py` can run on both OSes.
3. Feature parity with the *union* of keyhac-win and keyhac-mac (phased; see
   [05-features.md](05-features.md)).
4. A pure-Python, fully unit-testable core (the old codebases were essentially untested).
5. PuiKit becomes strictly better through the extensions Keyhac2 needs (tray, secondary
   windows, …), benefiting other PuiKit apps.

## Non-goals

- Backward-compatible execution of keyhac-win `config.py` files (camelCase API). We
  provide a migration guide, not a shim. (Decision — see [03-config-api.md](03-config-api.md).)
- 32-bit Windows, Linux, or Wayland support (Linux may become feasible later since PuiKit
  has a curses/web backend, but global hooks on Linux are a different world).
- An in-app config editor / GUI configuration. Config stays a Python file.

## The founding questions, answered

Short answers; each links to the full analysis.

### Q1. What languages do we use other than Python?

**Almost none at runtime.** Python 3.13 everywhere; Windows OS access via **ctypes**
(as PuiKit's Windows backend already proves viable, including COM), macOS via **PyObjC**
(Quartz/ApplicationServices provide `CGEventTap` and `AXUIElement`). The only non-Python
code is a **tiny embedding launcher per OS** (~150 lines of C/C++ using the PEP 587
`PyConfig` API, patterned on keyhac-win's `main.cpp` and keyhac-mac's `PythonBridge.cpp`)
whose job is app identity + starting the interpreter. No ckit, no pyauto, no Swift app
layer. → [06-packaging.md](06-packaging.md)

### Q2. Which parts have to be OS-specific?

The list is short but deep: **key hook & recovery**, **event injection & ordering**,
**keycode tables/layouts**, **focus & window queries**, **window actions**, **clipboard
monitoring**, **permissions**, **packaging**. Notably, *both* hooks are synchronous
consume-decisions (the "sync vs async" difference is real but lives in the surrounding
machinery: macOS needs injected-vs-real event reordering and tap re-enable; Windows needs
silent-unhook detection). → [02-platform-layer.md](02-platform-layer.md)

### Q3. Can we use the same config.py format / user-facing APIs?

**Yes for Keyhac2 going forward; no for keyhac-win compatibility.** The two existing APIs
already diverged (camelCase vs snake_case, `defineWindowKeymap(exe_name=...)` vs
`define_keytable(focus_path_pattern=...)`). Keyhac2 adopts keyhac-mac's API as the base —
existing keyhac-mac configs should run nearly unchanged — and adds portable focus
matching (`app=`, `title=`) plus the missing keyhac-win features. One config file can
serve both OSes, with `keymap.platform` branches for genuinely OS-specific parts.
→ [03-config-api.md](03-config-api.md)

### Q4. Do we have to extend PuiKit?

**Yes.** PuiKit today is "one backend = one standard resizable window as a regular app".
Keyhac2 needs: multiple simultaneous windows (console + chooser + balloon), frameless /
always-on-top / no-activate popup styles, an agent-app mode (no Dock icon), a system tray
icon / menu-bar extra, screen geometry queries, runtime window control (show/hide/move),
and a `call_later` timer. All are additive and fit PuiKit's capability model; tray and
several window capabilities are already declared in `PROFILE_GUI_DESKTOP` but unimplemented.
→ [04-puikit.md](04-puikit.md)

## Naming

- Product: **Keyhac 2.0** (versioning restarts the two lines under one number).
- Python package: `keyhac`. Repo: `keyhac2`.
- User data: `~/.keyhac/` on **both** OSes (keyhac-mac already does this; keyhac-win used
  `%APPDATA%\Keyhac` — migration note in [06-packaging.md](06-packaging.md)).
