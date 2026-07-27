# Windows session checklist

Everything queued for the next Windows sitting, in dependency order.
Code marked "written to spec" has never executed on Windows; everything else
has been run there. Start a hook bring-up with `tools/hook_echo.py`.

## 0. Setup
- `git pull` both repos; puikit `main` (the window extensions, the DPI font
  fix and Windows multi-window all shipped — PRs #76-#79).
- `make venv` in keyhac2 (installs ../puikit editable).

## 1. M1 interactive checklist (hook, still open)
The hook installs, delivers callbacks, and reports focus live, but **no key
has ever been consumed on Windows** — every run so far logged only PASSTHRU,
because the sample config's bindings were all unreachable there. With the
config now portable, this is the first thing to exercise.
- `make echo`: fast typing burst - no silent unhook; JIS layout detection.
- `make run-sandbox ARGS=-d`: remap, replace_key, one-shot, multi-stroke,
  `C-`/`W-` short forms, `class_name=` table (notepad/Edit).
- Put `time.sleep(0.5)` in a bound action: sanity-check re-install fires
  ("Key hook force cancellation detected"), modifiers recover.
- Extended keys: arrows/Home/End/PgUp/PgDn/Ins/Del/RCtrl/RAlt output
  correctly (EXTENDED_VKS table in platform/win/hook.py may need tuning).

## 2. M2 console window — DONE
Console opens, log streams, hook toggle and level dropdown work, inspector
shows last key and focus path. Per-monitor DPI verified at 200% (the
half-size widget text was puikit's font cache surviving the pre-open DPI
placeholder; fixed in puikit #77).
- Still open: WM_CLOSE with `main_window_close="hide"`; stdout redirect to
  the console (`print()` from config.py still goes to the terminal).

## 3. M3 clipboard + app control
- WinClipboardProvider: **written to spec** - copies land in history
  (sequence-number poll); get/set round-trip incl. Japanese text.
- send_text (KEYEVENTF_UNICODE): **written to spec** - ASCII + Japanese +
  emoji (surrogates).
- WinAppControl.activate_pid: **written to spec**. Note `Window.activate()`
  (platform/win/window.py) already carries the AttachThreadInput
  foreground-lock workaround and is verified; if activate_pid is refused,
  make it delegate there rather than re-solving it.
- Chooser: renders on Windows (verified against the real ChooserWindow). The
  end-to-end paste flow — select, refocus, Ctrl-V — is untested, and it
  exercises the clipboard provider and activate_pid together, so run it
  first.

## 4. M4 tray
- set_tray: icon appears (host exe icon), tooltip, left/right click menu,
  checkmark state, Quit works, icon removed on exit. **Written to spec** on
  the keyhac side; puikit's implementation is real and now declares the
  `system_tray` capability (#79).
- show_main_window after close-to-hide.
- Balloon window: unblocked by multi-window, never opened on Windows.

## 5. Multi-window — DONE (puikit #78)
`create_window()` gives real secondary HWNDs, each with its own DXGI swap
chain on the shared D3D device. Chooser and balloon both work on it.
Known limit: IME composition stays attached to the main HWND, so a popup
text field types ASCII but does not compose — the chooser's filter box with
a Japanese IME is the case to watch.

## 6. Windows API surface — DONE
- `platform/win/window.py`: the portable `Window`/`WindowProvider` (the
  pyauto.Window analogue). Enumeration excludes the shell desktop window and
  DWM-cloaked windows; `activate()` handles the foreground lock.
- `platform/win/uielement.py`: UI Automation via raw ctypes — element
  attributes, control-view parent walk, Invoke/Toggle/Expand/Collapse
  actions, Value/SelectedText patterns.
- Focus path is now the full UIA control hierarchy.
- `MoveWindow` and `ActivateWindow` both run on Windows.

## 7. Still missing on Windows
- Mouse output commands + WH_MOUSE_LL one-shot cancel (M4) — no mouse code
  exists on either OS yet.
- `settings.json`, single-instance guard (M2 leftovers).

## 8. Known-suspect list (cold-written code most likely to need fixes)
- KEYBDINPUT wScan for send_text vs MapVirtualKeyW interplay
- Shell_NotifyIconW struct size on different Windows versions
  (cbSize/legacy variants)
- WinEventLoop thread-id timers vs the puikit pump (keyhac2 headless mode)

## 9. Performance note
A full UIA focus walk costs ~33 ms on a deep Electron tree, so the focus
provider caches on a ~0.01 ms Win32 probe (foreground hwnd, focused hwnd,
title) and only walks when that changes. If focus paths ever feel slow after
an app switch, the fix is UIA cache requests
(`IUIAutomationCacheRequest` + `BuildUpdatedCache`), which fetch a subtree in
one cross-process call instead of one per property per level.
