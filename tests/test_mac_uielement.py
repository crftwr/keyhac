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


def test_from_ax_scalars_bridge():
    from Foundation import NSNumber, NSString
    from keyhac.platform.mac.uielement import _from_ax

    assert _from_ax(NSString.stringWithString_("s")) == "s"
    assert _from_ax(NSNumber.numberWithBool_(True)) == True  # noqa: E712 (bridged NSNumber)
    assert _from_ax(NSNumber.numberWithInt_(7)) == 7
