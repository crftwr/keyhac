"""Windows UI Automation element behavior that is a *choice*, not a slot index.

The hand-written vtable indices are pinned in test_win_focus.py against the
Win32 answer for the same window; what is here is what the element decides on
top of them.
"""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from keyhac.platform.win.uielement import UIElement  # noqa: E402


def _detached_element():
    """A UIElement with no COM pointer, for a method that dereferences none."""
    element = UIElement.__new__(UIElement)
    element._ptr = None
    element._native_hwnd = None
    element._window_control_type_id = None
    return element


def _report_control_type(monkeypatch, control_type_id):
    import ctypes

    import keyhac.platform.win.uielement as uielement

    def call(_ptr, _slot, _restype, _argtypes, out):
        ctypes.cast(out, ctypes.POINTER(ctypes.c_int)).contents.value = control_type_id
        return uielement.S_OK

    monkeypatch.setattr(uielement, "_com_call", call)


def test_describe_remembers_the_native_handle_of_a_window(monkeypatch):
    element = _detached_element()
    attributes = {"ControlType": "Window", "NativeWindowHandle": 0x1234}
    monkeypatch.setattr(element, "get_attribute_value", attributes.get)

    assert element.describe()["role"] == "Window"
    assert element._native_hwnd == 0x1234
    assert element._window_control_type_id == 50032


def test_a_destroyed_window_handle_is_stale_before_ui_automation_is_asked(monkeypatch):
    import keyhac.platform.win.uielement as uielement

    element = _detached_element()
    element._native_hwnd = 0x1234
    element._window_control_type_id = uielement.WINDOW_CONTROL_TYPE_ID
    monkeypatch.setattr(uielement.user32, "IsWindow", lambda _hwnd: False)
    monkeypatch.setattr(uielement, "_com_call",
                        lambda *_args: pytest.fail("UIA read after HWND died"))

    assert element.is_stale()


def test_a_recycled_window_handle_does_not_revive_a_degraded_element(monkeypatch):
    import keyhac.platform.win.uielement as uielement

    element = _detached_element()
    element._native_hwnd = 0x1234
    element._window_control_type_id = uielement.WINDOW_CONTROL_TYPE_ID
    monkeypatch.setattr(uielement.user32, "IsWindow", lambda _hwnd: True)
    _report_control_type(monkeypatch, 50033)  # stale UIA Window now reports Pane

    assert element.is_stale()


def test_a_live_window_handle_with_its_original_role_is_not_stale(monkeypatch):
    import keyhac.platform.win.uielement as uielement

    element = _detached_element()
    element._native_hwnd = 0x1234
    element._window_control_type_id = uielement.WINDOW_CONTROL_TYPE_ID
    monkeypatch.setattr(uielement.user32, "IsWindow", lambda _hwnd: True)
    _report_control_type(monkeypatch, uielement.WINDOW_CONTROL_TYPE_ID)

    assert not element.is_stale()


def test_non_window_elements_keep_the_existing_uia_staleness_check(monkeypatch):
    import keyhac.platform.win.uielement as uielement

    element = _detached_element()
    monkeypatch.setattr(uielement.user32, "IsWindow",
                        lambda _hwnd: pytest.fail("not a tracked window"))
    _report_control_type(monkeypatch, 50000)  # Button

    assert not element.is_stale()


def test_there_is_no_application_menu_bar_to_offer():
    """Windows has no menu bar in the sense the question asks about, and
    answering None is what carries that decision to MenuItemsSource.

    On macOS a menu bar is an OS-level part: one per application, always at
    the top of the screen, readable in full while closed. A Windows menu
    belongs to a window, may not be there at all, and fills only when it
    opens - so a "every command in the menus" list cannot be produced without
    opening every menu in turn. The top-level items are UI elements of the
    window instead, and the controls walk lists them (measured on a tk window:
    File, Edit, Shell, Debug, Options, Window, Help, all role MenuItem).
    """
    assert _detached_element().menu_bar() is None


