# 07 — Roadmap

## M0 — Feasibility spikes (throwaway code, answers only)

The three assumptions everything rests on. Each spike has a pass/fail exit criterion.

| Spike | Question | Exit criterion |
|---|---|---|
| S1 win hook | Pure-ctypes `WH_KEYBOARD_LL` + `SendInput`: is Python-in-the-hook latency acceptable and stable? (keyhac-win already ran Python in the hook via pyauto — the new part is ctypes plumbing) | Echo/remap tool: added latency ≤ a few ms typical; no unhook under typing burst + deliberate 50 ms handler stall recovers via sanity check |
| S2 mac hook | PyObjC `CGEventTapCreate` + `CGEventPost` + AX APIs: all callable, callback stable, permission flow works? | Same echo/remap tool incl. `flagsChanged` synthesis, source-ID filtering, tap-timeout re-enable; `AXUIElementCopyAttributeValue` walk of focused element works from PyObjC |
| S3 puikit windows | Multi-window + styles prototype in ../puikit (E1/E2/E3 sketch on both backends) | One process shows: normal window + frameless-topmost popup simultaneously, on macOS as accessory app, keyboard into popup, no focus theft |

Timebox: S1/S2 ~2-3 days each, S3 ~1 week. If S1 or S2 fails on latency → fallback
decision: minimal compiled helper (tiny C extension or reuse pyauto for win) — *not
expected*, both predecessors already run Python inline in the hook.

## M1 — Engine + minimal hook (remap works)

- `core/`: KeyCondition/KeyTable/expressions (portable names + aliases), modifier state
  machine (L/R planes, user modifiers, one-shot), replace_key, multi-stroke state,
  dispatch; `FakeInputHook` test harness; port regression cases from both changelogs.
- `platform/win` + `platform/mac`: InputHook (install/send/health/layout) productionized
  from spikes.
- CLI-only bootstrap (no UI): load `~/.keyhac/config.py`, log to stderr.
- **Done when**: a shared sample config remaps keys, one-shot and multi-stroke work, on
  both OSes, engine test suite green.

## M2 — Focus, config lifecycle, console

- FocusProvider both OSes; `define_keytable(app=, title=, class_name=/focus_path_pattern=,
  custom_condition_func=)`; merge-in-definition-order.
- Config reload, error containment, settings.json, single-instance guard.
- PuiKit console window (LogView, toggle, level, last-key/focus inspector) — needs E1
  partially (console may be the *main* window initially), E3 for mac agent mode.
- ThreadedAction + engine lock + `call_later` (E6).
- **Done when**: daily-drivable for keyboard-only workflows; console shows keys/focus.

## M3 — Actions & clipboard & chooser

- InputContext injection parity (lone-Win/Alt cancel, hook_call serialization, deferral
  on mac), InputText, ActivateWindow, MoveWindow, Launch/ShellExecute, UIElement (mac).
- Clipboard history + monitoring + persistence; chooser window (E1/E2) + history/
  snippets/tools actions.
- **Done when**: clipboard-history paste flow works reliably on both OSes (the single
  most-used feature after remapping).

## M4 — Tray, balloon, macro, mouse

- Tray/menu-bar extra (E4) with full menu; balloon window + multi-stroke help + macro
  status; record/playback (replay sources both OSes); Windows mouse output commands +
  WH_MOUSE_LL one-shot cancel.
- **Done when**: feature union minus deferred items (migemo, cron, mac mouse) reached.

## M5 — Polish & release

- Themes/fonts settings, i18n (en/ja), portable mode, help docs + API reference
  pipeline, packaging both OSes (launchers, bundles, codesign/notarize), migration guide
  keyhac-win→2, beta cycle with existing users.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| PyObjC AX/Quartz gaps (some AX functions poorly bridged) | S2 fails partially | Known alternatives: `objc.loadBundleFunctions` manual bridging; worst case a micro C extension for the few calls |
| GC/GIL pause inside hook deadline | stutter, unhook | Engine allocs minimized on hot path; `gc.freeze` after config load + tuned thresholds; watchdogs recover regardless |
| PuiKit multi-window refactor larger than hoped | M2/M3 slip | Fallback: multiple Backend instances + host loop (works on Windows today; mac needs only E3) |
| Two-source truth drift (old repos keep evolving) | wasted porting | Freeze feature reference at win 1.83 / mac 1.68; changes after that reviewed one-off |
| ctypes hook callback reentrancy (config runs Python inside hook while UI pumps) | subtle bugs | Same discipline as predecessors: engine lock; no nested loops (blocking popListWindow dropped by design) |

## Open decisions (owner: crftwr)

1. keyhac-win camelCase compat shim: confirmed **no** (migration guide only)?
2. `Mod-` portable modifier: built-in or `define_alias` only?
3. Cron/periodic API: drop, or `keymap.every(seconds, f)`?
4. Bundle id / product naming (`crftwr.Keyhac` reuse vs new id — affects AX permission
   carry-over from keyhac-mac).
5. Migemo in chooser: pure-Python port, ctypes to C/Migemo (win only), or drop.
6. Windows rich-clipboard history fidelity (HTML/DIB) in v2.0 or later.
