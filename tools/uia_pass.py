"""Live verification pass for the Windows element API (Windows only).

Everything added for the AI-integration work was written on macOS and verified
there.  The Windows half - the child walk, the text-layer accessors,
element_at_point, and the write side - was written against
UIAutomationClient.h and has never executed.  This is the pass that settles it,
in one sitting:

    python tools/uia_pass.py

Why it matters more than usual here: COM methods are called through their
vtable slot index, so a wrong index does not raise - it silently calls a
different method.  `keyhac/platform/win/uielement.py` already caught two that
way (a RECT read as doubles, and GetText sitting at 12 while 11
access-violated), which is why every slot in that file is supposed to be
pinned against a Win32 answer before it is trusted.  The slots this pass
exercises are:

    IUIAutomationElement    GetFirstChildElement 4, GetNextSiblingElement 6
    IUIAutomationTextPattern      get_DocumentRange 7
    IUIAutomationTextRange        ExpandToEnclosingUnit 6 (TextUnit_Line = 3)
    IUIAutomation                 ElementFromPoint 7

It also answers the measurement doc/dev/ai-integration.md §11 asks for - does
`set_value` work on the target systems - by timing all three write mechanisms
against a real control.

The target is Notepad, because it is always present and its Find dialog gives
a checkbox and a modal for free.  Nothing is saved; the document is closed
without saving at the end.  Run the hermetic suite first (`pytest -q`): the
portable half of this code is covered there, and this pass is only for what a
machine has to prove.

Paste the output back verbatim - the FAIL lines are the interesting ones, and
a wrong slot usually shows up as a plausible-looking wrong answer rather than
an error.
"""

import sys
import time

if sys.platform != "win32":
    sys.exit(f"{__file__} is a Windows pass; this is {sys.platform}.")

import subprocess                                                   # noqa: E402
import ctypes                                                       # noqa: E402
import tempfile                                                     # noqa: E402
import pathlib                                                      # noqa: E402

from keyhac.core.clipboard_history import ClipboardHistory          # noqa: E402
from keyhac.core.keymap import Keymap                               # noqa: E402
from keyhac.core.uitree import find_element, format_tree, get_ui_tree  # noqa: E402
from keyhac.core.vk import init_key_names                           # noqa: E402
from keyhac.core import fill                                        # noqa: E402
from keyhac.core.wait import wait_for, WaitTimeout           # noqa: E402
from keyhac.platform.fake import FakeFocusProvider                  # noqa: E402
from keyhac.platform.win.clipboard import WinClipboardProvider      # noqa: E402
from keyhac.platform.win.hook import WinInputHook                   # noqa: E402
from keyhac.platform.win.uielement import UIElement                 # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)

# Mandatory on 64-bit, and the same trap keyhac/platform/win/focus.py calls
# out: ctypes defaults restype to c_int, which truncates a pointer-sized HWND
# to 32 bits. The handle then looks plausible and matches nothing.
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.FindWindowW.restype = ctypes.c_void_p
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = ctypes.c_void_p

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def section(title):
    print(f"\n=== {title} ===")


def notepad_window():
    """The Notepad main window as a UIElement, or None."""
    hwnd = user32.FindWindowW("Notepad", None)
    if not hwnd:
        # Windows 11's Notepad is a WinUI app with a different class name.
        for class_name in ("ApplicationFrameWindow", "Notepad"):
            hwnd = user32.FindWindowW(class_name, None)
            if hwnd:
                break
    return UIElement.from_hwnd(hwnd) if hwnd else None


