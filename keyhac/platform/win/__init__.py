"""Windows platform implementation (ctypes).

STATUS: written to spec against keyhac-win/pyauto behavior, NOT yet run on
Windows - M1 was developed on macOS.  First Windows session: run
tools/hook_echo.py, then the full app.
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