def test_a_source_asking_for_one_gets_an_empty_list_not_an_error():
    """The whole point of answering None: MenuItemsSource in a shared config
    is a no-op here, not a crash and not a wrong list."""
    import keyhac.core.sources as src

    class _Win:
        element = _detached_element()

    class _Keymap:
        get_active_window = staticmethod(_Win)

    original = src.Keymap.get_instance
    src.Keymap.get_instance = staticmethod(lambda: _Keymap())
    try:
        assert list(src.MenuItemsSource().candidates()) == []
    finally:
        src.Keymap.get_instance = original


def test_a_worker_walking_the_tree_gets_the_mta():
    """The apartment a background walk runs in is chosen, not inherited.

    `get_automation()` initialises whichever thread calls it first as an STA,
    and until `com_worker_thread` existed no worker called `CoInitializeEx` at
    all - it landed in the implicit MTA that Windows 8 and later hand an
    uninitialised thread, which happens to be the apartment UI Automation
    clients want. This pins the intent rather than the accident: an explicit
    MTA, so that a worker which reached `get_automation()` before the main
    thread could not bind the process-wide automation object to an apartment
    that dies with it.
    """
    import ctypes
    import threading

    from keyhac.platform.win.uielement import com_worker_thread

    ole32 = ctypes.WinDLL("ole32")
    ole32.CoGetApartmentType.argtypes = [ctypes.POINTER(ctypes.c_int),
                                         ctypes.POINTER(ctypes.c_int)]
    ole32.CoGetApartmentType.restype = ctypes.c_long
    APTTYPE_MTA, APTTYPEQUALIFIER_NONE = 1, 0

    seen = {}

    def worker():
        with com_worker_thread():
            kind, qualifier = ctypes.c_int(), ctypes.c_int()
            seen["hr"] = ole32.CoGetApartmentType(ctypes.byref(kind),
                                                  ctypes.byref(qualifier))
            seen["apartment"] = (kind.value, qualifier.value)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert seen["hr"] == 0
    # MTA by our own CoInitializeEx - not APTTYPEQUALIFIER_IMPLICIT_MTA, which
    # is what an uninitialised thread would have reported.
    assert seen["apartment"] == (APTTYPE_MTA, APTTYPEQUALIFIER_NONE)


def test_a_newline_is_not_where_the_caret_is():
    r"""Measured in the Claude Code chat input inside VS Code, empty, caret at
    the start of it: the degenerate selection has no rectangles, expanding it
    to a character gives '\n', and that newline's rectangle is
    (3316, 1803, 32, 32) - the end of the line *box*, five hundred points
    right of the caret, in a field whose own line runs (2156, 1805, 276, 32).

    macOS meets the same character from the other side, asking for the one
    *before* the caret; here the expansion runs forward, and a newline means
    only that there is nothing at the caret to bound.
    """
    from keyhac.platform.win.uielement import _is_a_place_in_a_line

    assert not _is_a_place_in_a_line("\n")
    assert not _is_a_place_in_a_line("\r\n")
    assert not _is_a_place_in_a_line("")
    assert not _is_a_place_in_a_line(None)      # an empty range, or a failure
    assert _is_a_place_in_a_line("a")
    assert _is_a_place_in_a_line(" "), "a space is drawn where it is"
    assert _is_a_place_in_a_line("あ")


def test_a_range_s_text_is_shown_as_itself():
    """Quoted, so a newline in the report is visible as one, and cut short -
    a line of source is not a diagnosis."""
    from keyhac.platform.win.uielement import _shown

    assert _shown("\n") == repr("\n")
    assert _shown(None).startswith("nothing")
    assert _shown("x" * 60).endswith("...'")


def test_get_text_prefers_the_document_range_over_a_truncated_value(monkeypatch):
    """Issue #166: Chromium truncates ValuePattern.Value at 10,000 characters
    while the Text pattern's document range holds the whole text - so the
    document range is read first."""
    element = _detached_element()
    whole = "x" * 45_000
    monkeypatch.setattr(element, "_document_text", lambda: whole)
    monkeypatch.setattr(element, "get_attribute_value",
                        lambda name: whole[:10_000])
    assert element.get_text() == whole


