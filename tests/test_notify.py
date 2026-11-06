"""keymap.notify: a native notification, and what a click on it runs (issue #255)."""

from types import SimpleNamespace

import pytest

from keyhac.ui.balloon import BalloonManager


def _manager(post):
    manager = BalloonManager(SimpleNamespace(post_notification=post))
    manager.popped = []
    manager.pop = lambda *args, **kwargs: manager.popped.append((args, kwargs))
    return manager


def test_a_notification_is_handed_to_the_backend():
    posted = []

    def post(title, text, on_click=None):
        posted.append((title, text))
        return True

    manager = _manager(post)
    manager.notify("Deploy finished", "All 12 services are up")
    assert posted == [("Deploy finished", "All 12 services are up")]
    assert manager.popped == []


def _refuse(title, text, on_click=None):
    return False


def _raise(title, text, on_click=None):
    raise RuntimeError("no notification center")


@pytest.mark.parametrize("post", [_refuse, _raise])
def test_one_that_cannot_be_posted_shows_no_balloon(post):
    manager = _manager(post)
    manager.notify("Deploy finished", "All 12 services are up")
    assert manager.popped == []


def _clicked(on_click):
    handed = []

    def post(title, text, on_click=None):
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
