"""keymap.notify: a native notification, or a balloon where none can be posted
(issue #255)."""

from types import SimpleNamespace

import pytest

from keyhac.ui.balloon import BalloonManager


def _manager(post):
    manager = BalloonManager(SimpleNamespace(post_notification=post))
    manager.popped = []
    manager.pop = lambda *args, **kwargs: manager.popped.append((args, kwargs))
    return manager


def test_a_posted_notification_shows_no_balloon():
    posted = []

    def post(title, text):
        posted.append((title, text))
        return True

    manager = _manager(post)
    manager.notify("Deploy finished", "All 12 services are up")
    assert posted == [("Deploy finished", "All 12 services are up")]
    assert manager.popped == []


def _refuse(title, text):
    return False


def _raise(title, text):
    raise RuntimeError("no notification center")


@pytest.mark.parametrize("post", [_refuse, _raise])
def test_a_balloon_in_the_corner_says_it_otherwise_and_stays(post):
    manager = _manager(post)
    manager.notify("Deploy finished", "All 12 services are up")
    assert manager.popped == [
        (("Notification", "Deploy finished\nAll 12 services are up", None),
         {"anchor": "corner"})]


def test_a_title_alone_makes_a_balloon_with_no_blank_line():
    manager = _manager(_refuse)
    manager.notify("Deploy finished")
    assert manager.popped[0][0][1] == "Deploy finished"
