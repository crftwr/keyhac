"""macOS AX value conversion (issue #6: NSArray attributes such as AXWindows
were falling through _from_ax's isinstance checks to the str() fallback)."""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


def test_from_ax_nsarray():
    from Foundation import NSMutableArray
    from keyhac.platform.mac.uielement import _from_ax

    array = NSMutableArray.array()
    array.addObject_("x")
    array.addObject_(2)
    assert _from_ax(array) == ["x", 2]


def test_from_ax_nsdictionary():
    from Foundation import NSDictionary
    from keyhac.platform.mac.uielement import _from_ax

    d = NSDictionary.dictionaryWithDictionary_({"k": "v"})
    assert _from_ax(d) == {"k": "v"}


@pytest.mark.parametrize("type_name,value", [
    ("point", (1.0, 2.0)),
    ("size", (3.0, 4.0)),
    ("rect", (5.0, 6.0, 7.0, 8.0)),
    ("range", (9, 10)),          # CFRange arrives as a tuple, not a struct
])
def test_ax_value_round_trip(type_name, value):
    """Every AXValue type survives _to_ax -> _from_ax unchanged.

    "range" is the one that did not: AXSelectedTextRange raised AttributeError
    instead of returning (location, length), which took the caret vocabulary
    with it.
    """
    from keyhac.platform.mac.uielement import _from_ax, _to_ax

    assert _from_ax(_to_ax(type_name, value)) == value


def test_from_ax_scalars_bridge():
    from Foundation import NSNumber, NSString
    from keyhac.platform.mac.uielement import _from_ax

    assert _from_ax(NSString.stringWithString_("s")) == "s"
    assert _from_ax(NSNumber.numberWithBool_(True)) == True  # noqa: E712 (bridged NSNumber)
    assert _from_ax(NSNumber.numberWithInt_(7)) == 7