def test_get_text_falls_back_to_value_without_a_document(monkeypatch):
    """A combo box has no Text pattern; a provider may leave it blank."""
    element = _detached_element()
    monkeypatch.setattr(element, "get_attribute_value", lambda name: "Tokyo")
    for document in (None, ""):
        monkeypatch.setattr(element, "_document_text", lambda: document)
        assert element.get_text() == "Tokyo"


def _edit_with_value(monkeypatch, value, window_text):
    element = _detached_element()
    monkeypatch.setattr(element, "_document_text", lambda: None)
    monkeypatch.setattr(element, "get_attribute_value", lambda name: value)
    asked = []
    monkeypatch.setattr(element, "_window_text",
                        lambda: asked.append(True) or window_text)
    return element, asked


def test_get_text_asks_the_window_when_value_hits_the_msaa_limit(monkeypatch):
    """Issue #169: a native EDIT's Value is cut at 4,096 by the MSAA proxy
    and there is no Text pattern; WM_GETTEXT has the whole text."""
    from keyhac.platform.win.uielement import MSAA_VALUE_CUT

    whole = "line\r\n" * 5600                       # 33,600 characters
    element, asked = _edit_with_value(
        monkeypatch, whole[:MSAA_VALUE_CUT], whole)
    assert element.get_text() == whole and asked


def test_a_richedit_cut_one_shorter_also_asks_the_window(monkeypatch):
    """PR #170 Windows pass: RichEdit's Value is cut at 4,095, not 4,096."""
    whole = "rich line\r\n" * 1200
    element, asked = _edit_with_value(monkeypatch, whole[:4095], whole)
    assert element.get_text() == whole and asked


def test_window_text_never_reads_a_password_field(monkeypatch):
    """PR #170 Windows pass: WM_GETTEXT handed a WinForms password TextBox's
    plaintext to another process. Refused before any message is sent."""
    import keyhac.platform.win.uielement as uielement

    element = _detached_element()
    attributes = {"NativeWindowHandle": 0x1234, "IsPassword": True,
                  "ClassName": "WindowsForms10.EDIT.app.0.2bf8098_r6_ad1"}
    monkeypatch.setattr(element, "get_attribute_value", attributes.get)
    sent = []
    monkeypatch.setattr(uielement.user32, "SendMessageTimeoutW",
                        lambda *args: sent.append(args) or 1)
    assert element._window_text() is None and not sent
    # And by the window style alone, when UIA does not say.
    attributes["IsPassword"] = False
    monkeypatch.setattr(uielement.user32, "GetWindowLongW",
                        lambda hwnd, index: uielement.ES_PASSWORD)
    assert element._window_text() is None and not sent


def test_get_text_below_the_limit_never_asks_the_window(monkeypatch):
    """Every shorter read - and its line endings - stays what it was."""
    element, asked = _edit_with_value(monkeypatch, "rich one\rrich two",
                                      "rich one\r\nrich two")
    assert element.get_text() == "rich one\rrich two" and not asked


def test_get_text_keeps_the_value_when_the_window_has_no_more(monkeypatch):
    """A password EDIT answers WM_GETTEXT from outside with nothing."""
    from keyhac.platform.win.uielement import MSAA_VALUE_CUT

    value = "x" * MSAA_VALUE_CUT
    for answer in (None, "", value):
        element, _asked = _edit_with_value(monkeypatch, value, answer)
        assert element.get_text() == value


def test_window_text_is_only_asked_of_edit_controls(monkeypatch):
    element = _detached_element()
    attributes = {"NativeWindowHandle": 0x1234, "ClassName": "Button"}
    monkeypatch.setattr(element, "get_attribute_value", attributes.get)
    assert element._window_text() is None
    attributes["NativeWindowHandle"] = 0
    attributes["ClassName"] = "WindowsForms10.EDIT.app.0.2bf8098_r6_ad1"
    assert element._window_text() is None, "no window, nothing to ask"


