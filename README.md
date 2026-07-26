# Keyhac 2

Python-scriptable keyboard customization tool for **Windows and macOS**, from one shared
codebase. The successor to
[keyhac-win](https://github.com/crftwr/keyhac) and
[keyhac-mac](https://github.com/crftwr/keyhac-mac), with UI built on
[PuiKit](https://github.com/crftwr/puikit).

Design documents: [doc/](doc/) — start with [doc/00-overview.md](doc/00-overview.md).
Project guide for coding agents: [CLAUDE.md](CLAUDE.md).

## Status

**M3 (clipboard history + chooser) — in progress.**

- Clipboard history: portable `ClipboardProvider` (NSPasteboard changeCount /
  Win32 sequence-number polling), text history with dedup + JSON persistence
  (debounced; file format compatible with keyhac-mac's `clipboard.json`).
- Chooser window on PuiKit multi-window (`create_window`): search field +
  filtered list, Up/Down/Enter/Escape, Shift-select = copy without paste.
  Actions: `ShowClipboardHistory` / `ShowClipboardSnippets` /
  `ShowClipboardTools`, `ChooserAction` base, `ThreadedAction`,
  `LaunchApplication`. Verified live on macOS end-to-end (hotkey → chooser →
  history entry).
- Windows: clipboard provider written to spec (untested); `WinAppControl`
  pending.

**M2 (console window) — done on macOS.**

- PuiKit console window working on macOS: LogView with per-level colors, hook
  on/off toggle (re-enable reloads config), log-level selector, last-key /
  focus-path inspector with copy buttons. The console's PuiKit backend runs
  the process event loop; the CGEventTap shares it. Runs as an agent app (no
  Dock icon) via the new PuiKit `activation_policy="accessory"`.
- Requires the PuiKit window-management extensions
  ([puikit PR #76](https://github.com/crftwr/puikit/pull/76)): `WindowStyle`,
  `activation_policy`, `call_later`. Until that ships in a release, the
  Makefile installs `../puikit` (branch) editable.
- Windows console path written but pending the next Windows session.
- `--no-ui` keeps the headless M1 mode.

**M1 (engine + minimal hook) — done on macOS; Windows interactive checklist open.**

- Core keymap engine: done, 64 unit tests green (key expressions, L/R-agnostic
  modifier matching, one-shot, multi-stroke, user modifiers, replace_key,
  focus conditions, InputContext batching).
- macOS platform (PyObjC CGEventTap): done, validated live (tap install,
  injection, event-source classification, replay re-entry).
- Windows platform (ctypes WH_KEYBOARD_LL): first bring-up done — hook
  install/callbacks, SendInput injection + dwExtraInfo classification, focus
  query, message pump and timers validated on Windows. Still to exercise
  interactively: consume decisions on physical keys, per-VK extended-key
  flags, and the sanity-check re-install path (`tools/hook_echo.py`).
- UI (console/chooser/balloon/tray): M2+, PuiKit-based.

## Development (macOS)

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest            # engine tests, no permissions needed

.venv/bin/python tools/hook_echo.py   # echo key events (needs Accessibility permission)
.venv/bin/python -m keyhac -d         # run with ~/.keyhac/config.py
```

Configuration lives at `~/.keyhac/config.py` (created from a template on first
run) and defines `configure(keymap)` — see [keyhac/_config.py](keyhac/_config.py).
