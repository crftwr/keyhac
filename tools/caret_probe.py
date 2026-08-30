"""Caret probe - what the focused application actually reports (issue #118).

Placing a popup under the caret depends entirely on the application answering
truthfully, and the answers vary far more than the API suggests.  This prints,
for the focused element, the three numbers the decision is made from:

    get_rect()        the element's own frame
    get_caret_rect()  the caret rectangle it reports for the insertion point
    usable            whether keyhac.core.anchor believes it

**A successful call is not a true answer.**  Measured in VS Code: the caret
comes back as (0, 1112, 0, 0) for an element whose own frame is
(1275, 981, 409, 40) - no height, x at the screen edge, y outside the element
- and nothing about the return value says so.  That measurement is why
`usable_caret` exists, and this is the tool that finds the next one.

Run:  make caret-probe          (repeats for twenty seconds)
      make caret-probe ARGS=--survey
      .venv/bin/python tools/caret_probe.py [--survey] [--repeat N]

It reads the *frontmost* application, which is the case that matters and the
one a terminal cannot see from in front of it - so `make caret-probe` repeats
once a second, leaving time to switch to the application under test, click
into a field, and watch the numbers change.

--survey walks every windowed running application instead.  It is a much
weaker check: a background application legitimately reports no focused
element, so a blank row there means "not asked", not "cannot answer".
"""

from __future__ import annotations

import argparse
import sys
import time


def _read(element):
    from keyhac.core.anchor import popup_anchor, usable_caret
    rect = element.get_rect() if hasattr(element, "get_rect") else None
    caret = (element.get_caret_rect()
             if hasattr(element, "get_caret_rect") else None)
    anchor = popup_anchor(element)
    return rect, caret, usable_caret(caret, rect), anchor


def _role(element) -> str:
    for attribute in ("AXRole", "ControlType"):
        try:
            value = element.get_attribute_value(attribute)
        except Exception:
            continue
        if value:
            return str(value)
    return "?"


def _frontmost():
    """The focused element of the application in front, or None."""
    if sys.platform == "darwin":
        from keyhac.platform.mac.focus import MacFocusProvider
        return MacFocusProvider().get_focused_element()
    if sys.platform == "win32":
        from keyhac.platform.win.focus import WinFocusProvider
        return WinFocusProvider().get_focused_element()
    raise SystemExit(f"No focus provider for {sys.platform}.")


def _survey():
    """Every windowed application's focused element, macOS only."""
    if sys.platform != "darwin":
        raise SystemExit("--survey is macOS-only for now.")
    import ApplicationServices as AS
    from AppKit import NSWorkspace
    from keyhac.platform.mac.uielement import UIElement

    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.activationPolicy() != 0:
            continue
        reference = AS.AXUIElementCreateApplication(app.processIdentifier())
        AS.AXUIElementSetMessagingTimeout(reference, 0.2)
        err, focused = AS.AXUIElementCopyAttributeValue(
            reference, "AXFocusedUIElement", None)
        name = str(app.localizedName())
        if err != 0 or focused is None:
            print(f"{name:24} -  (not asked: nothing focused there)")
            continue
        _report(name, UIElement(focused))


def _report(label: str, element) -> None:
    rect, caret, usable, anchor = _read(element)
    kind = anchor[1] if anchor else "-"
    print(f"{label:24} {_role(element):16} rect={rect} caret={caret} "
          f"usable={usable} anchor={kind}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--survey", action="store_true",
                        help="every windowed application, not just the front one")
    parser.add_argument("--repeat", type=int, default=1, metavar="N",
                        help="print N times, one second apart")
    args = parser.parse_args()

    for turn in range(args.repeat):
        if turn:
            time.sleep(1.0)
        if args.survey:
            _survey()
            print()
            continue
        element = _frontmost()
        if element is None:
            print("nothing focused")
            continue
        _report("frontmost", element)


if __name__ == "__main__":
    main()
