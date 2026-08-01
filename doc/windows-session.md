# Windows session status

Originally the checklist for the Windows bring-up sittings; now the record
of what has been verified there and how. As of 2026-08-01 everything
automatable has been run live on this machine (Windows 11 Home 10.0.26200);
the short list of genuinely-interactive leftovers is at the bottom.

## How the automated verification works (patterns worth keeping)

- **In-process app harness** (scratchpad `harnessA.py` / `harnessB.py`
  pattern): host the real engine in the test process - real `WinInputHook`,
  real config loading, real `WinFocusProvider`/`ConsoleWindow`, the same
  wiring `main.py` does - and play the user with *untagged* `SendInput`
  (no Keyhac `dwExtraInfo` sentinel), which the hook classifies as real.
  A probe window records every `WM_KEYDOWN`/`WM_CHAR` that reaches an app.
  No subprocess, so no single-instance-guard collision: a concurrently
  running production Keyhac is untouched (its hook sits later in the LL
  chain; the harness binds only F13-F24, which no human config uses).
- **Foreground-lock probes must arm the lock.** An idle desktop does NOT
  arm it (steals succeed), and any process spawned from the
  foreground-process chain inherits steal permission - both give
  false-green results. A valid probe: WMI-spawn (outside the chain) a
  "target" that takes foreground and keeps receiving synthesized F24 taps,
  then reproduce/steal from a second WMI-spawned process.
- **Window-class + ctypes wndproc in tests**: keep the probe module-scoped.
  Re-registering the class in a second fixture instance leaves
  `lpfnWndProc` pointing at the first instance's freed thunk - the next
  message faults the interpreter.

## 1. M1 hook + engine — DONE (harness, 19/19)

Remap, key sequence (ordered), replace_key, one-shot (lone tap fires /
held+key acts as the plain modifier), `C-`/`S-` short forms, multi-stroke
(match, and the unmatched key-down leaving the mode is still consumed),
class_name-scoped table, extended-key output (Left arrives with the
extended bit). Findings:

- **F21-F24 were missing from the key-name tables** (`core/vk.py` built
  the F row with `range(1, 21)`); Win32 defines VKs to F24 and they now
  parse on Windows (macOS keeps F20 - no scan codes past it - and gets
  the exists-only-on-Windows diagnostic).
- **This Windows build survives a 0.6 s hook-callback stall** - the
  LowLevelHooksTimeout removal did not fire, so the silent-unhook path
  cannot be provoked that way here. The sanity check itself is verified by
  covert removal (`UnhookWindowsHookEx` behind the hook object's back -
  indistinguishable from the OS doing it): raw keys leak, four modifier
  flips with no callbacks trigger "Key hook force cancellation detected -
  re-installing", and consumption works again after.
- Macro record/playback: recorded X,Y replay through the engine and land
  in the probe again (Replay log start/stop/play).

Still hardware-dependent, not automatable: JIS layout detection
(`GetKeyboardType(0) == 7`) on a real JIS keyboard, and general typing
feel under load.

## 2. M2 console — DONE

Console opens, log streams, hook toggle, level dropdown, inspector,
per-monitor DPI at 200% (puikit #77). This session: **stdout redirect
verified** (`print()` from a config lands in the console ring buffer;
the old "still goes to the terminal" note was stale) and **WM_CLOSE
hides** (`main_window_close="hide"`, loop stays alive,
`show_main_window()` re-shows).

## 3. M3 clipboard + app control + chooser — DONE

- WinClipboardProvider: get/set round-trip incl. Japanese **and non-BMP
  emoji** - the emoji case caught a real bug (`create_unicode_buffer`
  sizes by code points, truncating surrogate pairs; set_text now encodes
  UTF-16-LE explicitly). Sequence-number poll, empty clipboard. Tests:
  `tests/test_win_clipboard.py`.
- send_text (KEYEVENTF_UNICODE): ASCII + Japanese + emoji arrive through
  the real VK_PACKET -> TranslateMessage -> WM_CHAR path.
  Tests: `tests/test_win_send_text.py`.
- WinAppControl.activate_pid: **the user-reported chooser-focus bug** was
  the anticipated one - bare SetForegroundWindow refused under the
  foreground lock. It now delegates to `Window.activate()`, picking the
  process's first visible window in Z-order (topmost chooser wins over
  the console).
- **`Window.activate()` needed the dual attach**: attaching to the
  foreground thread alone (the classic recipe and keyhac-win's) is no
  longer honored by Windows 11 under an armed lock for a *cross-process*
  target; attaching to both the foreground thread and the target's thread
  works. Verified against a genuinely armed lock.
- Chooser end-to-end (harness B, 14/14): chooser opens **with keyboard
  focus**, filter narrows, Enter refocuses the original (cross-process)
  app and Ctrl-V pastes into its real EDIT control, Shift-Enter sets the
  clipboard without pasting, the hotkey toggles the chooser closed with
  refocus.

## 4. M4 tray, balloon, macro, mouse — DONE

- Tray ran live in the earlier Keyhac.exe bundle session (see CLAUDE.md).
- Balloon: frameless topmost no-activate secondary HWND opens without
  stealing foreground, correct ex-styles, placed in the work area,
  replace-by-name and close verified (`tests/test_win_balloon.py`).
- Macro record/playback: harness-verified (see §1).
- **Mouse output implemented** (this session): `MouseMove(dx, dy)`,
  `MouseButtonDown/Up/Click(button)`, `MouseWheel(n)`,
  `MouseHorizontalWheel(n)` action classes; `InputContext.send_mouse_*`
  (buttons/wheels release held modifiers and restore them, keyhac-win
  behavior; moves keep them); `InputHook.send_mouse()`/`cursor_pos()`;
  Windows injection converts relative moves to absolute virtual-desktop
  coordinates so pointer acceleration cannot distort them. **WH_MOUSE_LL
  one-shot cancel**: observation-only hook; physical button/wheel input
  cancels a pending one-shot (`Keymap.on_mouse_event`), Keyhac's own
  tagged output is ignored. Live tests: `tests/test_win_mouse.py`;
  engine tests: `tests/test_mouse.py`. macOS: not yet (as keyhac-mac;
  planned route is mouse types in the tap mask).

## 5. Multi-window — DONE (puikit #78)

Known limit, still open: IME composition stays attached to the main HWND,
so a popup text field types ASCII but does not compose - the chooser's
filter box with a Japanese IME is the case to watch (needs interactive
confirmation, fix would be puikit work).

## 6. Windows API surface — DONE

window.py (portable Window/WindowProvider incl. work frames + SnapWindow,
verified live in tests/test_win_window.py), uielement.py (UIA), full UIA
focus path, MoveWindow/ActivateWindow.

## 7. Remaining (genuinely interactive / hardware)

- Typing feel under real fast typing; JIS keyboard layout detection.
- Chooser filter box with a Japanese IME (see §5).
- `windows_app/` Keyhac.exe bundle: build + import smoke done and tray ran
  live, but a full interactive pass on the bundle (as opposed to
  `python -m keyhac`) hasn't been repeated since the fixes landed.
- puikit 1.0.7 PyPI release (until then the venv must keep the editable
  checkout: tray image + main-window visibility APIs).

## 8. Performance note

A full UIA focus walk costs ~33 ms on a deep Electron tree, so the focus
provider caches on a ~0.01 ms Win32 probe (foreground hwnd, focused hwnd,
title) and only walks when that changes. If focus paths ever feel slow
after an app switch, the fix is UIA cache requests
(`IUIAutomationCacheRequest` + `BuildUpdatedCache`), which fetch a subtree
in one cross-process call instead of one per property per level.
