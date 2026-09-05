"""Windows UI Automation element access - IUIAutomation via raw ctypes.

The Windows counterpart of keyhac/platform/mac/uielement.py.  macOS exposes a
semantic accessibility tree (AXUIElement); the equivalent on Windows is UI
Automation, *not* the HWND tree: in a UWP/WinUI, Electron or Chrome window the
entire UI lives in one HWND, so walking GetParent yields nothing while UIA
still describes the real control hierarchy.  (Window-level operations - find,
activate, move, restore - stay on HWNDs; see keyhac/platform/win/window.py.)

Attribute names are UIA's own, not AX's.  A portable façade over the two
vocabularies would have to invent a third one and lie about both, so configs
that reach into the element tree branch on keymap.platform and use the names
the OS actually uses.  Portable code uses Focus.app_name / window_title /
class_name and the focus path.

COM without comtypes: interface methods are called through their vtable slot
index (the same technique puikit's _win32_dragdrop.py uses to hand-build
IDropTarget).  The slot numbers below come from UIAutomationClient.h and are
pinned by tests that cross-check each accessor against the Win32 answer for
the same window - a wrong index silently calls a different method, so it must
be verified, not assumed.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
from typing import Any

from keyhac.core import log
from keyhac.core.uitree import _first_name

logger = log.getLogger("WinUIElement")

#: Sibling bound on UIElement.children() - see its docstring.
MAX_CHILDREN = 2000

#: How far up from the focused element contains_focus() looks.  Bounded so
#: that a miss costs a handful of cross-process reads rather than a walk to
#: the desktop; deep enough for a composite control, whose parts are one level
#: down (a ComboBox focuses its Edit child).
FOCUS_ANCESTOR_LIMIT = 4

if sys.platform == "win32":
    from ctypes import wintypes

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    oleaut32 = ctypes.WinDLL("oleaut32", use_last_error=True)

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_uint32),
                    ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8)]

        def __init__(self, text: str):
            super().__init__()
            ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(self))

    ole32.CLSIDFromString.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(GUID)]
    ole32.CLSIDFromString.restype = ctypes.c_long
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID), ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
    oleaut32.SysFreeString.restype = None
    oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
    oleaut32.SysAllocString.restype = ctypes.c_void_p
    # GetBoundingRectangles hands back a SAFEARRAY of doubles rather than a
    # plain out-parameter, which is why these three are here and nowhere else
    # in the module.
    oleaut32.SafeArrayGetLBound.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_long)]
    oleaut32.SafeArrayGetLBound.restype = ctypes.c_long
    oleaut32.SafeArrayGetUBound.argtypes = [
        ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_long)]
    oleaut32.SafeArrayGetUBound.restype = ctypes.c_long
    oleaut32.SafeArrayAccessData.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    oleaut32.SafeArrayAccessData.restype = ctypes.c_long
    oleaut32.SafeArrayUnaccessData.argtypes = [ctypes.c_void_p]
    oleaut32.SafeArrayUnaccessData.restype = ctypes.c_long
    oleaut32.SafeArrayDestroy.argtypes = [ctypes.c_void_p]
    oleaut32.SafeArrayDestroy.restype = ctypes.c_long

    # Not UIA at all: the display a rectangle landed on, for get_coordinate_scale.
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HANDLE
    MONITOR_DEFAULTTONEAREST = 0x2
    MDT_EFFECTIVE_DPI = 0
    try:
        shcore = ctypes.WinDLL("shcore", use_last_error=True)
        shcore.GetDpiForMonitor.argtypes = [
            wintypes.HANDLE, ctypes.c_int,
            ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT)]
        shcore.GetDpiForMonitor.restype = ctypes.c_long
    except (OSError, AttributeError):     # pre-8.1: one DPI for the desktop
        shcore = None

    COINIT_APARTMENTTHREADED = 0x2
    COINIT_MULTITHREADED = 0x0
    CLSCTX_INPROC_SERVER = 0x1
    S_OK = 0
    # The element has been destroyed since we got a pointer to it.  Distinct
    # from a property simply not being supported, which is what lets
    # is_stale() tell "the screen moved" from "this control does not answer
    # that".
    UIA_E_ELEMENTNOTAVAILABLE = 0x80040201
    S_FALSE = 1
    RPC_E_CHANGED_MODE = -2147417850  # 0x80010106

    CLSID_CUIAutomation = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
    IID_IUIAutomation = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"


# --------------------------------------------------------------------------
# vtable slot indices (UIAutomationClient.h).  0-2 are IUnknown.

class _IUIAutomation:
    CompareElements = 3
    ElementFromHandle = 6
    ElementFromPoint = 7
    GetFocusedElement = 8
    get_ControlViewWalker = 14
    get_RawViewWalker = 16


class _IUIAutomationElement:
    SetFocus = 3
    GetCurrentPattern = 16
    get_CurrentProcessId = 20
    get_CurrentControlType = 21
    get_CurrentLocalizedControlType = 22
    get_CurrentName = 23
    get_CurrentHasKeyboardFocus = 26
    get_CurrentAutomationId = 29
    get_CurrentClassName = 30
    get_CurrentHelpText = 31
    get_CurrentNativeWindowHandle = 36
    get_CurrentIsOffscreen = 38
    get_CurrentFrameworkId = 40
    get_CurrentBoundingRectangle = 43


class _IUIAutomationTreeWalker:
    GetParentElement = 3
    GetFirstChildElement = 4
    GetNextSiblingElement = 6


# --------------------------------------------------------------------------
# Control patterns.  UIA splits behavior into patterns an element may support;
# they are the counterpart of AX actions and of AX value attributes at once.
# GetCurrentPattern(id) returns the pattern interface, or NULL when the
# element does not support it - which is also how support is tested.

class _PatternId:
    Invoke = 10000
    Selection = 10001
    Value = 10002
    RangeValue = 10003
    Scroll = 10004
    ExpandCollapse = 10005
    Grid = 10006
    SelectionItem = 10010
    Toggle = 10015
    Text = 10014
    Window = 10009


class _IUIAutomationInvokePattern:
    Invoke = 3


class _IUIAutomationTogglePattern:
    Toggle = 3
    get_CurrentToggleState = 4


class _IUIAutomationExpandCollapsePattern:
    Expand = 3
    Collapse = 4


class _IUIAutomationValuePattern:
    SetValue = 3
    get_CurrentValue = 4
    get_CurrentIsReadOnly = 5


class _IUIAutomationSelectionItemPattern:
    # How a tab, a list item or a radio button reports and changes which one of
    # a set is current.  There is no other way to do either: a Win32 TabItem
    # supports no Invoke, no Toggle and no Expand - get_action_names() on one
    # returns [] without this pattern - and its selected-ness is not a value,
    # so reading `.value` gives None however the tab is set.
    Select = 3
    AddToSelection = 4
    RemoveFromSelection = 5
    get_CurrentIsSelected = 6
    get_CurrentSelectionContainer = 7


class _IUIAutomationTextPattern:
    GetSelection = 5
    get_DocumentRange = 7


class _IUIAutomationTextRangeArray:
    get_Length = 3
    GetElement = 4


class _IUIAutomationTextRange:
    # 11 is GetEnclosingElement, whose out-param is an element pointer - calling
    # it as GetText(int, BSTR*) wrote through a bogus address and access-
    # violated. Verified live, not read off a header.
    ExpandToEnclosingUnit = 6    # UNVERIFIED on hardware - see below
    #: UNVERIFIED on hardware. Sits between the two slots that *are* pinned:
    #: ExpandToEnclosingUnit at 6 and GetEnclosingElement at 11, with
    #: FindAttribute, FindText and GetAttributeValue filling 7-9. A wrong
    #: index here calls a different method with a SAFEARRAY out-parameter's
    #: address, so `get_caret_rect()` treats every failure as "no caret".
    GetBoundingRectangles = 10
    GetText = 12


#: TextUnit, for ExpandToEnclosingUnit.
class _TextUnit:
    Character = 0
    Word = 2
    Line = 3
    Document = 6


#: Action name -> (pattern id, vtable slot).  Named after the UIA patterns
#: rather than AX's "AXPress" etc., for the same reason attributes are (see the
#: module docstring).
_ACTIONS = {
    "Invoke": (_PatternId.Invoke, _IUIAutomationInvokePattern.Invoke),
    "Toggle": (_PatternId.Toggle, _IUIAutomationTogglePattern.Toggle),
    "Expand": (_PatternId.ExpandCollapse, _IUIAutomationExpandCollapsePattern.Expand),
    "Collapse": (_PatternId.ExpandCollapse, _IUIAutomationExpandCollapsePattern.Collapse),
    "Select": (_PatternId.SelectionItem, _IUIAutomationSelectionItemPattern.Select),
}


#: UIA_ControlTypeIds -> the short role name used in focus paths.  Chosen to
#: read like the AX roles on macOS (which drop their "AX" prefix badly), so a
#: path is scannable: /Window(Untitled - Notepad)/Document().
CONTROL_TYPE_NAMES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar", 50018: "Tab",
    50019: "TabItem", 50020: "Text", 50021: "ToolBar", 50022: "ToolTip",
    50023: "Tree", 50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem", 50030: "Document",
    50031: "SplitButton", 50032: "Window", 50033: "Pane", 50034: "Header",
    50035: "HeaderItem", 50036: "Table", 50037: "TitleBar", 50038: "Separator",
    50039: "SemanticZoom", 50040: "AppBar",
}


def _com_call(ptr, index, restype, argtypes, *args):
    """Invoke vtable slot `index` on the COM interface pointer `ptr`."""
    vtable = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return prototype(vtable[index])(ptr, *args)


def _release(ptr) -> None:
    if ptr:
        _com_call(ptr, 2, ctypes.c_ulong, [])  # IUnknown::Release


def _same_element(first, second):
    """Whether two element pointers name the same element.

    None when UI Automation could not say, which callers must distinguish
    from False: two pointers to the same element are not pointer-equal, so a
    failed comparison looks exactly like a difference.
    """
    automation = get_automation()
    if automation is None or not first or not second:
        return None
    same = ctypes.c_int()
    hr = _com_call(automation, _IUIAutomation.CompareElements, ctypes.c_long,
                   [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)],
                   ctypes.c_void_p(first.value), ctypes.c_void_p(second.value),
                   ctypes.byref(same))
    return bool(same.value) if hr == S_OK else None


def _bstr_out(ptr, index) -> str | None:
    """Call a `HRESULT get_X(BSTR* out)` slot and return the string."""
    out = ctypes.c_void_p()
    hr = _com_call(ptr, index, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)],
                   ctypes.byref(out))
    if hr != S_OK or not out.value:
        return None
    try:
        return ctypes.wstring_at(out.value)
    finally:
        oleaut32.SysFreeString(out)


def _int_out(ctype, ptr, index):
    """Call a `HRESULT get_X(T* out)` slot returning a scalar."""
    out = ctype()
    hr = _com_call(ptr, index, ctypes.c_long, [ctypes.POINTER(ctype)], ctypes.byref(out))
    if hr != S_OK:
        return None
    return out.value


def _element_out(ptr, index, *args):
    """Call a slot whose last parameter is an IUIAutomationElement** out."""
    out = ctypes.c_void_p()
    argtypes = [type(a) for a in args] + [ctypes.POINTER(ctypes.c_void_p)]
    hr = _com_call(ptr, index, ctypes.c_long, argtypes, *args, ctypes.byref(out))
    if hr != S_OK or not out.value:
        return None
    return out


def _first_bounding_rect(text_range) -> tuple | None:
    """The first (x, y, w, h) of a text range's bounding rectangles.

    `GetBoundingRectangles` answers a SAFEARRAY of doubles - four per
    rectangle, one rectangle per line the range spans.  A caret spans one
    line, so the first four doubles are the whole answer.

    A collapsed range is allowed to answer with no rectangles at all, which
    is why a short array is "no caret" rather than an error worth logging.
    """
    array = ctypes.c_void_p()
    hr = _com_call(text_range, _IUIAutomationTextRange.GetBoundingRectangles,
                   ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)],
                   ctypes.byref(array))
    if hr != S_OK or not array.value:
        return None
    try:
        lower, upper = ctypes.c_long(), ctypes.c_long()
        if oleaut32.SafeArrayGetLBound(array, 1, ctypes.byref(lower)) != S_OK:
            return None
        if oleaut32.SafeArrayGetUBound(array, 1, ctypes.byref(upper)) != S_OK:
            return None
        if upper.value - lower.value + 1 < 4:
            return None
        data = ctypes.c_void_p()
        if oleaut32.SafeArrayAccessData(array, ctypes.byref(data)) != S_OK:
            return None
        try:
            values = ctypes.cast(data, ctypes.POINTER(ctypes.c_double))
            return (values[0], values[1], values[2], values[3])
        finally:
            oleaut32.SafeArrayUnaccessData(array)
    finally:
        oleaut32.SafeArrayDestroy(array)


def _range_text(text_range, max_length: int = -1) -> str | None:
    """The text of one IUIAutomationTextRange (-1 meaning no limit)."""
    out = ctypes.c_void_p()
    hr = _com_call(text_range, _IUIAutomationTextRange.GetText, ctypes.c_long,
                   [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                   ctypes.c_int(max_length), ctypes.byref(out))
    if hr != S_OK or not out.value:
        return None
    try:
        return ctypes.wstring_at(out.value)
    finally:
        oleaut32.SysFreeString(out)


def _is_a_place_in_a_line(text) -> bool:
    """Whether the character a caret was expanded to says where the caret is.

    **A newline is not drawn where the caret is.** Measured in the Claude
    Code chat input inside VS Code, empty, with the caret at the start of it:
    the degenerate selection has no rectangles at all, expanding it to a
    character gives `'\\n'`, and that newline's rectangle is
    `(3316, 1803, 32, 32)` - the end of the line *box*, which for a field
    1240 wide is the far right of it, while the caret's own line runs
    `(2156, 1805, 276, 32)`. A balloon opened five hundred points right of
    the caret.

    macOS reached the same rule from the other side: there the character
    *before* the caret is asked and a newline means the caret is at the start
    of the line below, which nothing in the AX vocabulary can express. Here
    the expansion runs forward instead, and a newline means only that there
    is no character at the caret to be bounded - so there is no caret to
    report, and the field the caret is in is a better answer than a rectangle
    at the end of a line box.

    An empty answer is refused for the same reason: the bounds of nothing are
    not a place.
    """
    return bool(text) and bool(text.strip("\r\n"))


def _shown(text) -> str:
    """A range's text as a report should read it.

    Quoted, so a space or a newline is visible as itself, and cut short - a
    line of source is not a diagnosis. `None` is the answer for an empty
    range *and* for a call that failed, `_range_text` having no way to tell
    them apart: GetText hands back an empty BSTR either way.
    """
    if text is None:
        return "nothing - an empty range, or the call failed"
    return repr(text if len(text) <= 40 else text[:40] + "...")


class _POINT(ctypes.Structure):
    """The POINT ElementFromPoint takes *by value*."""
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    """The rectangle IUIAutomationElement::get_CurrentBoundingRectangle fills.

    A Win32 RECT of LONGs, NOT the UiaRect of doubles - that one belongs to the
    *provider* side (IRawElementProviderFragment). Reading 32 bytes of doubles
    out of this 16-byte return yields NaNs, which is how the mistake shows.
    """
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


_automation = None
_automation_failed = False


def get_automation():
    """The process-wide IUIAutomation instance, created on first use.

    Returns None (once, with a logged warning) when UI Automation is
    unavailable, so every caller degrades instead of raising - focus queries
    run on the key-event path and must never break typing.
    """
    global _automation, _automation_failed
    if _automation is not None or _automation_failed:
        return _automation
    if sys.platform != "win32":
        _automation_failed = True
        return None
    try:
        # UIA needs COM on this thread. S_FALSE means already initialized;
        # RPC_E_CHANGED_MODE means someone chose MTA, which UIA also accepts.
        hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
        if hr not in (S_OK, S_FALSE, RPC_E_CHANGED_MODE):
            raise OSError(f"CoInitializeEx failed: 0x{hr & 0xFFFFFFFF:08x}")
        ptr = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(GUID(CLSID_CUIAutomation)), None, CLSCTX_INPROC_SERVER,
            ctypes.byref(GUID(IID_IUIAutomation)), ctypes.byref(ptr))
        if hr != S_OK or not ptr.value:
            raise OSError(f"CoCreateInstance(CUIAutomation) failed: 0x{hr & 0xFFFFFFFF:08x}")
        _automation = ptr
    except Exception as e:
        _automation_failed = True
        logger.warning(f"UI Automation unavailable ({e}); focus paths fall back "
                       f"to the window level and element access is disabled.")
        return None
    return _automation


@contextlib.contextmanager
def com_worker_thread():
    """Give a worker thread its own COM apartment for the duration of a walk.

    `CoInitializeEx` is per thread, and until this existed no worker ever
    called it.  That was not the failure it looks like: measured over 65,000
    UIA calls from uninitialised workers into six applications, none returned
    an error, because Windows 8 and later place a thread that touches COM
    without initialising it into the process-wide *implicit* MTA - the very
    apartment Microsoft recommends for UI Automation clients.  The walk was
    landing in the right place by accident.

    What it fixes is the accident's other half, which is reachable.  The
    process-wide automation object is created by whichever thread calls
    `get_automation()` first, and that call initialises *its* thread as an
    STA.  Should a worker ever get there first - nothing structurally stops
    it; only main() happening to touch UIA earlier - the object would be
    bound to the apartment of a thread that is about to exit, and the cached
    pointer would outlive it.  Measured: a worker that reaches
    `get_automation()` first becomes the process MAINSTA.  With the MTA
    claimed up front the same call returns RPC_E_CHANGED_MODE, which
    `get_automation()` already accepts, and the worker stays in the MTA.

    MTA rather than STA because an STA owes the apartment a message pump this
    thread will never run.
    """
    hr = ole32.CoInitializeEx(None, COINIT_MULTITHREADED)
    # RPC_E_CHANGED_MODE means this thread is already in an apartment of the
    # other kind and keeps it; there is then nothing of ours to undo.
    initialised = hr in (S_OK, S_FALSE)
    if hr not in (S_OK, S_FALSE, RPC_E_CHANGED_MODE):
        logger.warning(f"CoInitializeEx on the worker failed: "
                       f"0x{hr & 0xFFFFFFFF:08x}; the walk goes ahead anyway.")
    try:
        yield
    finally:
        if initialised:
            ole32.CoUninitialize()


class UIElement:
    """One UI Automation element.

    Mirrors the *shape* of keyhac/platform/mac/uielement.py's UIElement -
    named attribute reads plus a parent walk - with UIA's own attribute names
    (see the module docstring).  Instances own their COM reference and release
    it on collection.
    """

    #: Readable attributes -> (vtable slot, reader).  Kept as data so
    #: get_attribute_names() cannot drift from get_attribute_value().
    _SCALAR_ATTRS = {
        "ProcessId": (_IUIAutomationElement.get_CurrentProcessId, ctypes.c_int),
        "NativeWindowHandle": (_IUIAutomationElement.get_CurrentNativeWindowHandle, ctypes.c_void_p),
        "HasKeyboardFocus": (_IUIAutomationElement.get_CurrentHasKeyboardFocus, ctypes.c_int),
        "IsOffscreen": (_IUIAutomationElement.get_CurrentIsOffscreen, ctypes.c_int),
    }
    _STRING_ATTRS = {
        "Name": _IUIAutomationElement.get_CurrentName,
        "ClassName": _IUIAutomationElement.get_CurrentClassName,
        "AutomationId": _IUIAutomationElement.get_CurrentAutomationId,
        "HelpText": _IUIAutomationElement.get_CurrentHelpText,
        "LocalizedControlType": _IUIAutomationElement.get_CurrentLocalizedControlType,
        "FrameworkId": _IUIAutomationElement.get_CurrentFrameworkId,
    }
    #: Computed / non-uniform attributes.
    _DERIVED_ATTRS = ("ControlType", "ControlTypeId", "BoundingRectangle", "Parent")
    #: Pattern-backed attributes, listed only when the element supports the
    #: pattern - so get_attribute_names() answers "what can I read from *this*
    #: element", like AXUIElementCopyAttributeNames does on macOS.
    _PATTERN_ATTRS = {
        "Value": _PatternId.Value,
        "IsReadOnly": _PatternId.Value,
        "ToggleState": _PatternId.Toggle,
        "IsSelected": _PatternId.SelectionItem,
        "SelectedText": _PatternId.Text,
    }

    def __init__(self, ptr):
        self._ptr = ptr

    def __del__(self):
        try:
            _release(self._ptr)
        except Exception:
            pass
        self._ptr = None

    def __repr__(self):
        return f"UIElement({self.get_attribute_value('ControlType')}" \
               f"({self.get_attribute_value('Name') or ''}))"

    # -- attributes ---------------------------------------------------------

    def get_attribute_names(self) -> list[str]:
        names = [*self._STRING_ATTRS, *self._SCALAR_ATTRS, *self._DERIVED_ATTRS]
        names += [name for name, pattern_id in self._PATTERN_ATTRS.items()
                  if self._pattern(pattern_id) is not None]
        return sorted(names)

    def _pattern(self, pattern_id: int):
        """The pattern interface for `pattern_id`, or None when unsupported.

        The caller owns the returned pointer and must release it; every use
        below does so in a finally.
        """
        if not self._ptr:
            return None
        out = ctypes.c_void_p()
        hr = _com_call(self._ptr, _IUIAutomationElement.GetCurrentPattern,
                       ctypes.c_long,
                       [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)],
                       ctypes.c_int(pattern_id), ctypes.byref(out))
        if hr != S_OK or not out.value:
            return None
        return out

    def get_attribute_value(self, name: str) -> Any:
        if not self._ptr:
            return None
        if name in self._STRING_ATTRS:
            return _bstr_out(self._ptr, self._STRING_ATTRS[name])
        if name in self._SCALAR_ATTRS:
            slot, ctype = self._SCALAR_ATTRS[name]
            value = _int_out(ctype, self._ptr, slot)
            if name in ("HasKeyboardFocus", "IsOffscreen") and value is not None:
                return bool(value)
            return value
        if name == "ControlTypeId":
            return _int_out(ctypes.c_int, self._ptr,
                            _IUIAutomationElement.get_CurrentControlType)
        if name == "ControlType":
            type_id = _int_out(ctypes.c_int, self._ptr,
                               _IUIAutomationElement.get_CurrentControlType)
            return CONTROL_TYPE_NAMES.get(type_id, f"({type_id})" if type_id else None)
        if name == "BoundingRectangle":
            rect = _RECT()
            hr = _com_call(self._ptr, _IUIAutomationElement.get_CurrentBoundingRectangle,
                           ctypes.c_long, [ctypes.POINTER(_RECT)], ctypes.byref(rect))
            if hr != S_OK:
                return None
            # (x, y, w, h), matching the frame convention MoveWindow uses.
            return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
        if name == "Parent":
            return self.parent()
        if name in self._PATTERN_ATTRS:
            return self._pattern_attribute(name)
        return None

    def get_attribute_values(self, names: list[str]) -> dict:
        """Read several attributes in one call (the macOS twin's counterpart).

        macOS answers this in one round trip into the other application;
        UI Automation has no such call in the shape this API needs - its
        batching is a *cache request* built around a tree walk, not around
        one element - so here it is the loop the caller would have written,
        and costs exactly what the individual reads cost. The point is that
        callers get to be written once, against the shape that can be fast.

        Args:
            names: Attribute names, in this platform's vocabulary
                ("ControlType", "Name", ...).

        Returns:
            A dict from name to value, None for an attribute the element
            does not have.
        """
        return {name: self.get_attribute_value(name) for name in names}

    def _pattern_attribute(self, name: str):
        pattern = self._pattern(self._PATTERN_ATTRS[name])
        if pattern is None:
            return None
        try:
            if name == "Value":
                return _bstr_out(pattern, _IUIAutomationValuePattern.get_CurrentValue)
            if name == "IsReadOnly":
                value = _int_out(ctypes.c_int, pattern,
                                 _IUIAutomationValuePattern.get_CurrentIsReadOnly)
                return None if value is None else bool(value)
            if name == "ToggleState":
                # 0 off, 1 on, 2 indeterminate
                return _int_out(ctypes.c_int, pattern,
                                _IUIAutomationTogglePattern.get_CurrentToggleState)
            if name == "IsSelected":
                value = _int_out(
                    ctypes.c_int, pattern,
                    _IUIAutomationSelectionItemPattern.get_CurrentIsSelected)
                return None if value is None else bool(value)
            if name == "SelectedText":
                return self._selected_text(pattern)
        finally:
            _release(pattern)
        return None

    @staticmethod
    def _selected_text(text_pattern) -> str | None:
        """The selection as text, concatenated over the selected ranges.

        The Windows answer to keyhac-mac configs reading "AXSelectedText":
        TextPattern hands back an IUIAutomationTextRangeArray, each range
        yielding its own string. maxLength -1 means "no limit".
        """
        ranges = ctypes.c_void_p()
        hr = _com_call(text_pattern, _IUIAutomationTextPattern.GetSelection,
                       ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)],
                       ctypes.byref(ranges))
        if hr != S_OK or not ranges.value:
            return None
        try:
            count = _int_out(ctypes.c_int, ranges, _IUIAutomationTextRangeArray.get_Length)
            if not count:
                return ""
            parts = []
            for index in range(count):
                text_range = _element_out(ranges, _IUIAutomationTextRangeArray.GetElement,
                                          ctypes.c_int(index))
                if text_range is None:
                    continue
                try:
                    text = _range_text(text_range)
                    if text is not None:
                        parts.append(text)
                finally:
                    _release(text_range)
            return "".join(parts)
        finally:
            _release(ranges)

    # -- tree ---------------------------------------------------------------

    def parent(self) -> "UIElement | None":
        walker = _control_view_walker()
        if walker is None or not self._ptr:
            return None
        ptr = _element_out(walker, _IUIAutomationTreeWalker.GetParentElement,
                           ctypes.c_void_p(self._ptr.value))
        return UIElement(ptr) if ptr else None

    def children(self) -> list["UIElement"]:
        """The control-view child elements.

        The other half of the walk parent() has always done, and the piece the
        element API was missing: with only parent(), an action could act on the
        focused element and its ancestors but could never reach an element that
        was not focused.

        Bounded by MAX_CHILDREN because this returns a list before
        keyhac.core.uitree gets to apply its node budget, and a data grid can
        have tens of thousands of siblings.
        """
        walker = _control_view_walker()
        if walker is None or not self._ptr:
            return []
        out = []
        ptr = _element_out(walker, _IUIAutomationTreeWalker.GetFirstChildElement,
                           ctypes.c_void_p(self._ptr.value))
        while ptr and len(out) < MAX_CHILDREN:
            child = UIElement(ptr)
            out.append(child)
            ptr = _element_out(walker, _IUIAutomationTreeWalker.GetNextSiblingElement,
                               ctypes.c_void_p(ptr.value))
        return out

    def identity_key(self):
        """None - UI Automation's control view is a real tree.

        macOS returns a key here because its AX graph lists a table cell under
        both its row and its column; UIA has no such double-listing, so paying
        GetRuntimeId per node would buy nothing.
        """
        return None

    def role(self) -> str | None:
        """Just the role, in one property read - see the macOS twin.

        lazydocs: ignore
        """
        return self.get_attribute_value("ControlType")

    def menu_bar(self) -> "UIElement | None":
        """None: Windows has no menu bar in the sense this asks about.

        Not a gap - a decision. On macOS the menu bar is an *OS-level* part:
        one per application, always at the top of the screen, always there,
        and its whole tree readable while it is closed. That is what makes
        `MenuItemsSource` possible there - every command in the application,
        flattened, without opening anything.

        Windows has none of those properties. A menu belongs to a window, sits
        at the top of it or nowhere at all, and is *populated when it opens* -
        an unopened MenuItem reports no children, and reading the leaves would
        mean expanding each one, finding the popup (which is hosted outside
        the item), reading it and collapsing again: menus visibly flashing
        open, a cost per item, and a modal menu loop in the target application
        while it happens. So Keyhac does not offer a menu scope here. The
        menu's top-level items are UI elements of the window like any other,
        and `WindowControlsSource` already lists them (role MenuItem) -
        choosing one opens that menu, which is what clicking it does.

        Answering None rather than the bar itself is what carries that
        decision: `MenuItemsSource` asks the platform whether there is a menu
        bar, and on Windows the honest answer to *that question* is no.

        lazydocs: ignore
        """
        return None

    def describe(self) -> dict:
        """The portable projection consumed by keyhac.core.uitree.UINode."""
        # Name is the label; HelpText is the tooltip, which is where an
        # icon-only toolbar button often puts the only words it has.  Which
        # one answered travels as `name_source` - see the macOS twin.
        name, name_source = _first_name(
            ("label", self.get_attribute_value("Name")),
            ("help", self.get_attribute_value("HelpText")),
        )
        return {
            "role": self.get_attribute_value("ControlType"),
            "name": name,
            "name_source": name_source,
            "value": self.get_attribute_value("Value"),
            "identifier": self.get_attribute_value("AutomationId"),
            "rect": self.get_attribute_value("BoundingRectangle"),
        }

    # -- actions ------------------------------------------------------------

    def get_action_names(self) -> list[str]:
        """Actions this element actually supports (mirrors macOS's
        AXUIElementCopyActionNames)."""
        return sorted(name for name, (pattern_id, _slot) in _ACTIONS.items()
                      if self._pattern(pattern_id) is not None)

    def perform_action(self, name: str) -> bool:
        """Run one of get_action_names(). Returns False (and logs) when the
        element does not support it, rather than raising on the key path."""
        entry = _ACTIONS.get(name)
        if entry is None:
            logger.warning(f"Unknown UI Automation action: {name!r} "
                           f"(available: {', '.join(sorted(_ACTIONS))})")
            return False
        pattern_id, slot = entry
        pattern = self._pattern(pattern_id)
        if pattern is None:
            return False
        try:
            return _com_call(pattern, slot, ctypes.c_long, []) == S_OK
        finally:
            _release(pattern)

    def is_stale(self) -> bool:
        """True when the element this pointer refers to no longer exists.

        The cheapest read there is - a scalar property off the element itself,
        no pattern to acquire. ControlType specifically, because every UIA
        element is required to have one: a failure reading *this* property is
        the element not answering, never "that property is unsupported here",
        which is the distinction the check needs and the reason it is not any
        other property.

        A fact, not a policy - `keyhac.core.uitree.StaleElement` is raised by
        the layer that decides what to do about it.

        Any failure HRESULT, not one named constant, and that is measured
        rather than assumed (`tools/uia_pass.py`, staleness section). A control
        destroyed underneath us returns **E_UNEXPECTED (0x8000FFFF) for its
        first ~90 ms** and only then settles on UIA_E_ELEMENTNOTAVAILABLE
        (0x80040201), stably and in both sampling orders. Matching only the
        named constant therefore answered False during exactly the window that
        matters - the moment just after a dialog closed - and
        `keyhac.core.fill._press` reported "element supports no press action"
        for a button that had simply gone away, which is the misdiagnosis this
        method exists to prevent.

        The trade the widened check makes: a live element whose provider fails
        this read for some transient reason is now called stale. That surfaces
        as `StaleElement`, whose documented remedy is to re-find and carry on -
        a better answer to be wrong with than blaming the operator's selector.

        LIMITATION: a destroyed *top-level window* is not reliably detected.
        UIA commonly keeps answering S_OK for one, degrading its ControlType
        from Window (50032) to Pane (50033) rather than failing - seen to
        persist for a full 10 s with the handle confirmed unrecycled, though
        not on every run. So this answers "this control is gone" dependably and
        "this window is gone" only sometimes; a caller holding a window-level
        element (`from_hwnd`, `element_at_point`) must not rely on it.
        """
        control_type = ctypes.c_int()
        hr = _com_call(self._ptr, _IUIAutomationElement.get_CurrentControlType,
                       ctypes.c_long, [ctypes.POINTER(ctypes.c_int)],
                       ctypes.byref(control_type))
        return (hr & 0xFFFFFFFF) != S_OK

    def set_value(self, value: str) -> bool:
        """Write through the Value pattern (the editable-control setter)."""
        pattern = self._pattern(_PatternId.Value)
        if pattern is None:
            return False
        try:
            bstr = oleaut32.SysAllocString(ctypes.c_wchar_p(value))
            try:
                return _com_call(pattern, _IUIAutomationValuePattern.SetValue,
                                 ctypes.c_long, [ctypes.c_void_p],
                                 ctypes.c_void_p(bstr)) == S_OK
            finally:
                oleaut32.SysFreeString(ctypes.c_void_p(bstr))
        finally:
            _release(pattern)

    # -- text layer ---------------------------------------------------------
    #
    # The counterpart of the macOS accessors, for the same reason: the control
    # tree does not reach into text.  A terminal or an editor is one element
    # holding an undifferentiated blob, so "the error line" is unreachable by
    # traversal and has to be asked for as text (doc/dev/ai-integration.md §6).
    #
    # STATUS: written against UIAutomationClient.h and cross-checked for
    # internal consistency with the slots already verified live in this file
    # (GetEnclosingElement=11, GetText=12 pin the IUIAutomationTextRange
    # ordering that puts ExpandToEnclosingUnit at 6).  Not yet run on Windows -
    # a wrong slot calls a different method silently, so treat these three as
    # unverified until doc/dev/testing.md records otherwise.

    def get_selection(self) -> str | None:
        """The selected text inside this element, or None."""
        return self.get_attribute_value("SelectedText")

    def get_text(self) -> str | None:
        """This element's whole text content.

        Value pattern first, which covers fields and combo boxes, then the
        Text pattern's document range, which is what a terminal, a document
        view or an editor buffer offers instead.
        """
        value = self.get_attribute_value("Value")
        if isinstance(value, str) and value:
            return value
        pattern = self._pattern(_PatternId.Text)
        if pattern is None:
            return value
        try:
            text_range = _element_out(pattern,
                                      _IUIAutomationTextPattern.get_DocumentRange)
            if text_range is None:
                return value
            try:
                return _range_text(text_range)
            finally:
                _release(text_range)
        finally:
            _release(pattern)

    def get_rect(self) -> tuple | None:
        """This element's screen rectangle as (x, y, w, h), or None.

        The single property `describe()` reads for the same thing, for the
        callers that want a place and nothing else - placing a popup beside
        the focused control, checking that a caret rectangle is not a lie.
        """
        rect = self.get_attribute_value("BoundingRectangle")
        return tuple(rect) if isinstance(rect, (tuple, list)) and len(rect) == 4 else None

    def get_coordinate_scale(self) -> float:
        """Physical pixels per logical unit on the display this element is on.

        The counterpart of the macOS method that answers 1.0 and means it.
        Keyhac makes itself per-monitor DPI aware before any window exists, so
        UIA hands back physical pixels: on a 200% display every rectangle
        arrives twice the size it is described at, and a rule written in text
        lines - `keyhac.core.anchor`'s "a field is at most three of them" -
        has to be told which pixels those are.

        The monitor is asked for rather than assumed: two displays at
        different scales are ordinary, and the answer that matters is the
        scale where the *element* is, not where Keyhac's own window sits.
        """
        rect = self.get_rect()
        if rect is None:
            return 1.0
        if shcore is not None:
            point = wintypes.POINT(int(rect[0] + rect[2] / 2),
                                   int(rect[1] + rect[3] / 2))
            monitor = user32.MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST)
            dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
            if monitor and shcore.GetDpiForMonitor(
                    monitor, MDT_EFFECTIVE_DPI,
                    ctypes.byref(dpi_x), ctypes.byref(dpi_y)) == S_OK and dpi_x.value:
                return dpi_x.value / 96.0
        return 1.0

    def get_caret_rect(self, trace: list | None = None) -> tuple | None:
        """The text insertion point's screen rectangle, or None.

        UIA has no caret call.  The selection with nothing selected *is* the
        caret - a degenerate range - and `GetBoundingRectangles` turns a range
        into rectangles, one per line it spans.  A caret spans one, so the
        first is the answer.

        **A degenerate range is allowed to have no rectangles**, and providers
        differ on whether it does, exactly as they differ on macOS: there the
        same control answers a zero-length range with CGRectZero and a
        length-of-one with a real rectangle (Terminal.app), or the reverse
        (TextEdit at the end of its text).  So an empty answer is retried on
        the caret's *character*, whose left edge is the same place.

        **The character has to be one.** An empty range expands to whatever
        is at the caret, and where that is a newline the rectangle is the end
        of the line *box* rather than anywhere the caret has been - see
        `_is_a_place_in_a_line`, which is the Windows half of the newline
        case macOS meets from the other side.

        **Reported as the control gives it, lies included**; whether a
        rectangle is usable is `keyhac.core.anchor`'s single rule, tested
        without an application to be wrong.  A control that says nothing here
        - and a great many say nothing, this being the least-implemented
        corner of TextPattern - simply has no caret to offer.

        Args:
            trace: a list to append `(label, value)` rows to, one per step
                actually taken. `describe_caret()` passes one so the report
                cannot describe a road this method does not walk - two
                spellings answer here, and which one did is the whole
                diagnosis when a balloon opens a character away from the
                caret.
        """
        def note(label, value):
            if trace is not None:
                trace.append((label, value))

        pattern = self._pattern(_PatternId.Text)
        if pattern is None:
            return None
        try:
            ranges = ctypes.c_void_p()
            hr = _com_call(pattern, _IUIAutomationTextPattern.GetSelection,
                           ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)],
                           ctypes.byref(ranges))
            if hr != S_OK or not ranges.value:
                note("GetSelection", f"nothing (0x{hr & 0xffffffff:08x})")
                return None
            try:
                text_range = _element_out(ranges,
                                          _IUIAutomationTextRangeArray.GetElement,
                                          ctypes.c_int(0))
                if text_range is None:
                    note("GetSelection", "an empty array - no selection at all")
                    return None
                try:
                    note("the selection's text", _shown(_range_text(text_range)))
                    rect = _first_bounding_rect(text_range)
                    note("its first bounding rectangle", rect)
                    if rect is not None and rect[3] > 0:
                        return rect
                    # Expanding the snapshot moves nothing the user can see:
                    # GetSelection hands back a range object, not the
                    # selection itself.
                    hr = _com_call(text_range,
                                   _IUIAutomationTextRange.ExpandToEnclosingUnit,
                                   ctypes.c_long, [ctypes.c_int],
                                   ctypes.c_int(_TextUnit.Character))
                    if hr != S_OK:
                        note("ExpandToEnclosingUnit(Character)",
                             f"failed (0x{hr & 0xffffffff:08x})")
                        return rect
                    text = _range_text(text_range)
                    note("expanded to its character, that is", _shown(text))
                    expanded = _first_bounding_rect(text_range)
                    note("its first bounding rectangle", expanded)
                    if not _is_a_place_in_a_line(text):
                        note("which is not the caret",
                             "a newline is drawn at the end of its line box")
                        return rect
                    return expanded or rect
                finally:
                    _release(text_range)
            finally:
                _release(ranges)
        finally:
            _release(pattern)

    def _selection_expanded_to(self, unit: int):
        """(text, first rectangle) of the selection widened to `unit`.

        Diagnostic only - `describe_caret()` asks for the caret's *line*,
        which is the answer a suspect caret has to agree with: a rectangle
        that is not on the caret's line is not the caret, whatever else it
        might be.
        """
        pattern = self._pattern(_PatternId.Text)
        if pattern is None:
            return None, None
        try:
            ranges = ctypes.c_void_p()
            hr = _com_call(pattern, _IUIAutomationTextPattern.GetSelection,
                           ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)],
                           ctypes.byref(ranges))
            if hr != S_OK or not ranges.value:
                return None, None
            try:
                text_range = _element_out(ranges,
                                          _IUIAutomationTextRangeArray.GetElement,
                                          ctypes.c_int(0))
                if text_range is None:
                    return None, None
                try:
                    hr = _com_call(text_range,
                                   _IUIAutomationTextRange.ExpandToEnclosingUnit,
                                   ctypes.c_long, [ctypes.c_int],
                                   ctypes.c_int(unit))
                    if hr != S_OK:
                        return None, None
                    return _range_text(text_range), _first_bounding_rect(text_range)
                finally:
                    _release(text_range)
            finally:
                _release(ranges)
        finally:
            _release(pattern)

    def describe_caret(self) -> list:
        """What the caret read had to work with (the macOS twin's counterpart).

        Every row is a step `get_caret_rect()` took, recorded by the method
        itself rather than re-derived here - UIA has one spelling of the
        question but two roads through it, the degenerate selection and the
        character it is expanded to, and they can answer a character apart.
        The caret's *line* comes last, as the truth the answer has to agree
        with: a rectangle at the far end of the line the caret is on is not
        the caret, and nothing else in the report says so.
        """
        pattern = self._pattern(_PatternId.Text)
        if pattern is None:
            return [("TextPattern", "not supported by this control")]
        _release(pattern)
        rows = [("TextPattern", "supported")]
        self.get_caret_rect(rows)
        line_text, line_rect = self._selection_expanded_to(_TextUnit.Line)
        rows.append(("the caret's line", _shown(line_text)))
        rows.append(("the line's rectangle", line_rect))
        return rows

    def get_line_at_caret(self) -> str | None:
        """The line the caret is on.

        UIA has no "line at caret" call: take the selection (a degenerate
        range when nothing is selected, which is the caret) and widen it to
        its enclosing line.
        """
        pattern = self._pattern(_PatternId.Text)
        if pattern is None:
            return None
        try:
            ranges = ctypes.c_void_p()
            hr = _com_call(pattern, _IUIAutomationTextPattern.GetSelection,
                           ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)],
                           ctypes.byref(ranges))
            if hr != S_OK or not ranges.value:
                return None
            try:
                text_range = _element_out(ranges,
                                          _IUIAutomationTextRangeArray.GetElement,
                                          ctypes.c_int(0))
                if text_range is None:
                    return None
                try:
                    hr = _com_call(text_range,
                                   _IUIAutomationTextRange.ExpandToEnclosingUnit,
                                   ctypes.c_long, [ctypes.c_int],
                                   ctypes.c_int(_TextUnit.Line))
                    if hr != S_OK:
                        return None
                    return _range_text(text_range)
                finally:
                    _release(text_range)
            finally:
                _release(ranges)
        finally:
            _release(pattern)

    def set_focus(self) -> None:
        """Ask for the keyboard focus.  Whether it landed is a separate
        question - `has_focus()` and `contains_focus()` answer it.

        Returns nothing on purpose.  This used to return `SetFocus() == S_OK`,
        which is whether the request was *accepted*, and callers read it as
        whether the focus had landed.  The two differ: measured with
        tools/win_focus_pass.py against a page in Edge, a `<div>` and a `<p>`
        both accepted the call with S_OK while the focus stayed on the text
        field that already had it, and Notepad's WinUI status bar does the
        same.  A `set_text()` on the strength of that answer types the
        caller's data into whatever field the user was last in.

        Returning a verdict at all was the deeper mistake, because there are
        two honest verdicts and this layer cannot choose between them for the
        caller - see the two methods below.  A caller that forgets to check
        now gets None, which is falsy, so the failure is in the safe
        direction.

        The HRESULT is logged, not returned: it answers the other question,
        and providers were measured answering S_OK to both.
        """
        if not self._ptr:
            return
        hr = _com_call(self._ptr, _IUIAutomationElement.SetFocus, ctypes.c_long, [])
        if hr != S_OK:
            logger.debug(f"SetFocus was refused: 0x{hr & 0xFFFFFFFF:08x}")

    def has_focus(self) -> bool:
        """Whether the system-wide keyboard focus is on *this* element.

        Compared against `GetFocusedElement`, not this element's own
        `HasKeyboardFocus`, for the reason macOS gives and Windows confirms: a
        page element in a background browser reports HasKeyboardFocus True
        while the keyboard is somewhere else entirely (measured - Edge behind
        another window, its field still True).  The flag is only the fallback
        for when UIA cannot name a focused element or cannot compare it.
        """
        focused = UIElement.from_focus()
        if focused is None:
            return bool(self.get_attribute_value("HasKeyboardFocus"))
        same = _same_element(self._ptr, focused._ptr)
        if same is None:
            # CompareElements could not answer; the element's own flag is the
            # only thing left, and it is better than the HRESULT was.
            return bool(self.get_attribute_value("HasKeyboardFocus"))
        return same

    def contains_focus(self) -> bool:
        """Whether the keyboard focus is on this element *or inside it*.

        The other honest reading, and the dangerous one to assume: with a
        page's text field focused, the `<div>` around it, the document and the
        panes above it all contain the focus, and none of them can take a
        keystroke.  A caller that acts on this answer must know why it is
        entitled to - `keyhac.core.fill` accepts it only for the control types
        measured to hand their focus to a part of themselves.

        Bounded by FOCUS_ANCESTOR_LIMIT.  The focused element is read once
        and walked up from, rather than calling has_focus() first: each read
        is a cross-process round trip, and this runs on the main thread.
        """
        focused = UIElement.from_focus()
        if focused is None:
            return bool(self.get_attribute_value("HasKeyboardFocus"))
        element = focused
        for _ in range(FOCUS_ANCESTOR_LIMIT + 1):
            if element is None:
                return False
            same = _same_element(self._ptr, element._ptr)
            if same is None:
                return bool(self.get_attribute_value("HasKeyboardFocus"))
            if same:
                return True
            element = element.parent()
        return False

    # -- construction -------------------------------------------------------

    @staticmethod
    def from_focus() -> "UIElement | None":
        """The element with keyboard focus, system-wide."""
        automation = get_automation()
        if automation is None:
            return None
        ptr = _element_out(automation, _IUIAutomation.GetFocusedElement)
        return UIElement(ptr) if ptr else None

    @staticmethod
    def element_at_point(x: float, y: float) -> "UIElement | None":
        """The element under a screen point, whichever application owns it.

        STATUS: unverified on hardware, like the text accessors above -
        ElementFromPoint's slot (7) comes from the same header ordering that
        ElementFromHandle (6) and GetFocusedElement (8), both verified, sit in.
        """
        automation = get_automation()
        if automation is None:
            return None
        out = ctypes.c_void_p()
        hr = _com_call(automation, _IUIAutomation.ElementFromPoint, ctypes.c_long,
                       [_POINT, ctypes.POINTER(ctypes.c_void_p)],
                       _POINT(int(x), int(y)), ctypes.byref(out))
        if hr != S_OK or not out.value:
            return None
        return UIElement(out)

    @staticmethod
    def from_hwnd(hwnd) -> "UIElement | None":
        automation = get_automation()
        if automation is None or not hwnd:
            return None
        ptr = _element_out(automation, _IUIAutomation.ElementFromHandle,
                           ctypes.c_void_p(int(hwnd)))
        return UIElement(ptr) if ptr else None


_walker = None


def _control_view_walker():
    """The cached control-view TreeWalker (the view that skips the raw tree's
    noise - the closest analogue to what an AX parent walk yields)."""
    global _walker
    if _walker is not None:
        return _walker
    automation = get_automation()
    if automation is None:
        return None
    out = ctypes.c_void_p()
    hr = _com_call(automation, _IUIAutomation.get_ControlViewWalker, ctypes.c_long,
                   [ctypes.POINTER(ctypes.c_void_p)], ctypes.byref(out))
    if hr != S_OK or not out.value:
        return None
    _walker = out
    return _walker
