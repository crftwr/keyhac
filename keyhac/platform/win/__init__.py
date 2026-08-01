"""Windows platform implementation (ctypes).

STATUS: verified live on Windows - hook consume decisions, injection,
sanity-check re-install, clipboard, send_text, mouse output, window/app
control, UIA focus paths.  See doc/windows-session.md for what was run
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