def test_the_shared_client_carries_the_call_timeouts():
    """Issue #171: a UIA call into a hung application waited for as long as
    it hung - 23 s measured - on the thread that services the keyboard hook.
    Read back through the get_ slots (60, 62), which also pins the put_
    slots (61, 63) one past them: a wrong slot would not read back 250."""
    import ctypes

    from keyhac.platform.win import uielement

    automation = uielement.get_automation()
    assert automation is not None
    for slot, expected in ((60, uielement.UIA_CONNECTION_TIMEOUT_MS),
                           (62, uielement.UIA_TRANSACTION_TIMEOUT_MS)):
        value = ctypes.c_ulong()
        hr = uielement._com_call(automation, slot, ctypes.c_long,
                                 [ctypes.POINTER(ctypes.c_ulong)],
                                 ctypes.byref(value))
        assert hr == 0 and value.value == expected


def test_a_timed_out_call_raises_unresponsive(monkeypatch):
    """Issue #171: a call the UIA timeout cut short raises, ending the read
    it interrupts - a walk or a text read into a hung application pays the
    limit once, not once per element it had left."""
    import keyhac.platform.win.uielement as uielement
    from keyhac.core.uitree import Unresponsive

    monkeypatch.setattr(uielement, "_raw_com_call",
                        lambda *args: uielement.UIA_E_TIMEOUT - (1 << 32))
    with pytest.raises(Unresponsive, match="did not answer"):
        uielement._com_call(object(), 23, None, [])


def test_one_timeout_per_key_event(monkeypatch):
    """Inside a key event, the first timeout spends the event's budget and
    every later call raises without being made: the focus read and a bound
    function together wait for one time limit, not two."""
    import keyhac.platform.win.uielement as uielement
    from keyhac.core.uitree import Unresponsive, one_timeout_per_key_event

    made = []

    def raw(*args):
        made.append(args[1])
        return uielement.UIA_E_TIMEOUT - (1 << 32)

    monkeypatch.setattr(uielement, "_raw_com_call", raw)
    with one_timeout_per_key_event():
        for slot in (23, 24):
            with pytest.raises(Unresponsive):
                uielement._com_call(object(), slot, None, [])
    assert made == [23], "the second was refused without being made"
    # Outside a key event (an action's worker) each call is made.
    with pytest.raises(Unresponsive):
        uielement._com_call(object(), 25, None, [])
    assert made == [23, 25]


def test_release_runs_whatever_the_budget_says(monkeypatch):
    import keyhac.platform.win.uielement as uielement
    from keyhac.core.uitree import _spend_budget, one_timeout_per_key_event

    released = []
    monkeypatch.setattr(uielement, "_raw_com_call",
                        lambda *args: released.append(args[1]) or 0)
    with one_timeout_per_key_event():
        _spend_budget()
        uielement._release(object())
    assert released == [2]


def test_the_focus_path_falls_back_at_the_first_timeout(monkeypatch):
    """The focus walk runs inside the keyboard hook: GetFocusedElement
    answered, then the application stopped - the walk ends there and the
    window path is reported, never a half-read chain."""
    import keyhac.platform.win.uielement as uielement
    from keyhac.core.uitree import Unresponsive
    from keyhac.platform.win.focus import WinFocusProvider

    reads = []

    class Hung:
        def get_attribute_value(self, name):
            reads.append(name)
            raise Unresponsive("hung")

        def parent(self):
            reads.append("parent")
            return Hung()

    provider = WinFocusProvider.__new__(WinFocusProvider)
    path, element = provider._build_path("notepad", "Untitled", "Notepad",
                                         Hung(), False)
    assert path == "/Application(notepad)/Notepad(Untitled)"
    assert element is None
    assert reads == ["ControlType"], "stopped at the first"
    # Marked, so get_focus does not cache it: the next key asks again and an
    # application that has recovered gets its full path back (#175).
    assert provider._path_cut_short is True


