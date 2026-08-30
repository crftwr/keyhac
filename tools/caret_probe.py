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

--deep (macOS) additionally asks the *other* ways an application might know
where its caret is: Chromium and WebKit carry a second, richer text API keyed
on opaque "text markers", and an element that answers CGRectZero to
AXBoundsForRange may still answer AXBoundsForTextMarkerRange.  Use it on an
element the plain read refuses, and on the element that matters - VS Code's
editor exposes its text area only while it holds the focus, so it cannot be
reached by walking the tree from another process.
"""

from __future__ import annotations

import argparse
import sys
import time


def _read(element):
    from keyhac.core.anchor import display_scale, popup_anchor, usable_caret
    rect = element.get_rect() if hasattr(element, "get_rect") else None
    caret = (element.get_caret_rect()
             if hasattr(element, "get_caret_rect") else None)
    anchor = popup_anchor(element)
    return rect, caret, usable_caret(caret, rect, display_scale(element)), anchor


def _role(element) -> str:
    for attribute in ("AXRole", "ControlType"):
        try:
            value = element.get_attribute_value(attribute)
        except Exception:
            continue
        if value:
            return str(value)
    return "?"


def _check_permission() -> None:
    """Refuse to print "nothing focused" when the answer is "not allowed".

    The Accessibility permission belongs to the *terminal application* this
    is run from, not to python and not to Keyhac - and not necessarily to the
    terminal it was granted to last time. Every AX call from an untrusted
    process returns nothing at all, which is indistinguishable from an
    application that has no focused element until somebody says so.
    """
    if sys.platform != "darwin":
        return
    import ApplicationServices as AS
    if AS.AXIsProcessTrusted():
        return
    raise SystemExit(
        "Not trusted for Accessibility, so every AX call comes back empty.\n"
        "Grant it to the terminal application you are running this from:\n"
        "  System Settings > Privacy & Security > Accessibility\n"
        "The permission is per application - VS Code's built-in terminal and\n"
        "Terminal.app are two different grants.")


def _front_app() -> str:
    if sys.platform != "darwin":
        return "the front application"
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return str(app.localizedName()) if app else "nothing"


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


def _report(label: str, element, deep: bool = False) -> None:
    rect, caret, usable, anchor = _read(element)
    kind = anchor[1] if anchor else "-"
    print(f"{label:24} {_role(element):16} rect={rect} caret={caret} "
          f"usable={usable} anchor={kind}")
    if deep:
        _deep(element)


def _deep(element) -> None:
    """Everything else the element might know about where its caret is.

    Printed rather than judged: this is the reconnaissance that decides
    whether a second way of asking is worth building into the platform layer,
    and what it should ask for.
    """
    if sys.platform != "darwin":
        print("    (--deep is macOS-only for now)")
        return
    import ApplicationServices as AS
    from keyhac.platform.mac.uielement import _from_ax

    reference = getattr(element, "_ref", None)
    if reference is None:
        print("    (not a macOS element)")
        return

    for name in ("AXSelectedTextRange", "AXInsertionPointLineNumber",
                 "AXNumberOfCharacters"):
        print(f"    {name}: {element.get_attribute_value(name)}")
    for length in (0, 1):
        selection = element.get_attribute_value("AXSelectedTextRange")
        if not isinstance(selection, tuple):
            break
        print(f"    AXBoundsForRange(caret, {length}): "
              f"{element.get_parameterized_attribute_value('AXBoundsForRange', 'range', (selection[0], length))}")

    line = element.get_parameterized_attribute_value(
        "AXLineForIndex", "int",
        (element.get_attribute_value("AXSelectedTextRange") or (0,))[0])
    line_range = (element.get_parameterized_attribute_value(
        "AXRangeForLine", "int", line) if line is not None else None)
    print(f"    the caret's line: {line} range={line_range} bounds="
          f"{element.get_parameterized_attribute_value('AXBoundsForRange', 'range', line_range) if isinstance(line_range, tuple) else None}")

    err, marker_range = AS.AXUIElementCopyAttributeValue(
        reference, "AXSelectedTextMarkerRange", None)
    if err != 0 or marker_range is None:
        print(f"    AXSelectedTextMarkerRange: absent (err={err}) - no marker API here")
        return
    # The marker API is Chromium's and WebKit's own, and it is the only road
    # left where AXBoundsForRange is dead. AXUIElementForTextMarker is the
    # interesting one: in a contenteditable the element *containing* the
    # caret is the line's own box, which is the rectangle we could not get
    # any other way.
    for parameterized, caption in (
            ("AXBoundsForTextMarkerRange", "the selection"),
            ("AXUIElementForTextMarker", "the element holding the caret"),
            ("AXLineTextMarkerRangeForTextMarker", "its line, as a range"),
            ("AXLeftLineTextMarkerRangeForTextMarker", "its line, leftwards")):
        err, value = AS.AXUIElementCopyParameterizedAttributeValue(
            reference, parameterized, marker_range, None)
        if err != 0:
            print(f"    {parameterized}: err={err}")
            continue
        decoded = _from_ax(value)
        if isinstance(decoded, tuple):
            print(f"    {parameterized} ({caption}): {decoded}")
            continue
        if hasattr(decoded, "get_rect"):
            print(f"    {parameterized} ({caption}): "
                  f"role={decoded.get_attribute_value('AXRole')} "
                  f"rect={decoded.get_rect()}")
            continue
        # A marker range comes back opaque; ask what it covers on screen.
        err, bounds = AS.AXUIElementCopyParameterizedAttributeValue(
            reference, "AXBoundsForTextMarkerRange", value, None)
        print(f"    bounds of {caption}: "
              f"{_from_ax(bounds) if err == 0 else f'err {err}'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--survey", action="store_true",
                        help="every windowed application, not just the front one")
    parser.add_argument("--repeat", type=int, default=1, metavar="N",
                        help="print N times, one second apart")
    parser.add_argument("--deep", action="store_true",
                        help="also ask the marker-based text API (macOS)")
    args = parser.parse_args()
    _check_permission()

    for turn in range(args.repeat):
        if turn:
            time.sleep(1.0)
        if args.survey:
            _survey()
            print()
            continue
        element = _frontmost()
        if element is None:
            # Which application said so matters: a Chromium one ships its
            # accessibility tree switched off and answers nothing until an
            # assistive client asks, so "no focused element" from Chrome or
            # Electron is a different problem from the same words out of a
            # native application.
            print(f"nothing focused - {_front_app()} is in front and reports "
                  f"no focused element")
            continue
        _report("frontmost", element, deep=args.deep)


if __name__ == "__main__":
    main()
