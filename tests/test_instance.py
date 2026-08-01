"""Single-instance lock - both platform implementations, each gated to its OS.

The Windows mutex and the macOS flock both behave identically for a second
acquire in the *same* process (CreateMutexW reports ERROR_ALREADY_EXISTS for
an existing name regardless of the owning process; flock is per-open-file,
not per-process), so the contended path is testable without spawning a
process. The cross-process path is exercised live (doc/dev/testing.md).
"""

import sys

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
class TestWinInstanceLock:

    NAME = "crftwr.Keyhac2.SingleInstance.pytest"

    def test_second_acquire_fails_until_released(self):
        from keyhac.platform.win.instance import acquire_instance_lock
        lock = acquire_instance_lock(self.NAME)
        assert lock is not None
        try:
            assert acquire_instance_lock(self.NAME) is None
        finally:
            lock.release()
        lock2 = acquire_instance_lock(self.NAME)
        assert lock2 is not None
        lock2.release()

    def test_release_twice_is_noop(self):
        from keyhac.platform.win.instance import acquire_instance_lock
        lock = acquire_instance_lock(self.NAME)
        lock.release()
        lock.release()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only")
class TestMacInstanceLock:

    def test_second_acquire_fails_until_released(self, tmp_path):
        from keyhac.platform.mac.instance import acquire_instance_lock
        path = str(tmp_path / "instance.lock")
        lock = acquire_instance_lock(path)
        assert lock is not None
        try:
            assert acquire_instance_lock(path) is None
        finally:
            lock.release()
        lock2 = acquire_instance_lock(path)
        assert lock2 is not None
        lock2.release()

    def test_losing_acquire_keeps_holder_pid_note(self, tmp_path):
        import os
        from keyhac.platform.mac.instance import acquire_instance_lock
        path = str(tmp_path / "instance.lock")
        lock = acquire_instance_lock(path)
        assert acquire_instance_lock(path) is None
        with open(path) as f:
            assert f.read() == str(os.getpid())
        lock.release()
