"""Windows platform implementation (ctypes).

STATUS: verified live on Windows - hook consume decisions, injection,
sanity-check re-install, clipboard, send_text, mouse output, window/app
control, UIA focus paths.  See doc/dev/testing.md for what was run
and how.
"""


def create_platform():
    from keyhac.platform.win.hook import WinInputHook
    from keyhac.platform.win.focus import WinFocusProvider
    from keyhac.platform.win.loop import WinEventLoop
    return WinInputHook(), WinFocusProvider(), WinEventLoop()


def acquire_instance_lock():
    from keyhac.platform.win.instance import acquire_instance_lock
    return acquire_instance_lock()


def notify_already_running():
    from keyhac.platform.win.instance import notify_already_running
    notify_already_running()


def offer_config_migration(target_config_path):
    from keyhac.platform.win.migrate import offer_config_migration
    return offer_config_migration(target_config_path)


def worker_thread_context():
    """The COM apartment a walking worker needs - see
    keyhac.platform.win.uielement.com_worker_thread."""
    from keyhac.platform.win.uielement import com_worker_thread
    return com_worker_thread()