def main():
    print(__doc__.split("\n\n")[0])
    print(f"python {sys.version.split()[0]} on {sys.getwindowsversion()}\n")

    # The lesson from the macOS side, and the reason it is spelled out here:
    # configure() is what populates the modifier map, and without it send_key()
    # emits the key with no modifiers at all - silently. A pass that skipped it
    # would report that pasting does not work on Windows, and be wrong.
    init_key_names("windows", "ansi")
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="keyhac-uia-pass-"))
    # An explicit empty config: with no config_path, configure() would load -
    # and if absent, create - the operator's real ~/.keyhac/config.py. A
    # verification pass has no business doing either.
    config = scratch / "config.py"
    config.write_text("def configure(keymap):\n    pass\n")

    hook = WinInputHook()
    keymap = Keymap(hook, FakeFocusProvider(), "windows",
                    config_path=str(config), template_path=str(config))
    keymap.configure()
    clipboard = WinClipboardProvider()
    history = ClipboardHistory(clipboard, filename=str(scratch / "clipboard.json"))
    history.persist = False
    keymap._clipboard_history = history

    subprocess.Popen(["notepad.exe"])
    time.sleep(1.5)

    section("tree: children() - the walker slots")
    window = wait_for(notepad_window, timeout=15, message="a Notepad window")
    check("from_hwnd + window found", window is not None)

    children = window.children()
    check("children() returns elements", bool(children),
          f"{len(children)} direct children")
    tree = get_ui_tree(window, max_depth=10, max_nodes=400)
    nodes = list(tree.walk())
    check("get_ui_tree walks past the root", len(nodes) > 3,
          f"{len(nodes)} nodes, roles: "
          f"{sorted({n.role for n in nodes if n.role})[:8]}")
    print(format_tree(get_ui_tree(window, max_depth=4, max_nodes=40)))

    edit = (find_element(window, role="Document")
            or find_element(window, role="Edit")
            or find_element(window, role="Text"))
    check("the text area is findable", edit is not None,
          f"{edit!r}" if edit else "no Document/Edit/Text in the tree")
    if edit is None:
        return report()

    section("write side - the §11 measurement")
    for method in ("set_value", "paste", "keys"):
        text = f"hello-{method}"
        started = time.perf_counter()
        try:
            fill.set_text(edit, text, methods=(method,), timeout=3.0)
            elapsed = (time.perf_counter() - started) * 1000
            check(f"set_text via {method}", True, f"{elapsed:.0f} ms")
        except fill.FillFailed as error:
            check(f"set_text via {method}", False, str(error)[:160])

    try:
        used = fill.set_text(edit, "REC-001")
        check("default chain (paste, then keys)", True, f"used {used!r}")
    except fill.FillFailed as error:
        check("default chain (paste, then keys)", False, str(error)[:160])

    saved = clipboard.get_text()
    with fill.preserve_clipboard():
        clipboard.set_text("scratch")
    check("preserve_clipboard restores", clipboard.get_text() == saved,
          f"{clipboard.get_text()!r}")

    section("text layer - DocumentRange 7, ExpandToEnclosingUnit 6")
    fill.set_text(edit, "first line\r\nsecond line has REC-002\r\nthird line",
                  verify=False)
    time.sleep(0.4)
    whole = edit.element.get_text() if hasattr(edit, "element") else edit.get_text()
    check("get_text() returns the buffer", bool(whole and "second line" in whole),
          repr((whole or "")[:70]))

    element = edit.element if hasattr(edit, "element") else edit
    element.set_focus()
    # Caret to the start, then down one line: the line at the caret should be
    # the second one. If ExpandToEnclosingUnit is at the wrong slot this
    # usually returns the whole document or a single character, not an error.
    with keymap.get_input_context() as ctx:
        ctx.send_key("Ctrl-Home")
    with keymap.get_input_context() as ctx:
        ctx.send_key("Down")
    time.sleep(0.3)
    line = element.get_line_at_caret()
    check("get_line_at_caret() is one line", bool(line) and "second line" in (line or ""),
          repr(line))
    check("get_line_at_caret() is NOT the whole document",
          bool(line) and "third line" not in (line or ""),
          "a whole-document answer means the TextUnit or the slot is wrong")

    section("selection")
    with keymap.get_input_context() as ctx:
        ctx.send_key("Ctrl-A")
    time.sleep(0.3)
    selection = element.get_selection()
    check("get_selection() after Ctrl-A", bool(selection and "second" in selection),
          repr((selection or "")[:60]))

    section("element_at_point - ElementFromPoint 7")
    rect = (edit.rect if hasattr(edit, "rect") else None) or \
        element.get_attribute_value("BoundingRectangle")
    if rect:
        x, y = rect[0] + rect[2] / 2, rect[1] + rect[3] / 2
        hit = UIElement.element_at_point(x, y)
        check("element_at_point returns an element", hit is not None)
        if hit is not None:
            hit_rect = hit.get_attribute_value("BoundingRectangle")
            check("element_at_point hits the text area (or a child of it)",
                  hit_rect is not None
                  and hit_rect[0] >= rect[0] - 2 and hit_rect[1] >= rect[1] - 2,
                  f"{hit.get_attribute_value('ControlType')} at {hit_rect}")
    else:
        check("BoundingRectangle available for the hit test", False)

    section("focus")
    check("set_focus() reports success", bool(element.set_focus()))
    check("...and HasKeyboardFocus agrees",
          bool(element.get_attribute_value("HasKeyboardFocus")))

    section("modal three-beat + checkbox (Find)")
    # Classic Notepad opens Find as its own top-level window; Windows 11's
    # opens an in-app bar inside the same one. Search both rather than assuming.
    def find_area():
        foreground = UIElement.from_hwnd(user32.GetForegroundWindow())
        return [area for area in (window, foreground) if area is not None]

    def anywhere(**criteria):
        for area in find_area():
            found = find_element(area, **criteria)
            if found is not None:
                return found
        return None

    try:
        with keymap.get_input_context() as ctx:
            ctx.send_key("Ctrl-F")
        box = wait_for(lambda: anywhere(role="CheckBox"), timeout=8,
                       message="the Find UI to appear with its checkbox")
        check("wait_for saw the Find UI", box is not None, repr(box))

        # Idempotency without reaching into private helpers: ask for the same
        # state twice and require the second call to press nothing.
        fill.set_checked(box, True)
        again = fill.set_checked(box, True)
        check("checkbox reads as checked after set_checked(True)",
              str(fill.read_value(box)).strip().lower() in ("1", "true", "on"),
              repr(fill.read_value(box)))
        check("set_checked is idempotent (second call presses nothing)",
              again is False)
        fill.set_checked(box, False)

        with keymap.get_input_context() as ctx:
            ctx.send_key("Escape")
        wait_for(lambda: anywhere(role="CheckBox") is None, timeout=8,
                 message="the Find UI to close")
        check("the Find UI closed again", True)
    except WaitTimeout as error:
        check("modal three-beat", False, str(error)[:160])
    except Exception as error:                       # noqa: BLE001
        check("modal three-beat", False, f"{type(error).__name__}: {error}")

    return report()


def report():
    print()
    failed = [name for name, ok in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print("failed:")
        for name in failed:
            print(f"  - {name}")
    print("\nClose Notepad without saving.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
