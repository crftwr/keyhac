# Windows session checklist

Everything queued for the next Windows sitting, in dependency order.
All code below is written to spec but has never executed on Windows unless
marked done. Start every bring-up with `tools/hook_echo.py`.

## 0. Setup
- `git pull` both repos; puikit branch `keyhac-window-extensions`.
- `make venv` in keyhac2 (installs ../puikit editable).

## 1. M1 interactive checklist (hook, still open from the first session)
- `make echo`: fast typing burst - no silent unhook; JIS layout detection.
- `make run-sandbox ARGS=-d`: remap, replace_key, one-shot, multi-stroke,
  `C-`/`W-` short forms, `class_name=` table (notepad/Edit).
- Put `time.sleep(0.5)` in a bound action: sanity-check re-install fires
  ("Key hook force cancellation detected"), modifiers recover.
- Extended keys: arrows/Home/End/PgUp/PgDn/Ins/Del/RCtrl/RAlt output
  correctly (EXTENDED_VKS table in platform/win/hook.py may need tuning).

## 2. M2 console window
- Console opens, log streams, hook toggle, level dropdown, inspector.
- WM_CLOSE with main_window_close="hide": close hides, app keeps running.
- DPI: per-monitor scaling on a >100% display.

## 3. M3 clipboard + app control
- WinClipboardProvider: copies land in history (sequence-number poll);
  get/set round-trip incl. Japanese text.
- WinAppControl.activate_pid: ActivateWindow(app=...) foregrounds
  (foreground-lock restrictions may need the AttachThreadInput fallback
  from keyhac-win if SetForegroundWindow is refused).
- send_text (KEYEVENTF_UNICODE): ASCII + Japanese + emoji (surrogates).

## 4. M4 tray (puikit 281ff04)
- set_tray: icon appears (host exe icon), tooltip, left/right click menu,
  checkmark state, Quit works, icon removed on exit.
- show_main_window after close-to-hide.

## 5. Multi-window (NOT implemented on Windows yet - largest item)
puikit `multi_window` is False on WindowsBackend. Implementation plan:
- Per-window state object holding hwnd + swap chain + target bitmap +
  render target + front/back lists (factor out of the backend fields the
  same way MacOSBackend routes _front/_back through _active_win).
- Reuse _CLASS_NAME windows; _hwnd_backends already routes wndproc by
  hwnd - extend the lookup to (backend, window_handle).
- WM_PAINT renders the per-window front list into that window's target;
  _create_render_resources parameterized by hwnd.
- WindowStyle: WS_POPUP / WS_EX_TOPMOST / WS_EX_NOACTIVATE / TOOLWINDOW
  already honored at CreateWindowExW (open() path) - reuse for
  create_window.
- Until it lands: chooser and balloon log an error on Windows.

## 6. Known-suspect list (cold-written code most likely to need fixes)
- KEYBDINPUT wScan for send_text vs MapVirtualKeyW interplay
- Shell_NotifyIconW struct size on different Windows versions
  (cbSize/legacy variants)
- EnumDisplayMonitors callback lifetime (MONITORENUMPROC must be kept
  referenced during the call - it is, as a local)
- WinEventLoop thread-id timers vs the puikit pump (keyhac2 headless mode)
