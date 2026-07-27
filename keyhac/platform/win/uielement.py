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

    COINIT_APARTMENTTHREADED = 0x2
    CLSCTX_INPROC_SERVER = 0x1
    S_OK = 0
    S_FALSE = 1
    RPC_E_CHANGED_MODE = -2147417850  # 0x80010106

    CLSID_CUIAutomation = "{ff48dba4-60ef-4201-aa87-54103eef594e}"
    IID_IUIAutomation = "{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}"


# --------------------------------------------------------------------------
# vtable slot indices (UIAutomationClient.h).  0-2 are IUnknown.

class _IUIAutomation:
    ElementFromHandle = 6
    GetFocusedElement = 8
    get_ControlViewWalker = 14
    get_RawViewWalker = 16


class _IUIAutomationElement:
    SetFocus = 3
    get_CurrentProcessId = 20
    get_CurrentControlType = 21
    get_CurrentLocalizedControlType = 22
    get_CurrentName = 23
    get_CurrentHasKeyboardFocus = 26
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
        return sorted([*self._STRING_ATTRS, *self._SCALAR_ATTRS, *self._DERIVED_ATTRS])

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
        return None

    # -- tree ---------------------------------------------------------------

    def parent(self) -> "UIElement | None":
        walker = _control_view_walker()
        if walker is None or not self._ptr:
            return None
        ptr = _element_out(walker, _IUIAutomationTreeWalker.GetParentElement,
                           ctypes.c_void_p(self._ptr.value))
        return UIElement(ptr) if ptr else None

    def set_focus(self) -> bool:
        if not self._ptr:
            return False
        return _com_call(self._ptr, _IUIAutomationElement.SetFocus,
                         ctypes.c_long, []) == S_OK

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
