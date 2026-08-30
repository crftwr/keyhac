"""The two rules a caret-anchored popup needs, on their own.

What the chooser and the balloon do with them is in test_chooser.py; these
pin the arithmetic and the disbelief, which is where the surprises are.
"""

from keyhac.core.anchor import (caret_anchor, place_below, place_over_top,
                                popup_anchor, usable_caret)

#: The measurement this whole module exists for: VS Code's text area, and the
#: caret rectangle it answers with for a caret inside it.
VSCODE_ELEMENT = (1275.0, 981.0, 409.0, 40.0)
VSCODE_CARET = (0.0, 1112.0, 0.0, 0.0)


class TestUsableCaret:

    def test_nothing_is_not_a_caret(self):
        assert not usable_caret(None, None)
        assert not usable_caret((1, 2, 3), None)
        assert not usable_caret("nope", None)

    def test_a_caret_has_no_width_and_that_is_fine(self):
        """A caret is a line. Insisting on width would reject every honest
        answer and keep the dishonest ones."""
        assert usable_caret((100.0, 200.0, 0.0, 18.0), (90.0, 190.0, 300.0, 40.0))

    def test_no_height_is_not_fine(self):
        assert not usable_caret((100.0, 200.0, 0.0, 0.0), (90.0, 190.0, 300.0, 40.0))

    def test_the_vs_code_lie(self):
        """The call succeeds and the answer is nonsense: x at the screen edge,
        no size, and a y outside the element it is supposed to be in. Nothing
        in the return value says so, which is why this check exists."""
        assert not usable_caret(VSCODE_CARET, VSCODE_ELEMENT)

    def test_a_caret_outside_its_element_is_not_believed(self):
        assert not usable_caret((100.0, 900.0, 0.0, 18.0),
                                (90.0, 190.0, 300.0, 40.0))

    def test_the_boundary_is_slack(self):
        """A caret at the end of a line sits on its element's edge, and web
        content reports a line box a little proud of the field's bounds."""
        assert usable_caret((390.0, 195.0, 0.0, 18.0), (90.0, 190.0, 300.0, 40.0))

    def test_with_no_element_rect_the_caret_is_taken_on_trust(self):
        """There is nothing to check it against, and refusing would throw away
        every caret on a platform that cannot report an element's frame."""
        assert usable_caret((0.0, 1112.0, 0.0, 18.0), None)


class _Element:
    def __init__(self, caret=None, rect=None):
        self._caret, self._rect = caret, rect

    def get_caret_rect(self):
        return self._caret

    def get_rect(self):
        return self._rect


class TestARectangleTheSizeOfItsOwnElement:
    """A caret is not the size of the thing it is in, and two different roads
    answer that way - both of them a way of saying "nothing".

    Excel with no cell being edited: the grid is an AXLayoutArea of no
    characters, and every spelling of the question - the character, the
    insertion point, even the caret's line - comes back as (482, 293, 945,
    624), which is the grid. VS Code's editor: the marker API for an empty
    range, Monaco's input proxy carrying no text, so the bounds of nothing
    are the whole element."""

    EXCEL_GRID = (482.0, 293.0, 945.0, 624.0)

    def test_excels_grid_is_not_a_caret(self):
        assert not usable_caret(self.EXCEL_GRID, self.EXCEL_GRID)

    def test_vs_codes_marker_answer_is_not_a_caret(self):
        rect = (1274.0, 981.0, 409.0, 40.0)
        assert not usable_caret(rect, rect)

    def test_a_fraction_out_is_still_the_element(self):
        """Screen coordinates that have been through a coordinate flip."""
        assert not usable_caret((1274.4, 981.0, 409.0, 40.0),
                                (1274.0, 981.0, 409.0, 40.0))

    def test_a_real_caret_inside_a_large_element_is_kept(self):
        """Gmail's compose body, which is the answer this road exists for."""
        assert usable_caret((142.0, 413.0, 0.0, 14.0),
                            (107.0, 341.0, 512.0, 295.0))

    def test_nothing_to_compare_against_is_not_a_refusal(self):
        assert usable_caret((142.0, 413.0, 0.0, 14.0), None)


class TestCaretAnchor:

    def test_a_believable_caret(self):
        """Kept at the caret's column, and grown to the bottom of the field it
        is in - see TestClearTheField."""
        element = _Element((100.0, 200.0, 0.0, 18.0), (90.0, 190.0, 300.0, 40.0))
        assert caret_anchor(element) == (100.0, 200.0, 0.0, 30.0)

    def test_a_caret_in_a_document_is_left_as_it_is(self):
        element = _Element((100.0, 200.0, 0.0, 18.0), (90.0, 190.0, 300.0, 800.0))
        assert caret_anchor(element) == (100.0, 200.0, 0.0, 18.0)

    def test_a_lie_is_no_caret(self):
        assert caret_anchor(_Element(VSCODE_CARET, VSCODE_ELEMENT)) is None

    def test_nothing_at_all(self):
        assert caret_anchor(None) is None
        assert caret_anchor(object()) is None       # no such methods

    def test_an_element_that_raises_is_an_element_with_no_caret(self):
        """It may have been destroyed between being handed over and being
        asked, and a popup that fails to open is worse than one in the middle
        of the window."""
        class _Gone:
            def get_caret_rect(self):
                raise RuntimeError("stale")

            def get_rect(self):
                raise RuntimeError("stale")

        assert caret_anchor(_Gone()) is None


