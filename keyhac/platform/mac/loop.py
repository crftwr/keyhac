"""macOS main event loop - CFRunLoop.

M1: a bare CFRunLoop on the main thread hosts the event tap source and
timers.  In M2 this is replaced by / integrated with PuiKit's NSApplication
loop (the tap source lives on the same run loop either way).
"""

from typing import Callable

import Quartz

from keyhac.platform.base import EventLoop


class MacEventLoop(EventLoop):

    def __init__(self):
        self._timers = set()

    def run(self) -> None:
        Quartz.CFRunLoopRun()

    def stop(self) -> None:
        Quartz.CFRunLoopStop(Quartz.CFRunLoopGetMain())

    def call_later(self, delay_seconds: float, func: Callable[[], None]) -> None:
        def fire(timer, info=None):
            self._timers.discard(timer)
            func()

        timer = Quartz.CFRunLoopTimerCreate(
            None,
            Quartz.CFAbsoluteTimeGetCurrent() + delay_seconds,
            0, 0, 0,
            fire,
            None,
        )
        self._timers.add(timer)  # keep alive until fired
        Quartz.CFRunLoopAddTimer(
            Quartz.CFRunLoopGetMain(), timer, Quartz.kCFRunLoopCommonModes)
