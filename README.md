# Keyhac 2

Python-scriptable keyboard customization tool for **Windows and macOS**, from one shared
codebase. The successor to
[keyhac-win](https://github.com/crftwr/keyhac) and
[keyhac-mac](https://github.com/crftwr/keyhac-mac), with UI built on
[PuiKit](https://github.com/crftwr/puikit).

Design documents: [doc/](doc/) — start with [doc/00-overview.md](doc/00-overview.md).
Project guide for coding agents: [CLAUDE.md](CLAUDE.md).

## Status

**M1 (engine + minimal hook) — in progress.**

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