class TestClearTheField:
    """A caret is the text; a field is the text plus its padding and border.

    Measured in Finder's search field: the caret is (924.5, 202, 0, 16) inside
    a field at (891, 207, 242, 38) - starting five points *above* the field
    and ending twenty-seven above its bottom. Under that caret is inside the
    box it was typed into."""

    FINDER_CARET = (924.5, 202.0, 0.0, 16.0)
    FINDER_FIELD = (891.0, 207.0, 242.0, 38.0)

    def test_it_reaches_the_bottom_of_the_field(self):
        anchored = caret_anchor(_Element(self.FINDER_CARET, self.FINDER_FIELD))
        assert anchored[1] + anchored[3] == 245.0, "the field's bottom edge"

    def test_the_popup_then_clears_the_field(self):
        anchored = caret_anchor(_Element(self.FINDER_CARET, self.FINDER_FIELD))
        _x, y = place_below((400.0, 200.0), anchored, gap=4.0)
        assert y == 249.0

    def test_the_column_is_still_the_caret_s(self):
        """The one thing the field cannot say, and the reason to read a caret
        at all."""
        anchored = caret_anchor(_Element(self.FINDER_CARET, self.FINDER_FIELD))
        assert anchored[0] == 924.5

    def test_a_document_is_not_a_field(self):
        """Under a text area's bottom edge is nowhere near the caret."""
        caret = (100.0, 300.0, 0.0, 14.0)
        assert caret_anchor(_Element(caret, (90.0, 100.0, 600.0, 800.0))) == caret

    def test_a_caret_already_past_the_field_is_left_alone(self):
        caret = (100.0, 200.0, 0.0, 60.0)
        anchored = caret_anchor(_Element(caret, (90.0, 195.0, 300.0, 30.0)))
        assert anchored == caret


class TestPopupAnchor:

    def test_the_caret_wins(self):
        element = _Element((100.0, 200.0, 0.0, 18.0), (90.0, 190.0, 300.0, 40.0))
        assert popup_anchor(element, (0, 0, 800, 600)) == (
            (100.0, 200.0, 0.0, 30.0), "caret")

    def test_the_control_when_the_caret_is_not_believed(self):
        element = _Element(VSCODE_CARET, VSCODE_ELEMENT)
        assert popup_anchor(element, (0, 0, 800, 600)) == (VSCODE_ELEMENT, "element")

    def test_the_window_when_the_element_has_no_place_either(self):
        assert popup_anchor(_Element(), (0, 0, 800, 600)) == (
            (0, 0, 800, 600), "window")

    def test_a_zero_sized_control_is_not_a_place(self):
        assert popup_anchor(_Element(rect=(10, 10, 0, 0)), (0, 0, 800, 600))[1] \
            == "window"

    def test_a_document_sized_control_is_not_a_place_either(self):
        """Under a full-window text area is neither where you are looking nor
        out of the way. Only the tall ones fail that way - a field is fine,
        which is what keeps Electron applications placeable at all."""
        assert popup_anchor(_Element(rect=(100, 100, 1200, 800)),
                            (0, 0, 800, 600))[1] == "window"

    def test_a_field_is_a_place(self):
        assert popup_anchor(_Element(rect=(343, 680, 473, 19)),
                            (0, 0, 800, 600)) == ((343, 680, 473, 19), "element")

    def test_nowhere_to_point_at(self):
        assert popup_anchor(None) is None


class TestPlaceBelow:

    SCREEN = (0.0, 0.0, 1000.0, 800.0)

    def test_under_it_with_a_gap_and_left_edges_aligned(self):
        assert place_below((200.0, 100.0), (300.0, 400.0, 0.0, 18.0),
                           self.SCREEN, gap=4.0) == (300.0, 422.0)

    def test_it_flips_above_when_there_is_no_room_below(self):
        x, y = place_below((200.0, 100.0), (300.0, 700.0, 0.0, 18.0),
                           self.SCREEN, gap=4.0)
        assert (x, y) == (300.0, 596.0)

    def test_it_does_not_flip_into_a_place_that_is_no_better(self):
        """A popup taller than the screen above the caret would be flipped and
        then clamped straight back, which only moves which end of it covers
        the text."""
        _x, y = place_below((200.0, 700.0), (300.0, 300.0, 0.0, 18.0),
                            self.SCREEN, gap=4.0)
        assert y == 100.0, "clamped, not flipped"

    def test_it_clamps_to_the_right_edge(self):
        x, _y = place_below((200.0, 100.0), (950.0, 400.0, 0.0, 18.0),
                            self.SCREEN)
        assert x == 800.0

    def test_a_screen_that_is_not_a_screen_does_not_clamp(self):
        assert place_below((200.0, 100.0), (950.0, 400.0, 0.0, 18.0),
                           None, gap=4.0) == (950.0, 422.0)


