"""Does the UIA Text pattern answer in the editors people actually run?

`doc/dev/ai-integration.md` §6 rests on a cheap path: read the whole buffer,
take the last match, and never touch the caret or the pointer. That path was
measured on macOS (Terminal.app: yes) and on Windows only against Notepad,
which is a weak proxy for anything - a single document surface with no renderer
of its own. This settles the two that matter:

    python tools/text_pattern_survey.py

  - **Windows Terminal**, the analogue of the Terminal.app measurement.
  - **VS Code**, which is Electron. On macOS an Electron app exposes *nothing*
    until asked - a loaded window was 59 nodes of chrome - and only
    `set_manual_accessibility()` moved it. There is no Windows equivalent of
    that call, because Chromium on Windows is supposed to turn accessibility on
    by itself when a UIA client attaches. "Supposed to" is why this exists.

**Notepad is the control, and that is the point of the design.** A survey that
finds nothing in VS Code cannot tell "VS Code exposes no text" from "this probe
is broken", so it also runs against a target already known to answer. A run
where the control fails says nothing about the other two.

Three things this file does deliberately, each of them a lesson from
`tools/uia_pass.py`:

  - **One target at a time, opened and closed before the next.** The first
    version searched for a shared sentinel and matched the *terminal's* window
    while looking for VS Code - the command line it was running contained the
    sentinel, so the title did too. Every target now has its own token.
  - **Its own scratch document**, never the operator's.
  - **Shape, not content.** It reports lengths and whether the sentinel is
    present, and never prints what it read: on a developer's machine the thing
    it is reading is their work.

`get_selection()` is not surveyed. It needs a selection to exist, and injecting
one differs per application; the Notepad pass already established that the call
works on Windows. What §6 leans on is the other two rungs.

Nothing is saved. Each window is closed before the next one opens.
"""

import ctypes
import os
import pathlib
import subprocess
import sys
import tempfile
import time

if sys.platform != "win32":
    sys.exit(f"{__file__} is a Windows survey; this is {sys.platform}.")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from keyhac.core.uitree import get_ui_tree                           # noqa: E402
from keyhac.platform.win.uielement import UIElement                  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                ctypes.c_void_p, ctypes.c_void_p]
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.EnumWindows.argtypes = [WNDENUMPROC, ctypes.c_void_p]

WM_CLOSE = 0x0010

#: In the document, and therefore in what a successful read must contain.
BODY = "KEYHAC-SURVEY-BODY"


def window_titled(fragment):
    found = []

    def visit(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buffer, 512)
            if fragment.lower() in buffer.value.lower():
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(WNDENUMPROC(visit), None)
    return found[0] if found else None


