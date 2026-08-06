"""macOS main event loop - CFRunLoop.

M1: a bare CFRunLoop on the main thread hosts the event tap source and
timers.  In M2 this is replaced by / integrated with PuiKit's NSApplication
loop (the tap source lives on the same run loop either way).
"""

import threading
from typing import Callable

import Quartz

from keyhac.platform.base import EventLoop


class MacEventLoop(EventLoop):

    def __init__(self):
        self._timers = set()
        self._lock = threading.Lock()
        self._blocks = set()

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

    def call_on_main_thread(self, callback: Callable[[], None]) -> None:
        main_loop = Quartz.CFRunLoopGetMain()

        def block():
            with self._lock:
                self._blocks.discard(block)
            callback()

        # PyObjC bridges the block over the Python callable, so hold a
        # reference until it runs - the same reason call_later keeps _timers.
        with self._lock:
            self._blocks.add(block)
        Quartz.CFRunLoopPerformBlock(
            main_loop, Quartz.kCFRunLoopCommonModes, block)
        # A loop parked in CFRunLoopRun does not poll for blocks; without the
        # wake-up the callback waits for the next unrelated source to fire.
        Quartz.CFRunLoopWakeUp(main_loop)
