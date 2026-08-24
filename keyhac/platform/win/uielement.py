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

import ctypes
import sys
from typing import Any

from keyhac.core import log

logger = log.getLogger("WinUIElement")

#: Sibling bound on UIElement.children() - see its docstring.
MAX_CHILDREN = 2000

#: Ancestor bound for contains_focus()'s climb.  The macOS counterpart carries
#: the measurement this is sized from.
FOCUS_ANCESTOR_LIMIT = 64

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
    oleaut32.SysFreeString.argtypes = [ctypes.c_void_p]
    oleaut32.SysFreeString.restype = None
    oleaut32.SysAllocString.argtypes = [ctypes.c_wchar_p]
    oleaut32.SysAllocString.restype = ctypes.c_void_p

    COINIT_APARTMENTTHREADED = 0x2
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
    # 3 is the first method after IUnknown, and the three below it are pinned
    # from the far side: ElementFromHandle (6) is verified against the Win32
    # answer, and UIAutomationClient.h orders CompareElements(3),
    # CompareRuntimeIds(4), GetRootElement(5) before it.
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
    # UNVERIFIED on hardware, unlike its neighbours: it sits between
    # HasKeyboardFocus (26) and AutomationId (29) in UIAutomationClient.h,
    # both of which the accessor tests pin, so the ordering is constrained
    # from both sides - but constrained is not measured.
    get_CurrentIsKeyboardFocusable = 27
    get_CurrentAutomationId = 29
    get_CurrentClassName = 30
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
        "IsKeyboardFocusable": (_IUIAutomationElement.get_CurrentIsKeyboardFocusable, ctypes.c_int),
        "IsOffscreen": (_IUIAutomationElement.get_CurrentIsOffscreen, ctypes.c_int),
    }
    _STRING_ATTRS = {
        "Name": _IUIAutomationElement.get_CurrentName,
        "ClassName": _IUIAutomationElement.get_CurrentClassName,
        "AutomationId": _IUIAutomationElement.get_CurrentAutomationId,
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
            if name in ("HasKeyboardFocus", "IsKeyboardFocusable",
                        "IsOffscreen") and value is not None:
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

    def describe(self) -> dict:
        """The portable projection consumed by keyhac.core.uitree.UINode."""
        return {
            "role": self.get_attribute_value("ControlType"),
            "name": self.get_attribute_value("Name"),
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

    def accepts_focus(self) -> bool:
        """Whether UI Automation says this element can take keyboard focus.

        The counterpart of the macOS AXUIElementIsAttributeSettable guard:
        asked before the write, it separates an element that will take focus
        from one whose SetFocus is accepted and does nothing.

        STATUS: unverified on hardware.  IsKeyboardFocusable is read through
        the same scalar-property path as HasKeyboardFocus below, which is
        verified.
        """
        value = self.get_attribute_value("IsKeyboardFocusable")
        return bool(value) if value is not None else False

    def request_focus(self) -> None:
        """Ask for keyboard focus, without waiting to see whether it arrives.

        The write half of set_focus().  Split out for the same reason as on
        macOS: the write is main-thread element access and the wait for it to
        take effect is not, and only `keyhac.core.fill.focus()` is in a
        position to put each on the right thread.

        The HRESULT is deliberately dropped.  S_OK here means UIA accepted the
        request, which is not the question anyone downstream is asking - see
        has_focus().
        """
        if self._ptr:
            _com_call(self._ptr, _IUIAutomationElement.SetFocus,
                      ctypes.c_long, [])

    def has_focus(self) -> bool:
        """Whether the keyboard is pointed at this element right now."""
        if not self._ptr:
            return False
        return bool(self.get_attribute_value("HasKeyboardFocus"))

    def contains_focus(self) -> bool:
        """Whether the keyboard is inside this element - it, or a descendant.

        The Windows half of the macOS method of the same name, which carries
        the reasoning.  Walks the control view up from the focused element,
        comparing with IUIAutomation::CompareElements - UIA element pointers
        are not identity-comparable, which is also why keyhac.core cannot do
        this walk itself and why it lives here on both platforms.

        STATUS: unverified on hardware.
        """
        if not self._ptr:
            return False
        automation = get_automation()
        walker = _control_view_walker()
        if automation is None or walker is None:
            return False
        focused = _element_out(automation, _IUIAutomation.GetFocusedElement)
        for _ in range(FOCUS_ANCESTOR_LIMIT):
            if focused is None or not focused.value:
                return False
            same = ctypes.c_int()
            hr = _com_call(automation, _IUIAutomation.CompareElements,
                           ctypes.c_long,
                           [ctypes.c_void_p, ctypes.c_void_p,
                            ctypes.POINTER(ctypes.c_int)],
                           ctypes.c_void_p(focused.value),
                           ctypes.c_void_p(self._ptr.value),
                           ctypes.byref(same))
            if hr == S_OK and same.value:
                return True
            focused = _element_out(walker,
                                   _IUIAutomationTreeWalker.GetParentElement,
                                   ctypes.c_void_p(focused.value))
        return False

    def set_focus(self) -> bool:
        """Ask for focus and look once, immediately, to see if it arrived.

        Used to return whether SetFocus returned S_OK, which reports success
        for focus that never landed - the mirror image of the macOS method's
        old fault, and the more dangerous direction, since what follows a
        focus call in this codebase is usually a keystroke.  **Does not
        wait**; `keyhac.core.fill.focus()` is the one that does.
        """
        if not self._ptr:
            return False
        self.request_focus()
        return self.has_focus()

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
