"""UIElement - macOS Accessibility automation for configs (port of
keyhac-mac's KeyhacCore_UIElement, in PyObjC). Exposed as Focus.native."""

import ApplicationServices as AS
import Quartz
from AppKit import NSWorkspace
from Foundation import NSArray, NSDictionary

_AX_TYPES = {
    "point": AS.kAXValueCGPointType, "size": AS.kAXValueCGSizeType,
    "rect": AS.kAXValueCGRectType, "range": AS.kAXValueCFRangeType,
}


def _from_ax(value):
    if value is None:
        return None
    if AS.CFGetTypeID(value) == AS.AXUIElementGetTypeID():
        return UIElement(value)
    if AS.CFGetTypeID(value) == AS.AXValueGetTypeID():
        ax_type = AS.AXValueGetType(value)
        ok, out = AS.AXValueGetValue(value, ax_type, None)
        if not ok:
            return None
        if ax_type == AS.kAXValueCGPointType:
            return (out.x, out.y)
        if ax_type == AS.kAXValueCGSizeType:
            return (out.width, out.height)
        if ax_type == AS.kAXValueCGRectType:
            return (out.origin.x, out.origin.y, out.size.width, out.size.height)
        if ax_type == AS.kAXValueCFRangeType:
            return (out.location, out.length)
        return None
    # AX collections arrive as NSArray/NSDictionary proxies, which are NOT
    # list/dict instances - matching only the Python types silently turned
    # e.g. AXWindows into its str() description (issue #6).
    if isinstance(value, (list, tuple, NSArray)):
        return [_from_ax(v) for v in value]
    if isinstance(value, (dict, NSDictionary)):
        return {str(k): _from_ax(value[k]) for k in value}
    if isinstance(value, (str, int, float, bool)):
        return value
    try:  # NSString/NSNumber bridge
        return str(value)
    except Exception:
        return None


def _to_ax(type_name, value):
    if type_name == "bool":
        return bool(value)
    if type_name == "number":
        return float(value)
    if type_name == "string":
        return str(value)
    if type_name == "point":
        return AS.AXValueCreate(AS.kAXValueCGPointType, Quartz.CGPoint(*value))
    if type_name == "size":
        return AS.AXValueCreate(AS.kAXValueCGSizeType, Quartz.CGSize(*value))
    if type_name == "rect":
        return AS.AXValueCreate(AS.kAXValueCGRectType,
                                Quartz.CGRect(Quartz.CGPoint(value[0], value[1]),
                                              Quartz.CGSize(value[2], value[3])))
    if type_name == "range":
        return AS.AXValueCreate(AS.kAXValueCFRangeType, AS.CFRange(*value[:2]))
    raise ValueError(f"Unknown AX value type: {type_name}")


class UIElement:
    """A UI element in the macOS Accessibility tree."""

    def __init__(self, ref):
        self._ref = ref

    def get_attribute_names(self) -> list[str]:
        err, names = AS.AXUIElementCopyAttributeNames(self._ref, None)
        return [str(n) for n in names] if err == 0 and names else []

    def get_attribute_value(self, name: str):
        err, value = AS.AXUIElementCopyAttributeValue(self._ref, name, None)
        return _from_ax(value) if err == 0 else None

    def set_attribute_value(self, name: str, type_name: str, value) -> None:
        AS.AXUIElementSetAttributeValue(self._ref, name, _to_ax(type_name, value))

    def parent(self) -> "UIElement | None":
        """The AX parent element (the same shape the Windows UIElement's
        control-view parent() walk gives - doc/configuration.md)."""
        parent = self.get_attribute_value("AXParent")
        return parent if isinstance(parent, UIElement) else None

    def get_action_names(self) -> list[str]:
        err, names = AS.AXUIElementCopyActionNames(self._ref, None)
        return [str(n) for n in names] if err == 0 and names else []

    def perform_action(self, name: str) -> None:
        AS.AXUIElementPerformAction(self._ref, name)

    @staticmethod
    def get_focused_application() -> "UIElement | None":
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return UIElement(AS.AXUIElementCreateApplication(app.processIdentifier()))

    @staticmethod
    def get_running_applications() -> list[tuple[str, int]]:
        return [(str(a.localizedName()), int(a.processIdentifier()))
                for a in NSWorkspace.sharedWorkspace().runningApplications()]


    @staticmethod
    def get_screen_frames() -> list[tuple[float, float, float, float]]:
        """Screen frames in AX (top-left origin) coordinates, main first.

        Uses CoreGraphics, NOT NSScreen: this is called from ThreadedAction
        worker threads, and AppKit off the main thread crashes with SIGTRAP.
        CGDisplayBounds is thread-safe and already top-left-origin global."""
        err, displays, _count = Quartz.CGGetActiveDisplayList(16, None, None)
        if err != 0 or not displays:
            return []
        main_id = Quartz.CGMainDisplayID()
        frames = []
        for display in sorted(displays, key=lambda d: d != main_id):
            b = Quartz.CGDisplayBounds(display)
            frames.append((b.origin.x, b.origin.y, b.size.width, b.size.height))
        return frames

    @staticmethod
    def get_onscreen_window_frames() -> list[tuple[float, float, float, float]]:
        """Frames of all normal on-screen windows (AX top-left coords), via
        CGWindowList - far cheaper than per-app AX walks."""
        wins = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID) or []
        frames = []
        for w in wins:
            if w.get("kCGWindowLayer", 0) != 0:
                continue
            b = w.get("kCGWindowBounds")
            if b:
                frames.append((b["X"], b["Y"], b["Width"], b["Height"]))
        return frames