def wait_for_window(fragment, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = window_titled(fragment)
        if hwnd:
            time.sleep(1.0)          # let it finish painting its tree
            return hwnd
        time.sleep(0.5)
    return None


def text_elements(window):
    """Elements claiming the Text pattern.

    "SelectedText" is listed by get_attribute_names() only when the element
    supports TextPattern, so this asks the tree what it can do rather than
    guessing from roles - the lesson Notepad's Find UI taught, that on Windows
    a capability is a more reliable question than a role.
    """
    found = []
    for node in get_ui_tree(window, max_depth=16, max_nodes=2500).walk():
        try:
            if "SelectedText" in node.element.get_attribute_names():
                found.append(node)
        except Exception:                                # noqa: BLE001
            continue
    return found


def describe(node) -> str:
    return (f"{node.role or '?'}"
            + (f" #{node.identifier}" if node.identifier else "")
            + (f" {node.name!r}" if node.name else ""))


def survey(label, hwnd, note=""):
    print(f"\n=== {label} ===")
    if not hwnd:
        print("  window not found - nothing measured")
        return False
    window = UIElement.from_hwnd(hwnd)
    if window is None:
        print("  no UI Automation element for that window")
        return False

    nodes = list(get_ui_tree(window, max_depth=16, max_nodes=2500).walk())
    print(f"  {len(nodes)} nodes; roles: "
          f"{sorted({n.role for n in nodes if n.role})[:12]}")

    candidates = text_elements(window)
    print(f"  elements supporting the Text pattern: {len(candidates)}")
    if not candidates:
        print("  => the Text pattern is offered nowhere in this window")
        if note:
            print(f"     {note}")
        return False

    # The one that matters is whichever actually holds the document. Ranking by
    # that rather than by position keeps a window full of labels - each of them
    # a Text-pattern element - from burying the editor surface.
    holders = []
    for node in candidates:
        try:
            whole = node.element.get_text()
        except Exception:                                # noqa: BLE001
            continue
        if whole and BODY in whole:
            holders.append((node, whole))

    print(f"  of those, holding the document body: {len(holders)}")
    if not holders:
        print("  => the Text pattern is offered, but nothing exposes the buffer")
        for node in candidates[:5]:
            print(f"     - {describe(node)}")
        if note:
            print(f"     {note}")
        return False

    for node, whole in holders[:3]:
        line = node.element.get_line_at_caret()
        same = bool(line and line.strip() == whole.strip())
        print(f"  {describe(node)}")
        print(f"    get_text():          {len(whole)} chars, holds the body")
        print(f"    get_line_at_caret(): "
              + ("None" if line is None else f"{len(line)} chars")
              + ("  (the whole buffer, so not a line)" if same else ""))
    return True


def close(hwnd):
    if hwnd:
        user32.PostMessageW(hwnd, WM_CLOSE, None, None)
        time.sleep(1.5)


def main():
    print(__doc__.split("\n\n")[0])
    print(f"python {sys.version.split()[0]} on {sys.getwindowsversion()}")

    scratch = pathlib.Path(tempfile.mkdtemp(prefix="keyhac-text-survey-"))
    results = {}

    def document(token):
        path = scratch / f"{token}.txt"
        path.write_text("\r\n".join(f"{BODY}-line-{n}" for n in (1, 2, 3)) + "\r\n",
                        encoding="utf-8")
        return path

    # -- the control ---------------------------------------------------------
    token = "KEYHAC-SURVEY-NOTEPAD"
    subprocess.Popen(["notepad.exe", str(document(token))])
    hwnd = wait_for_window(token, timeout=25)
    results["Notepad (control)"] = survey("Notepad - the control", hwnd)
    close(hwnd)

    # -- Windows Terminal ----------------------------------------------------
    token = "KEYHAC-SURVEY-WT"
    hwnd = None
    # Two things about launching wt, both of which cost a run each.
    #
    # `;` is Windows Terminal's *own* subcommand separator, so a PowerShell
    # -Command containing one is split by wt into extra tabs and never reaches
    # the shell intact. A -File avoids the question entirely.
    #
    # And the shell sets the title, not `wt --title`: wt honours --title only
    # until the hosted program names the window itself, which PowerShell does
    # promptly, using its own command line. That command line contained the
    # body text here, so the window ended up matching the *next* target's
    # search - which is how the first run measured the terminal and labelled it
    # VS Code.
    script = scratch / "survey.ps1"
    script.write_text(
        f"$host.UI.RawUI.WindowTitle = '{token}'\n"
        f"Write-Host '{BODY}-line-1'\n"
        f"Write-Host '{BODY}-line-2'\n", encoding="utf-8")
    try:
        subprocess.Popen(
            ["wt.exe", "--title", token, "powershell.exe", "-NoLogo", "-NoExit",
             "-ExecutionPolicy", "Bypass", "-File", str(script)])
    except OSError:
        print("\n=== Windows Terminal ===\n  wt.exe not installed")
    else:
        # Cold-starting wt on this machine took over 40s once and about 2s
        # warm, so this waits generously rather than reporting a launch as a
        # measurement.
        hwnd = wait_for_window(token, timeout=90)
    results["Windows Terminal"] = survey("Windows Terminal", hwnd)
    close(hwnd)

    # -- VS Code -------------------------------------------------------------
    token = "KEYHAC-SURVEY-CODE"
    code = "C:\\Program Files\\Microsoft VS Code\\bin\\code.cmd"
    hwnd = None
    if not os.path.exists(code):
        print("\n=== VS Code ===\n  not installed at the expected path")
    else:
        # --new-window, so this opens *our* file rather than joining whatever
        # the operator has open: the Notepad lesson, applied in advance.
        subprocess.Popen([code, "--new-window", str(document(token))], shell=True)
        hwnd = wait_for_window(token, timeout=60)
    results["VS Code"] = survey(
        "VS Code (Electron)", hwnd,
        note="Chromium is supposed to enable accessibility when a UIA client "
             "attaches. If this found nothing, relaunch it with "
             "--force-renderer-accessibility and compare - the difference is "
             "the finding.")
    close(hwnd)

    print("\n--- summary ---")
    for name, answered in results.items():
        print(f"  {name:22} {'whole-buffer read works' if answered else 'no'}")
    if not results.get("Notepad (control)"):
        print("\n  The control failed, so the other rows say nothing about those "
              "applications - fix the probe before believing them.")
    print("\nClosed what it opened. Nothing was saved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
