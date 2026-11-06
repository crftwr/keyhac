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

    def post(title, text, on_click=None, on_refused=None):
        posted.append((title, text))
        return True

    manager = _manager(post)
    manager.notify("Deploy finished", "All 12 services are up")
    assert posted == [("Deploy finished", "All 12 services are up")]
    assert manager.popped == []


def _refuse(title, text, on_click=None, on_refused=None):
    return False


def _raise(title, text, on_click=None, on_refused=None):
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


def test_a_refusal_after_the_post_shows_the_balloon_once():
    """macOS's answer to the first request arrives after the call returned."""
    answers = []

    def post(title, text, on_click=None, on_refused=None):
        answers.append(on_refused)
        return True

    manager = _manager(post)
    manager.notify("Deploy finished", "All 12 services are up")
    assert manager.popped == []
    answers[0]()
    assert manager.popped == [
        (("Notification", "Deploy finished\nAll 12 services are up", None),
         {"anchor": "corner"})]


def _clicked(on_click):
    handed = []

    def post(title, text, on_click=None, on_refused=None):
        handed.append(on_click)
        return True

    _manager(post).notify("Saved", "report.csv", on_click=on_click)
    return handed[0]


def test_a_click_runs_on_click():
    clicks = []
    _clicked(lambda: clicks.append(1))()
    assert clicks == [1]


def test_with_no_on_click_there_is_nothing_to_run():
    assert _clicked(None) is None


def test_a_threaded_action_is_something_to_click(engine):
    """An instance is called with no arguments, as a key binding calls it, and
    what is slow happens on a worker."""
    from keyhac import ThreadedAction

    import threading
    done = threading.Event()
    threads = []

    class Convert(ThreadedAction):
        def run(self):
            threads.append(threading.current_thread())
            done.set()

    engine(lambda keymap: None)
    _clicked(Convert())()
    assert done.wait(5)
    assert threads[0] is not threading.current_thread()


def test_a_failing_on_click_goes_to_the_log_and_stops_there():
    def fail():
        raise RuntimeError("no such file")

    _clicked(fail)()