def test_a_full_focus_path_is_not_marked_cut_short(monkeypatch):
    import keyhac.platform.win.uielement as uielement
    from keyhac.platform.win.focus import WinFocusProvider

    class Edit:
        def get_attribute_value(self, name):
            return {"ControlType": "Edit", "Name": "Body"}.get(name)

        def parent(self):
            return None

    provider = WinFocusProvider.__new__(WinFocusProvider)
    provider._path_cut_short = True             # left over from a hang
    path, element = provider._build_path("notepad", "Untitled", "Notepad",
                                         Edit(), False)
    assert path.endswith("/Edit(Body)") and element is not None
    assert provider._path_cut_short is False


def test_get_text_pays_one_timeout_not_two(monkeypatch):
    """PR #172 measured get_text at 508 ms on a hung window: the Text
    pattern, then Value, each timing out. The first now ends it."""
    import keyhac.platform.win.uielement as uielement
    from keyhac.core.uitree import Unresponsive

    element = _detached_element()
    asked = []

    def document():
        raise Unresponsive("hung")

    monkeypatch.setattr(element, "_document_text", document)
    monkeypatch.setattr(uielement.UIElement, "get_attribute_value",
                        lambda self, name: asked.append(name))
    with pytest.raises(Unresponsive):
        element.get_text()
    assert asked == []



def test_a_worker_thread_lands_in_the_mta():
    """PR #174's Windows pass: run() threads reported CO_E_NOTINITIALIZED -
    no apartment at all - since nothing kept an MTA alive. get_automation()
    now does (CoIncrementMTAUsage), so an uninitialised worker is in the
    implicit MTA the chooser's walks were measured in."""
    import ctypes
    import threading

    from keyhac.platform.win import uielement

    assert uielement.get_automation() is not None
    ole32 = ctypes.WinDLL("ole32")
    ole32.CoGetApartmentType.argtypes = [ctypes.POINTER(ctypes.c_int),
                                         ctypes.POINTER(ctypes.c_int)]
    ole32.CoGetApartmentType.restype = ctypes.c_long
    seen = {}

    def worker():
        kind, qualifier = ctypes.c_int(), ctypes.c_int()
        seen["hr"] = ole32.CoGetApartmentType(ctypes.byref(kind),
                                              ctypes.byref(qualifier))
        seen["kind"], seen["qualifier"] = kind.value, qualifier.value

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=5)
    APTTYPE_MTA, APTTYPEQUALIFIER_IMPLICIT_MTA = 1, 1
    assert seen == {"hr": 0, "kind": APTTYPE_MTA,
                    "qualifier": APTTYPEQUALIFIER_IMPLICIT_MTA}


def test_a_cut_short_focus_is_reused_for_a_second_only(monkeypatch):
    """PR #176's Windows pass: not caching a cut-short focus at all made every
    key down and up pay a timeout while the window stayed hung (keys every
    150 ms handled every ~680 ms, system-wide). It is reused for
    CUT_SHORT_FOCUS_RETRY, then asked again so a recovery is seen in time."""
    import keyhac.platform.win.focus as wf

    class Native:
        def __init__(self, hwnd):
            self.hwnd = hwnd

        def get_text(self):
            return "Hung"

        def get_process_name(self):
            return "hung.exe"

        def get_class_name(self):
            return "Edit"

        def get_pid(self):
            return 4242

    monkeypatch.setattr(wf.user32, "GetForegroundWindow", lambda: 1)
    monkeypatch.setattr(wf.user32, "GetWindowThreadProcessId", lambda *a: 7)
    monkeypatch.setattr(wf.user32, "GetGUIThreadInfo", lambda *a: 0)
    monkeypatch.setattr(wf, "NativeWindow", Native)
    clock = [100.0]
    monkeypatch.setattr(wf.time, "monotonic", lambda: clock[0])
    builds = []

    def build(self, app_name, title, class_name, element, cut_short):
        builds.append(1)
        self._path_cut_short = True
        return "/Application(hung)/Edit(Hung)", None

    monkeypatch.setattr(wf.WinFocusProvider, "_build_path", build)
    monkeypatch.setattr(wf.WinFocusProvider, "_read_focused_element",
                        staticmethod(lambda: (None, False)))
    provider = wf.WinFocusProvider()
    first = provider.get_focus()
    clock[0] += 0.5
    assert provider.get_focus() is first and builds == [1], "reused"
    clock[0] += 1.0
    provider.get_focus()
    assert builds == [1, 1], "asked again after the second"