class TestPlaceOverTop:
    """Where a popup goes when there is nothing in the window to point at:
    the title bar, which holds nothing anyone is reading."""

    SCREEN = (0.0, 25.0, 1920.0, 1055.0)

    def test_centred_on_the_top_edge(self):
        assert place_over_top((160.0, 40.0), (400.0, 200.0, 1100.0, 800.0),
                              self.SCREEN) == (870.0, 200.0)

    def test_the_drop_starts_it_below_that_edge(self):
        """Flush with the edge reads as part of the window's frame rather
        than as something laid on top of it."""
        assert place_over_top((160.0, 40.0), (400.0, 200.0, 1100.0, 800.0),
                              self.SCREEN, drop=2.0) == (870.0, 202.0)

    def test_a_window_off_the_left_of_the_screen_is_clamped(self):
        x, _y = place_over_top((160.0, 40.0), (-500.0, 200.0, 300.0, 800.0),
                               self.SCREEN)
        assert x == 0.0

    def test_a_window_whose_title_bar_is_above_the_work_area(self):
        _x, y = place_over_top((160.0, 40.0), (400.0, 0.0, 1100.0, 800.0),
                               self.SCREEN)
        assert y == 25.0, "the menu bar's strip is not ours to draw in"

    def test_without_a_screen_it_is_arithmetic_alone(self):
        assert place_over_top((160.0, 40.0),
                              (400.0, 200.0, 1100.0, 800.0)) == (870.0, 200.0)


class TestReportCaretAnchor:
    """The diagnostic action: press a key inside the application in question
    and it says where a popup would go and why, at INFO."""

    def _run(self, monkeypatch, element, popped, focus_element=...):
        from keyhac.core.keymap import Keymap
        from keyhac.actions import ReportCaretAnchor

        class _Provider:
            def get_focused_element(self):
                return element

        class _Stub:
            _focus_provider = _Provider()
            focus = type("F", (), {
                "element": element if focus_element is ... else focus_element,
                "app_name": "Fake.app"})()

            def pop_balloon(self, name, text, timeout=None, near=None):
                popped.append((text, near))

        monkeypatch.setattr(Keymap, "get_instance", staticmethod(lambda: _Stub()))
        ReportCaretAnchor()()

    def test_it_shows_the_anchor_it_found(self, monkeypatch, caplog):
        popped = []
        element = _Element((100.0, 200.0, 0.0, 18.0), (90.0, 190.0, 300.0, 40.0))
        with caplog.at_level("INFO"):
            self._run(monkeypatch, element, popped)
        assert popped == [("anchor: caret", (100.0, 200.0, 0.0, 30.0))]
        assert "anchor          : caret" in caplog.text

    def test_it_reports_at_info_rather_than_debug(self, monkeypatch, caplog):
        """The whole point of the action: no log level to turn on first."""
        element = _Element((100.0, 200.0, 0.0, 18.0), (90.0, 190.0, 300.0, 40.0))
        with caplog.at_level("INFO"):
            self._run(monkeypatch, element, [])
        assert any(record.levelname == "INFO" and "Caret report" in record.message
                   for record in caplog.records)

    def test_it_says_what_each_way_of_asking_answered(self, monkeypatch, caplog):
        """Which spelling the application answered is the whole diagnosis - it
        is what tells a control with no caret from one whose caret is a lie."""
        class _Detailed(_Element):
            def describe_caret(self):
                return [("AXBoundsForRange(caret, 1)", (100.0, 200.0, 5.0, 18.0)),
                        ("AXBoundsForRange(caret, 0)", VSCODE_CARET)]

        with caplog.at_level("INFO"):
            self._run(monkeypatch,
                      _Detailed((100.0, 200.0, 0.0, 18.0), (90.0, 190.0, 300.0, 40.0)),
                      [])
        assert "AXBoundsForRange(caret, 1)" in caplog.text
        assert "AXBoundsForRange(caret, 0)" in caplog.text

    def test_a_lie_is_reported_as_not_believed(self, monkeypatch, caplog):
        popped = []
        with caplog.at_level("INFO"):
            self._run(monkeypatch, _Element(VSCODE_CARET, VSCODE_ELEMENT), popped)
        assert "not believed" in caplog.text
        assert popped == [("anchor: element", VSCODE_ELEMENT)]

    def test_nothing_focused_names_the_chromium_case(self, monkeypatch, caplog):
        """"No focused element" out of a Chromium application means something
        specific and fixable, and the report says so rather than leaving it as
        a dead end."""
        popped = []
        with caplog.at_level("INFO"):
            self._run(monkeypatch, None, popped, focus_element=None)
        assert "enable_content_access" in caplog.text
        assert popped == [("anchor: no focused element", None)]
