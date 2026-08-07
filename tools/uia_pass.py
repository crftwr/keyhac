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

The target is Notepad, because it is always present and its Find UI gives a
toggle and a transient panel for free.

It opens its **own** scratch file and will not write anywhere else.  The first
run of this pass did: Windows 11's Notepad is tabbed and single-instance, so
launching it merely activated the window the operator already had open - on
their real ~/.keyhac/config.py - and `FindWindowW("Notepad", None)` adopted it.
Every write went into that buffer.  Nothing reached the disk, but a harness
that can overwrite the file it is testing against is a harness with a bug in
it.  Hence: a scratch file, opened by name; the window located by *its* title;
the editable element identified by a sentinel string that only this pass
writes; and a refusal to write at all if any of that does not line up.

Run the hermetic suite first (`pytest -q`): the portable half of this code is
covered there, and this pass is only for what a machine has to prove.

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
user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]

WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.EnumWindows.argtypes = [WNDENUMPROC, ctypes.c_void_p]

#: The scratch document's stem, which is also what its window title contains.
DOCUMENT = "keyhac-uia-pass"

#: Written into the scratch file, and required to be present in an element
#: before anything is written to it.  This is the guard that keeps the pass off
#: the operator's own documents when Notepad has several tabs open.
SENTINEL = "KEYHAC-UIA-PASS-SCRATCH-DO-NOT-SAVE"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def section(title):
    print(f"\n=== {title} ===")


def window_titled(fragment):
    """The first visible top-level window whose title contains `fragment`.

    By title rather than by class, because the class is exactly what went
    wrong: `FindWindowW("Notepad", None)` names *a* Notepad, and on Windows 11
    there is only ever one - the operator's.  The title is what distinguishes
    our document from theirs.  It also survives the "*" Notepad prepends once
    the document is modified, since this is a containment test.
    """
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


def notepad_window():
    """The window holding our scratch document, as a UIElement, or None."""
    hwnd = window_titled(DOCUMENT)
    return UIElement.from_hwnd(hwnd) if hwnd else None


def popup_root(element, limit: int = 6):
    """The panel an element belongs to, walking up from it.

    Wanted because "in the Find UI" is a structural fact and there is no
    property that states it: the search field is the only thing here that can
    be named with confidence, so the panel is defined as what encloses it.
    Stops at the first Window (Notepad 11 hosts the panel in a popup Window;
    classic Notepad's Find *is* a window), or gives up and returns the last
    ancestor it reached rather than the desktop.
    """
    current = element
    for _ in range(limit):
        parent = current.parent()
        if parent is None:
            break
        current = parent
        if parent.get_attribute_value("ControlType") == "Window":
            break
    return current


