"""The two rules a caret-anchored popup needs, on their own.

What the chooser and the balloon do with them is in test_chooser.py; these
pin the arithmetic and the disbelief, which is where the surprises are.
"""

from keyhac.core.anchor import (caret_anchor, place_below, popup_anchor,
                                usable_caret)

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


class TestCaretAnchor:

    def test_a_believable_caret(self):
        element = _Element((100.0, 200.0, 0.0, 18.0), (90.0, 190.0, 300.0, 40.0))
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


class TestPopupAnchor:

    def test_the_caret_wins(self):
        element = _Element((100.0, 200.0, 0.0, 18.0), (90.0, 190.0, 300.0, 40.0))
        assert popup_anchor(element, (0, 0, 800, 600)) == (
            (100.0, 200.0, 0.0, 18.0), "caret")

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
