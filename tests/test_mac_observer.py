"""AX notification subscription (keyhac/platform/mac/observer.py).

Installation only.  Delivery needs a running CFRunLoop and another application
doing something, which is a live pass rather than a suite test - see the record
in doc/dev/testing.md.  What is covered here is the part that broke twice while
being written: PyObjC refuses a plain Python callable for an AX callback, so
the closure and the observer->instance routing have to be exactly right, and a
wrong one fails at creation rather than at delivery.
"""

import os
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")


@pytest.fixture
def trusted():
    import ApplicationServices as AS
    if not AS.AXIsProcessTrusted():
        pytest.skip("no accessibility permission for this interpreter")


def test_observer_installs_and_closes(trusted):
    from keyhac.platform.mac.observer import UIObserver

    observer = UIObserver(os.getpid())
    assert observer.active
    observer.close()
    assert not observer.active
    observer.close()          # idempotent


def test_a_process_with_no_ui_registers_nothing_and_does_not_raise(trusted):
    """The graceful-refusal path: an app that will not take a notification is
    a missed accelerator, never an error - the wait still polls."""
    from keyhac.platform.mac.observer import UIObserver

    with UIObserver(os.getpid()) as observer:
        assert observer.notifications == []
        assert observer.count == 0
        assert not observer.event.is_set()


def test_the_callback_is_a_pyobjc_closure(trusted):
    """AXObserverCreate rejects a plain function, a bound method, a lambda and
    a functools.partial alike ("Callable argument is not a PyObjC closure"),
    which is why _deliver is built by objc.callbackFor and routes through a
    registry instead of closing over the instance."""
    import ApplicationServices as AS
    from keyhac.platform.mac import observer as module

    err, created = AS.AXObserverCreate(os.getpid(), module._deliver, None)
    assert err == 0 and created is not None

    with pytest.raises(TypeError, match="PyObjC closure"):
        AS.AXObserverCreate(os.getpid(), lambda *a: None, None)


def test_notification_routes_to_the_right_instance(trusted):
    """Delivery arrives at a module-level callback and is routed by observer
    ref, so two observers must not share a doorbell."""
    from keyhac.platform.mac.observer import UIObserver, _OBSERVERS, _deliver

    first, second = UIObserver(os.getpid()), UIObserver(os.getpid())
    try:
        # Simulate what the run loop does, without needing one.
        ref = first._observer
        assert _OBSERVERS.get(ref) is first
        _deliver(ref, None, "AXWindowCreated", None)
        assert first.event.is_set() and first.last == "AXWindowCreated"
        assert not second.event.is_set()
        assert first.count == 1 and second.count == 0
    finally:
        first.close()
        second.close()


def test_closing_unregisters_the_route(trusted):
    from keyhac.platform.mac.observer import UIObserver, _OBSERVERS, _deliver

    observer = UIObserver(os.getpid())
    ref = observer._observer
    observer.close()
    assert ref not in _OBSERVERS
    _deliver(ref, None, "AXWindowCreated", None)      # must not raise
    assert not observer.event.is_set()