def write_fixture(edit, element, text):
    """Get multi-line text into the document, whatever it takes.

    The text-layer checks need real line breaks in the buffer, and the write
    mechanism is not what they are testing - so this tries the mechanisms in
    order of how little can go wrong (set_value touches neither the clipboard
    nor the IME) and reports which one worked.  Line breaks are checked for
    explicitly: a write that arrives with its newlines eaten leaves
    get_line_at_caret() looking broken when the fixture is what broke.
    """
    for method in ("set_value", "keys", "paste"):
        try:
            fill.set_text(edit, text, methods=(method,), verify=False)
        except fill.FillFailed:
            continue
        time.sleep(0.4)
        whole = element.get_text() or ""
        if ("second line" in whole and "third line" in whole
                and ("\r" in whole or "\n" in whole)):
            return method, whole
    return None, (element.get_text() or "")


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

    # Our own document, opened by name. Notepad 11 puts it in a new tab of the
    # window that is already running, which is fine - what matters is that the
    # title identifies it and the sentinel identifies its text element.
    document = scratch / f"{DOCUMENT}.txt"
    document.write_text(SENTINEL + "\r\n")
    subprocess.Popen(["notepad.exe", str(document)])
    time.sleep(1.5)

    section("tree: children() - the walker slots")
    window = wait_for(notepad_window, timeout=15,
                      message=f"a Notepad window titled {DOCUMENT}")
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

    # By the sentinel, not by role alone: a tabbed Notepad exposes a Document
    # per tab and the first one is whichever tab the operator left open.
    try:
        edit = wait_for(
            lambda: find_element(window, role="Document|Edit|Text",
                                 value=f"*{SENTINEL}*"),
            timeout=10, message="the scratch document's text element")
    except WaitTimeout:
        edit = None
    check("the scratch text area is findable", edit is not None,
          f"{edit!r}" if edit else "nothing in the tree holds the sentinel")
    if edit is None:
        print("\nRefusing to write: the element holding the sentinel was not "
              "found, and every other document in this window belongs to "
              "someone else.")
        return report()

    element = edit.element if hasattr(edit, "element") else edit

    # Focus is the other half of the guard. The element is ours, but the keys
    # go wherever focus actually is, so an unfocused write would land in the
    # operator's tab even with the right element in hand.
    element.set_focus()
    time.sleep(0.3)
    focused = bool(element.get_attribute_value("HasKeyboardFocus"))
    check("the scratch document has keyboard focus", focused)
    if not focused:
        print("\nRefusing to write: keystrokes would go to whatever does have "
              "focus. Click the scratch tab and rerun.")
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
            # A mechanism can legitimately not work against a given control;
            # what must never happen is it failing quietly. "keys" does not
            # work here: Windows 11's Notepad is a WinUI editor that reorders a
            # burst of injected unicode - "hello-keys" arrived as "helloke-ys"
            # - while the same string down the same code path lands intact in a
            # plain Win32 control, 30/30 with the hook installed. So the
            # property left to pin is that the read-back caught it instead of
            # reporting scrambled text as a successful write.
            check(f"set_text via {method} failed loudly rather than quietly",
                  "wrote nothing readable" in str(error), str(error)[:160])

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

    # The regression check for the race the first run of this pass found: with
    # verify=False there is no read-back, so the restore used to go out in the
    # same breath as Ctrl-V and the field received the *previous* clipboard.
    # Two claims, and only the first is ours. The stale clipboard must never
    # arrive - that is the bug. Whether the paste lands at all is this target's
    # business: Notepad drops an injected Ctrl-V outright often enough that a
    # single attempt made the check read as a regression when what had happened
    # was that nothing arrived, leaving the previous write in place. So retry,
    # and let the attempt count carry that.
    clipboard.set_text("STALE-CLIPBOARD-MUST-NOT-ARRIVE")
    landed, stale, attempt = "", False, 0
    for attempt in range(1, 4):
        try:
            fill.set_text(edit, "RACE-CHECK", methods=("paste",), verify=False)
        except fill.FillFailed:
            pass
        time.sleep(0.4)
        landed = element.get_text() or ""
        stale = stale or "STALE-CLIPBOARD" in landed
        if "RACE-CHECK" in landed:
            break
    check("unverified paste never delivers the previous clipboard", not stale,
          repr(landed[:70]))
    check("...and the pasted text itself arrived", "RACE-CHECK" in landed,
          f"{landed[:40]!r} after {attempt} attempt(s)")

    section("text layer - DocumentRange 7, ExpandToEnclosingUnit 6")
    fixture = "first line\r\nsecond line has REC-002\r\nthird line"
    method, whole = write_fixture(edit, element, fixture)
    check("the multi-line fixture went in", method is not None,
          f"via {method}" if method else f"buffer reads {whole[:70]!r}")
    check("get_text() returns the buffer",
          bool(whole and "second line" in whole and "third line" in whole),
          repr((whole or "")[:70]))
    if method is None:
        # Every check below reads this text back; without it they would report
        # slot failures that are really this write's failure - which is exactly
        # how the first run's three FAILs came about.
        print("\nSkipping the text-layer checks: the fixture never arrived, so "
              "anything they said about the vtable slots would be about the "
              "write instead.")
        return report()

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

    section("modal three-beat + toggle (Find)")
    # Three things vary here, and the first run of this pass guessed all three.
    # Classic Notepad opens Find as its own top-level window while Windows 11's
    # opens a popup inside this one, so search both. Windows 11 puts no toggle
    # in that popup at all - "Match case" and the rest are behind its "More
    # options" button. And what appears then is not a CheckBox.
    #
    # So the toggle is located by *capability*: an element with a ToggleState
    # is one set_checked can drive, whatever the platform calls it. Which is
    # the macOS lesson from the authoring skill in its Windows form - a control
    # is defined by what it can do, not by the role it reports.
    def togglable(node):
        return node.element.get_attribute_value("ToggleState") is not None

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
        # Beat one, anchored on the search field: it is the one part of this UI
        # that every Notepad has, and it is present the moment the panel is.
        def find_field():
            return (anywhere(role="Edit|Text", name="Find*")
                    or anywhere(identifier="FindSuggestBox"))

        field = wait_for(find_field, timeout=8,
                         message="the Find UI to appear")
        check("wait_for saw the Find UI", field is not None, repr(field))

        # Scoped to the panel, not to the window. Searching the window found
        # the *toolbar's* Bold button - it has a ToggleState, it is nothing to
        # do with Find, and the pass cheerfully toggled it. Capability answers
        # "can set_checked drive this"; only the subtree answers "is this part
        # of the thing that just opened".
        panel = popup_root(field.element)
        box = find_element(panel, predicate=togglable)
        opened_menu = False
        if box is None:
            # Windows 11 keeps them one press further in.
            more = find_element(panel, name="More options*", role="Button")
            if more is not None:
                fill.press(more)
                opened_menu = True
                box = wait_for(lambda: anywhere(predicate=togglable), timeout=6,
                               message="a toggle under \"More options\"")
        check("a togglable control is reachable", box is not None,
              f"{box!r} ToggleState="
              f"{box.element.get_attribute_value('ToggleState')!r}"
              if box is not None else "nothing in the Find UI has a ToggleState")
        if box is None:
            # So a Find UI built some third way costs one more reading of this
            # output rather than one more trip to a Windows machine.
            print("\nWhat the Find UI actually holds:")
            print(format_tree(get_ui_tree(find_area()[-1], max_depth=10,
                                          max_nodes=200)))
            raise WaitTimeout("no togglable control in the Find UI")

        # Idempotency without reaching into private helpers: ask for the same
        # state twice and require the second call to press nothing.
        fill.set_checked(box, True)
        again = fill.set_checked(box, True)
        # Read it the way set_checked does. The value is where a CheckBox keeps
        # its state and ToggleState is where a ToggleButton keeps it; requiring
        # the first is what made this look unsupported on Windows 11.
        value = fill.read_value(box)
        state = box.element.get_attribute_value("ToggleState")
        check("the toggle reads as checked after set_checked(True)",
              str(value).strip().lower() in ("1", "true", "on") or state == 1,
              f"value={value!r} ToggleState={state!r}")
        check("set_checked is idempotent (second call presses nothing)",
              again is False)
        fill.set_checked(box, False)

        # Beat three. One Escape per thing that was opened - the menu only
        # exists if we opened it - and the wait is on the field, since a menu
        # closing would otherwise read as the panel having closed.
        for _ in range(2 if opened_menu else 1):
            with keymap.get_input_context() as ctx:
                ctx.send_key("Escape")
            time.sleep(0.3)
        wait_for(lambda: find_field() is None, timeout=8,
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
    print(f"\nClose the {DOCUMENT} tab. It is a scratch file in a temp "
          f"directory, so saving it harms nothing - which is the point.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