def _scripted_focus_provider(monkeypatch, pid=4242):
    """A WinFocusProvider over one unchanging window, whose focused-element
    read answers from `reads` - the list the test pushes onto."""
    import keyhac.platform.win.focus as wf

    class Native:
        def __init__(self, hwnd):
            self.hwnd = hwnd

        def get_text(self):
            return "Page"

        def get_process_name(self):
            return "msedge.exe"

        def get_class_name(self):
            return "Chrome_WidgetWin_1"

        def get_pid(self):
            return pid

    class Element:
        def __init__(self, name):
            self.name = name
            self._ptr = name

        def get_attribute_value(self, attribute):
            return {"ControlType": "Edit", "Name": self.name}.get(attribute)

        def parent(self):
            return None

    monkeypatch.setattr(wf.user32, "GetForegroundWindow", lambda: 1)
    monkeypatch.setattr(wf.user32, "GetWindowThreadProcessId", lambda *a: 7)
    monkeypatch.setattr(wf.user32, "GetGUIThreadInfo", lambda *a: 0)
    monkeypatch.setattr(wf, "NativeWindow", Native)
    import keyhac.platform.win.uielement as uielement
    monkeypatch.setattr(uielement, "_same_element", lambda a, b: a == b)
    reads = []
    asked = []

    def read():
        asked.append(1)
        return reads[-1]

    monkeypatch.setattr(wf.WinFocusProvider, "_read_focused_element",
                        staticmethod(read))
    return wf.WinFocusProvider(), reads, asked, Element


def test_focus_moving_inside_one_window_changes_the_path(monkeypatch):
    """Issue #150: one HWND, one title, and the focus moves from one page
    field to another - the case the Win32 probe alone cannot see."""
    provider, reads, asked, Element = _scripted_focus_provider(monkeypatch)
    reads.append((Element("first"), False))
    first = provider.get_focus()
    assert first.path.endswith("/Edit(first)")
    assert provider.get_focus() is first, "same element: cached"
    reads.append((Element("second"), False))
    second = provider.get_focus()
    assert second is not first and second.path.endswith("/Edit(second)")
    assert len(asked) == 3, "the element is read on every key event"


def test_an_uncomparable_element_is_a_change(monkeypatch):
    # CompareElements answering "cannot say" - a cached element whose
    # process exited - rebuilds rather than reusing.
    import keyhac.platform.win.uielement as uielement
    provider, reads, asked, Element = _scripted_focus_provider(monkeypatch)
    reads.append((Element("first"), False))
    first = provider.get_focus()
    monkeypatch.setattr(uielement, "_same_element", lambda a, b: None)
    assert provider.get_focus() is not first


def test_a_timed_out_focus_read_is_cut_short_and_not_asked_again(monkeypatch):
    """#175's rule, kept: the read that timed out is served by the
    application in front, so it is not repeated on the next key event."""
    provider, reads, asked, Element = _scripted_focus_provider(monkeypatch)
    reads.append((None, True))
    cut_short = provider.get_focus()
    assert cut_short.path == "/Application(msedge)/Chrome_WidgetWin_1(Page)"
    assert provider.get_focus() is cut_short
    assert len(asked) == 1


def test_keyhacs_own_windows_are_not_asked_on_every_key(monkeypatch):
    import os
    provider, reads, asked, Element = _scripted_focus_provider(
        monkeypatch, pid=os.getpid())
    reads.append((Element("console"), False))
    first = provider.get_focus()
    assert first.element is None, "#173: no element for our own windows"
    reads.append((Element("elsewhere"), False))
    assert provider.get_focus() is first
    assert len(asked) == 1

