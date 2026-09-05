"""UIElement - macOS Accessibility automation for configs (port of
keyhac-mac's KeyhacCore_UIElement, in PyObjC). Exposed as Focus.native."""

import ApplicationServices as AS
import Quartz
from AppKit import NSWorkspace
from Foundation import NSArray, NSDictionary

from keyhac.core.uitree import _first_name

_AX_TYPES = {
    "point": AS.kAXValueCGPointType, "size": AS.kAXValueCGSizeType,
    "rect": AS.kAXValueCGRectType, "range": AS.kAXValueCFRangeType,
}

#: What UIElement.describe() reads, in one batched call.  Order matters only
#: in that the results come back positionally.
_DESCRIBE_ATTRS = ["AXRole", "AXTitle", "AXValue", "AXDescription", "AXHelp",
                   "AXDOMIdentifier", "AXIdentifier", "AXPosition", "AXSize"]

#: Descent bound for UIElement.get_text()'s leaf collection.
_TEXT_MAX_DEPTH = 12

#: How far up from the focused element contains_focus() looks - the twin of
#: the Windows constant of the same name, and bounded for the same reason.
FOCUS_ANCESTOR_LIMIT = 4


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
            # PyObjC hands a CFRange back as a plain (location, length) tuple,
            # not as a struct - unlike CGPoint/CGSize/CGRect just above, which
            # do arrive with named fields. Reading .location therefore raised
            # AttributeError on every range attribute (AXSelectedTextRange,
            # AXVisibleCharacterRange), which is most of the caret vocabulary.
            if isinstance(out, (tuple, list)):
                return (out[0], out[1])
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
    if type_name == "int":
        # AXLineForIndex / AXRangeForLine take an integer index; the float
        # "number" builds a CFNumber of the wrong type for them.
        return int(value)
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

    def get_attribute_values(self, names: list[str]) -> dict:
        """Read several attributes in one call.

        Every accessibility read is a round trip into the other application,
        answered on *its* main thread, so the count of them - not the amount
        of data - is what a tree walk costs. Reading seven attributes of a
        menu item one at a time costs seven round trips; this costs one.

        Args:
            names: Attribute names, in this platform's vocabulary
                ("AXRole", "AXTitle", ...).

        Returns:
            A dict from name to value. An attribute the element does not
            have maps to None, so a caller reads the result the same way
            whether the element answered or not.
        """
        err, values = AS.AXUIElementCopyMultipleAttributeValues(
            self._ref, list(names), 0, None)
        if err != 0 or values is None:
            return {name: None for name in names}
        return dict(zip(names, [_from_ax(v) for v in values]))

    def set_attribute_value(self, name: str, type_name: str, value) -> None:
        AS.AXUIElementSetAttributeValue(self._ref, name, _to_ax(type_name, value))

    def set_focus(self) -> None:
        """Ask for the keyboard focus (the macOS half of the Windows
        UIElement.set_focus()).  Whether it landed is `has_focus()` /
        `contains_focus()`.

        Returns nothing on purpose: AX takes AXFocused writes that do nothing,
        so the write is not the verdict, and there are two honest verdicts
        this layer cannot choose between for the caller.  A caller that
        forgets to check gets None, which is falsy - the failure lands in the
        safe direction.
        """
        self.set_attribute_value("AXFocused", "bool", True)

    def has_focus(self) -> bool:
        """Whether the system-wide keyboard focus is on *this* element.

        Checked against the system-wide AXFocusedUIElement, not this element's
        own AXFocused: an element in a background application can hold its
        application's focus while the keyboard goes somewhere else entirely.
        Anything about to send keystrokes needs to know which of those it has,
        because the failure is not "nothing happens", it is text typed into
        whatever the user was actually looking at.  (Measured on Windows,
        where the same is true of a page element in a browser that is behind
        another window.)
        """
        err, focused = AS.AXUIElementCopyAttributeValue(
            AS.AXUIElementCreateSystemWide(), "AXFocusedUIElement", None)
        if err == 0 and focused is not None:
            return bool(AS.CFEqual(focused, self._ref))
        return bool(self.get_attribute_value("AXFocused"))

    def contains_focus(self) -> bool:
        """Whether the keyboard focus is on this element *or inside it*.

        The other honest reading of "did it get focus", and the one a caller
        must justify before acting on: everything that contains the focused
        control also contains the focus, up to the application itself, and
        none of those can take a keystroke.  `keyhac.core.fill` accepts it
        only for the control roles measured to hand their focus to a part of
        themselves.

        STATUS: unverified on hardware.  Written from the Windows measurement
        (tools/win_focus_pass.py) and this file's own AX vocabulary, on a
        machine with no Mac - the same way the Windows text layer was written
        before tools/uia_pass.py settled it.  What wants measuring here is
        whether an AXComboBox delegates its focus to an inner AXTextField the
        way a Win32 ComboBox does; if it does not, macOS simply never uses
        this path.

        The focused element is read once and walked up from, rather than
        calling has_focus() first: every accessibility read is a round trip
        into the other application, answered on *its* main thread, so the
        count of them is what this costs.
        """
        err, focused = AS.AXUIElementCopyAttributeValue(
            AS.AXUIElementCreateSystemWide(), "AXFocusedUIElement", None)
        if err != 0 or focused is None:
            return bool(self.get_attribute_value("AXFocused"))
        element = _from_ax(focused)
        for _ in range(FOCUS_ANCESTOR_LIMIT + 1):
            if not isinstance(element, UIElement):
                return False
            if bool(AS.CFEqual(element._ref, self._ref)):
                return True
            element = element.parent()
        return False

    def set_value(self, value: str) -> bool:
        """Write AXValue (the macOS counterpart of the Windows Value pattern).

        **Frequently does nothing, silently.** Measured against a plain
        `<input type=text>` in Safari - no framework in the way - the write
        returned without error and the field stayed empty.  It is here for
        completeness and for the applications where it does work; use
        `keyhac.core.fill.set_text()`, which pastes, falls back to typing, and
        reads the value back either way.
        """
        self.set_attribute_value("AXValue", "string", value)
        return self.get_attribute_value("AXValue") == value

    def get_parameterized_attribute_value(self, name: str, type_name: str, value):
        """Read an attribute that takes an argument (AXStringForRange, ...).

        The caret vocabulary is all parameterized: AXLineForIndex turns an
        offset into a line number, AXRangeForLine turns that back into a range,
        AXStringForRange turns a range into text.
        """
        err, out = AS.AXUIElementCopyParameterizedAttributeValue(
            self._ref, name, _to_ax(type_name, value), None)
        return _from_ax(out) if err == 0 else None

    # -- text layer ---------------------------------------------------------

    def get_selection(self) -> str | None:
        """The selected text inside this element, or None.

        "" is a real answer - a caret with nothing selected - and is not None.
        """
        value = self.get_attribute_value("AXSelectedText")
        return value if isinstance(value, str) else None

    def get_text(self) -> str | None:
        """This element's whole text content.

        AXValue where the element has one (fields, text areas), and otherwise
        the text of its leaf descendants joined by newline: web content puts a
        <pre>'s or a paragraph's text in child AXStaticText nodes and leaves
        the container's own AXValue empty, so reading AXValue alone reports
        nothing for exactly the elements a log or an error line lives in.

        Leaves only, because WebKit nests an AXStaticText inside the
        AXStaticText of a label and counting both doubles every string.
        """
        value = self.get_attribute_value("AXValue")
        if isinstance(value, str) and value:
            return value

        parts = []

        def collect(element, depth):
            children = element.children()
            if not children:
                text = element.get_attribute_value("AXValue")
                if isinstance(text, str) and text and (not parts or parts[-1] != text):
                    parts.append(text)
                return
            if depth < _TEXT_MAX_DEPTH:
                for child in children:
                    collect(child, depth + 1)

        collect(self, 0)
        return "\n".join(parts) if parts else (value if isinstance(value, str) else None)

    def get_rect(self) -> tuple | None:
        """This element's screen rectangle as (x, y, w, h), or None.

        Two attribute reads where `describe()` makes eight, for the callers
        that want a place and nothing else - placing a popup beside the
        focused control, checking that a caret rectangle is not a lie.
        """
        position = self.get_attribute_value("AXPosition")
        size = self.get_attribute_value("AXSize")
        if isinstance(position, tuple) and isinstance(size, tuple):
            return (position[0], position[1], size[0], size[1])
        return None

    def get_coordinate_scale(self) -> float:
        """Screen units per logical unit: always 1.0 here.

        AX rectangles are in points, and a point is a point on a Retina
        display exactly as on any other - the backing scale never reaches
        this API. `keyhac.core.anchor` asks because its Windows twin has a
        different answer, where the same call reports physical pixels and a
        one-line field on a 200% display measures 112 of them.
        """
        return 1.0

    def get_caret_rect(self) -> tuple | None:
        """The text insertion point's screen rectangle, or None.

        `AXSelectedTextRange` gives the caret its character offset and
        `AXBoundsForRange` turns a range there into a rectangle: the pair an
        IME places its candidate window with.

        **The character at the caret is asked first, and the insertion point
        second.**  Both name the same place - a length of one starts where a
        length of zero sits - and the character is the one applications get
        right.  Checked against the bounds of the caret's own line, which is
        the truth an answer has to agree with:

            app             len 0      len 1      the line
            iTerm2          y=425      y=425      y=425
            Terminal.app    CGRectZero y=649      y=649
            TextEdit        y=399      y=413      y=413   <- len 0 is a
            (its find field)y=362      y=362      y=362      whole line high

        Four elements, and the zero-length range is wrong or absent on two of
        them while the character agrees every time.  TextEdit's is the one
        that shows on screen: a balloon placed under a caret reported a line
        too high lands on top of the line being typed.

        The character's width is whatever it occupies - a newline runs to the
        end of the line - which costs nothing, placement using the left edge
        and the height.

        A caret past the last character has no character to bound.  The
        character *before* it does, and says more than the insertion point
        does: the caret is at that character's trailing edge - or at the
        start of the line below it, when that character is a newline.  That
        last case is TextEdit at the end of a document, where every other
        answer names the line the newline is on and the caret is on the empty
        line under it:

            AXBoundsForRange(63, 0)  (101, 497, 0, 14)     <- a line too high
            AXBoundsForRange(62, 1)  (101, 497, 576, 14)   <- the newline
            the caret's line         (101, 497, 576, 14)   <- agrees, wrongly
            where the caret is       (101, 511, 0, 14)

        The insertion point is the last resort, with its y and height taken
        from the caret's line where that can be had.

        **Reported as the element gives it, lies included**, except that a
        rectangle with no height is not an answer and the other spelling is
        tried instead.  Measured in VS Code (Electron): both answer
        (0, 1112, 0, 0) for an element whose own frame is
        (1275, 981, 409, 40) - CGRectZero, flipped - and the first of them
        comes back for the log to show.  Judging *that* is
        `keyhac.core.anchor`'s job, so the rule is one rule and can be tested
        without an application to be wrong.
        """
        selection = self.get_attribute_value("AXSelectedTextRange")
        if not isinstance(selection, tuple):
            return None
        location = selection[0]
        refused = None
        for length in (1, 0):
            rect = self.get_parameterized_attribute_value(
                "AXBoundsForRange", "range", (location, length))
            if not isinstance(rect, tuple) or len(rect) != 4:
                continue
            if rect[3] <= 0:
                # Kept so the caller can log what was actually said. "Said
                # nothing" and "said a rectangle with no height" are different
                # applications behaving differently, and the log is where that
                # difference gets noticed.
                refused = refused or rect
                continue
            if length == 1:
                return rect
            trailing = self._after_the_last_character(location)
            if trailing is not None:
                return trailing
            line = self._caret_line_rect(location)
            return (rect[0], line[1], rect[2], line[3]) if line else rect
        # No line lookup here: reaching this means AXBoundsForRange answered
        # uselessly or not at all, and the line is asked through the same
        # attribute. Three more round trips into an application that has
        # already said nothing, on the key hook's clock, for nothing.
        #
        # The marker API is a different attribute and a different
        # implementation, which is exactly why it is worth the two round
        # trips: in a Gmail compose body it answers (142, 413, 0, 14) for an
        # element at (107, 341, 512, 295) where AXBoundsForRange answers
        # CGRectZero to both spellings.
        return self._caret_marker_rect() or refused

    def _caret_marker_rect(self) -> tuple | None:
        """The caret through Chromium's and WebKit's own text API, or None.

        Those two carry a second text interface keyed on opaque "text
        markers", and it is implemented where `AXBoundsForRange` is not.  It
        is asked only as a fallback: on a native control the first road works
        and this one costs two more cross-process round trips.

        **An empty range degenerates to the element itself** - VS Code's
        editor being the case, its input proxy carrying no text - and that is
        reported as given, like every other answer here.  Refusing it is
        `keyhac.core.anchor.usable_caret`'s, which refuses the same shape
        from `AXBoundsForRange` (Excel's grid answers that way) and would
        have to agree with itself about both.
        """
        err, marker_range = AS.AXUIElementCopyAttributeValue(
            self._ref, "AXSelectedTextMarkerRange", None)
        if err != 0 or marker_range is None:
            return None
        err, bounds = AS.AXUIElementCopyParameterizedAttributeValue(
            self._ref, "AXBoundsForTextMarkerRange", marker_range, None)
        if err != 0:
            return None
        rect = _from_ax(bounds)
        if not isinstance(rect, tuple) or len(rect) != 4 or rect[3] <= 0:
            return None
        return rect

    def describe_caret(self) -> list:
        """Every way of asking where the caret is, and what each answered.

        For `ReportCaretAnchor`, which has to say *why* a popup went where it
        did - and the why is always which spelling of the question this
        particular application chose to answer.
        """
        selection = self.get_attribute_value("AXSelectedTextRange")
        rows = [("AXSelectedTextRange", selection),
                ("AXNumberOfCharacters",
                 self.get_attribute_value("AXNumberOfCharacters"))]
        if not isinstance(selection, tuple):
            return rows
        for length in (1, 0):
            rows.append((f"AXBoundsForRange(caret, {length})",
                         self.get_parameterized_attribute_value(
                             "AXBoundsForRange", "range",
                             (selection[0], length))))
        rows.append(("bounds of the caret's line",
                     self._caret_line_rect(selection[0])))
        rows.append(("AXBoundsForTextMarkerRange (the marker API)",
                     self._caret_marker_rect()))
        return rows

    def _after_the_last_character(self, location: int) -> tuple | None:
        """Where a caret past the end of the text is, or None.

        Read from the character before it, which is the only one there is to
        ask.  A newline is the case worth the extra round trip: the caret is
        then on the line *below* the one that character is on, at its start,
        and nothing else in the AX vocabulary says so.

        Without the text - `AXStringForRange` is not universal - the trailing
        edge is used for everything, which is right except after a newline,
        where it gives the correct line and the far end of it.
        """
        if location <= 0:
            return None
        previous = self.get_parameterized_attribute_value(
            "AXBoundsForRange", "range", (location - 1, 1))
        if not isinstance(previous, tuple) or len(previous) != 4 \
                or previous[3] <= 0:
            return None
        text = self.get_parameterized_attribute_value(
            "AXStringForRange", "range", (location - 1, 1))
        if text in ("\n", "\r", "\r\n", "\u2028", "\u2029"):
            line = self._caret_line_rect(location)
            return ((line[0] if line else previous[0]),
                    previous[1] + previous[3], 0.0, previous[3])
        return (previous[0] + previous[2], previous[1], 0.0, previous[3])

    def _caret_line_rect(self, location: int) -> tuple | None:
        """The bounds of the line a character offset is on, or None."""
        line = self.get_parameterized_attribute_value(
            "AXLineForIndex", "int", location)
        if line is None:
            return None
        line_range = self.get_parameterized_attribute_value(
            "AXRangeForLine", "int", line)
        if not isinstance(line_range, tuple):
            return None
        rect = self.get_parameterized_attribute_value(
            "AXBoundsForRange", "range", line_range)
        if isinstance(rect, tuple) and len(rect) == 4 and rect[3] > 0:
            return rect
        return None

    def get_line_at_caret(self) -> str | None:
        """The line the caret is on, without the user selecting anything.

        The cheapest read in the text layer: one keystroke in the app that
        already has focus, no pointer and no selection (doc/dev/
        ai-integration.md §6).  None when the element has no caret.
        """
        selection = self.get_attribute_value("AXSelectedTextRange")
        if not isinstance(selection, tuple):
            return None
        line = self.get_parameterized_attribute_value(
            "AXLineForIndex", "int", selection[0])
        if line is None:
            return None
        line_range = self.get_parameterized_attribute_value(
            "AXRangeForLine", "int", line)
        if not isinstance(line_range, tuple):
            return None
        text = self.get_parameterized_attribute_value(
            "AXStringForRange", "range", line_range)
        return text if isinstance(text, str) else None

    def parent(self) -> "UIElement | None":
        """The AX parent element (the same shape the Windows UIElement's
        control-view parent() walk gives - doc/configuration.md)."""
        parent = self.get_attribute_value("AXParent")
        return parent if isinstance(parent, UIElement) else None

    def children(self) -> list["UIElement"]:
        """The AX child elements (empty for a leaf, or for an element whose
        application has not built its accessibility tree - see
        `set_manual_accessibility`)."""
        children = self.get_attribute_value("AXChildren")
        if not isinstance(children, list):
            return []
        return [c for c in children if isinstance(c, UIElement)]

    def identity_key(self):
        """A hashable identity, for the DAG dedupe in keyhac.core.uitree.

        AX element refs hash and compare by CFEqual, so the same table cell
        reached through its row and through its column collapses to one node.
        (`is` does not work here: each read builds a fresh Python proxy.)
        """
        return self._ref

    def describe(self) -> dict:
        """The portable projection consumed by keyhac.core.uitree.UINode.

        One AXUIElementCopyMultipleAttributeValues call rather than eight
        AXUIElementCopyAttributeValue calls: same answers (verified node for
        node on a live page), a little over twice as fast, and the saving is
        per node over a whole tree.  Attributes the element does not have come
        back as an AX error value, which _from_ax renders as None.
        """
        err, values = AS.AXUIElementCopyMultipleAttributeValues(
            self._ref, _DESCRIBE_ATTRS, 0, None)
        if err != 0 or values is None:
            got = {}
        else:
            got = dict(zip(_DESCRIBE_ATTRS, [_from_ax(v) for v in values]))

        position, size = got.get("AXPosition"), got.get("AXSize")
        rect = None
        if isinstance(position, tuple) and isinstance(size, tuple):
            rect = (position[0], position[1], size[0], size[1])

        role = got.get("AXRole")
        value = got.get("AXValue")
        if not isinstance(value, (str, int, float, bool)):
            # AXValue is an element on some containers; not content.
            value = None
        elif role == "AXHeading":
            # A heading's AXValue is its *level* - "2" for an <h2> - not its
            # text.  Reporting it as content puts a stray number in the middle
            # of every heading read, which is how this was found: a dialog
            # title came back as "Approve this item? 2 Approve this item?".
            # The level is still there for anyone who wants it, on .element.
            value = None

        # AXTitle is the label; AXDescription is what an unlabelled control
        # (an icon button) offers instead; AXHelp is the tooltip, the last
        # thing left before an element has no name at all.  Content stays out
        # of all three - it is `value`.  Which one answered is reported as
        # `name_source`, because it decides what an action can do with it: a
        # title is a label the user can see, a description is one only
        # assistive tech sees, and a tooltip may say something else entirely.
        name, name_source = _first_name(
            ("label", got.get("AXTitle")),
            ("description", got.get("AXDescription")),
            ("help", got.get("AXHelp")),
        )
        return {
            "role": got.get("AXRole"),
            "name": name,
            "name_source": name_source,
            "value": value,
            # The DOM id in web content, AXIdentifier in native AppKit UI.
            "identifier": got.get("AXDOMIdentifier") or got.get("AXIdentifier"),
            "rect": rect,
        }

    def set_manual_accessibility(self, enable: bool = True) -> None:
        """Ask a Chromium-based application to build its accessibility tree.

        Call on an *application* element.  Chrome, Edge and Electron apps (VS
        Code, Slack, Discord) ship their accessibility tree switched off and
        build it only once an assistive client asks.  Until then the
        application exposes its menu bar and a window whose subtree is browser
        chrome only - measured on Chrome 2026-08-06, a loaded page was 59
        nodes with no form, no table and no page text - which reads like an
        app that has no accessible content rather than one waiting to be
        asked.  With it on, the same window was 119 nodes and every field
        addressable by its DOM id.

        Two attributes, because the apps disagree about which one they honour:
        `AXManualAccessibility` is Chromium's targeted opt-in, and Chrome
        ignores it - only `AXEnhancedUserInterface` moved it.  That one is the
        blunter signal ("an assistive client is present"), and some apps change
        behaviour when they see it: VS Code switches the editor to its
        screen-reader-optimised rendering.  So this is an explicit call and
        never something a walk does on its own.

        **Switching it off does not put the tree back.**  Measured again on
        2026-08-31: six minutes after `set_manual_accessibility(False)` Chrome
        was still exposing the page, and an earlier note here that it returned
        to 59 nodes did not reproduce.  What the flag does reverse is whether
        a *press* into that content is live - on VS Code, eight presses with
        it unset moved nothing and four with it set all worked - so this is a
        switch on acting, and close to a one-way door on reading.

        **The tree is not built by this alone.**  A freshly started Electron
        application exposes 13 nodes and no web area, and stays there however
        often it is walked; one `MacFocusProvider.get_focus()` takes it to
        443.  So Keyhac's own key hook has been turning it on as a side effect
        of focus tracking all along, one keystroke before anything else could
        ask - which is why "is the flag needed in order to read?" measures as
        no on a running system and yes on a fresh one.

        The screen-reader rendering above is the author's observation and is
        left standing; it did not show up in the accessibility tree when
        looked for on 2026-08-31 (no notification, no element added or
        removed), which is not the same as not happening.

        Native Cocoa apps ignore both.
        """
        for attribute in ("AXManualAccessibility", "AXEnhancedUserInterface"):
            try:
                self.set_attribute_value(attribute, "bool", bool(enable))
            except Exception:
                # Not every app advertises both; setting an absent one is a
                # no-op error, not a reason to skip the other.
                pass

    def get_action_names(self) -> list[str]:
        err, names = AS.AXUIElementCopyActionNames(self._ref, None)
        return [str(n) for n in names] if err == 0 and names else []

    def perform_action(self, name: str) -> bool:
        """Run an action, and report whether the OS accepted it.

        The result used to be discarded, which made a press on an element that
        had gone away indistinguishable from one that worked - the silent
        wrong thing §3.7 exists to refuse. Windows already returned a bool
        here; now both do.
        """
        return AS.AXUIElementPerformAction(self._ref, name) == 0

    def is_stale(self) -> bool:
        """True when the element this reference points at no longer exists.

        Asked with the cheapest possible read: AX answers
        kAXErrorInvalidUIElement for a reference whose element is gone, and
        that is a different error from "this element has no AXRole", so a
        control that simply lacks the attribute is not mistaken for a dead one.

        A fact, not a policy - `keyhac.core.uitree.StaleElement` is raised by
        the layer that decides what to do about it.
        """
        err, _ = AS.AXUIElementCopyAttributeValue(self._ref, "AXRole", None)
        return err == AS.kAXErrorInvalidUIElement

    def role(self) -> str | None:
        """Just the role, in one attribute read.

        `describe()` is a batched read of nine attributes; a walk that only
        wants to know whether an element is worth describing pays for eight it
        will not look at. Measured over a 4000-node tree: 588 ms describing
        every node against 346 ms reading the role first and describing only
        the ones that matter.

        lazydocs: ignore
        """
        return self.get_attribute_value("AXRole")

    def menu_bar(self) -> "UIElement | None":
        """The application's menu bar, from any element inside it.

        macOS hangs `AXMenuBar` off the *application* element, so this walks
        up to it first; on an element already at the top the walk stops
        immediately.

        lazydocs: ignore
        """
        current = self
        for _ in range(32):
            bar = current.get_attribute_value("AXMenuBar")
            if bar is not None:
                return bar
            parent = current.parent()
            if parent is None:
                return None
            current = parent
        return None

    @staticmethod
    def get_focused_application() -> "UIElement | None":
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return UIElement(AS.AXUIElementCreateApplication(app.processIdentifier()))

    @staticmethod
    def element_at_point(x: float, y: float) -> "UIElement | None":
        """The element under a screen point, whichever application owns it.

        The other cheap entry into the text layer: the pointer is usually
        already over the line the user means, so this costs one keystroke and
        no selection.  Screen coordinates, top-left origin - the same
        convention as get_screen_frames() and a UINode's rect.
        """
        err, element = AS.AXUIElementCopyElementAtPosition(
            AS.AXUIElementCreateSystemWide(), x, y, None)
        return UIElement(element) if err == 0 and element else None

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
