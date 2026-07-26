"""UIElement - macOS Accessibility automation for configs (port of
keyhac-mac's KeyhacCore_UIElement, in PyObjC). Exposed as Focus.native."""

import ApplicationServices as AS
import Quartz
from AppKit import NSWorkspace

_AX_TYPES = {
    "point": AS.kAXValueCGPointType, "size": AS.kAXValueCGSizeType,
    "rect": AS.kAXValueCGRectType, "range": AS.kAXValueCFRangeType,
}


def _from_ax(value):
    if value is None:
        return None
    type_id = getattr(value, "_cfTypeID", None)
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
    if isinstance(value, (list, tuple)):
        return [_from_ax(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _from_ax(v) for k, v in value.items()}
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
