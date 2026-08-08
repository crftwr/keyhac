"""Threaded actions (ported from keyhac-mac keyhac_action.py).

starting()/finished() run on the event-loop thread under the engine lock
(serialized with the hook); run() executes on a shared pool, concurrently with
other actions.

Two things beyond that lifecycle live here, and both exist because an action of
the kind this framework serves runs for minutes rather than milliseconds: it
can be stopped with Esc, and what it produced is recorded where something can
come back for it (keyhac/core/capture.py).
"""

import contextlib
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from keyhac.core import capture, log

logger = log.getLogger("Action")


class ActionCancelled(BaseException):
    """Raised inside a running action when the user cancels it with Esc.

    **Derived from BaseException rather than Exception, and that is the
    point.** An action of the kind this framework exists for catches
    `Exception` around each item, because partial failure is the thing it is
    built to survive:

    ```python
    for system in self.systems:
        try:
            self._read_system(system, rows)
        except Exception as error:          # <- would swallow a cancellation
            self.failed.append((system["name"], str(error)))
    ```

    Were this an ordinary Exception, pressing Esc there would be recorded as
    "SystemA failed" and the run would carry on to SystemB - the one thing
    cancelling must not do. As a BaseException it passes through every such
    handler while still unwinding the action's `finally` blocks, so progress
    already written stays written.

    Cancellation is KeyboardInterrupt's cousin, not an error. An action never
    needs to know this class exists: `wait_for` raises it, and long actions
    spend most of their time waiting.
    """


#: The action whose run() this thread is executing.  How `wait_for` - a free
#: function that is handed no action - knows whose cancellation to honour.
_current = threading.local()


def current_action():
    """The ThreadedAction running on this thread, or None.

    lazydocs: ignore
    """
    return getattr(_current, "action", None)


class ThreadedAction:
    """Base class for time-consuming key actions.

    Anything slow - network, subprocess, sleeping, heavy computation - must
    not run inline, because a bound function executes inside the keyboard
    hook's deadline.  Derive from this and implement starting(), run() and
    finished() instead.

    Three threads, and which one you are on decides what you may touch.
    starting() and finished() run on the event-loop thread under the engine
    lock: main-thread-only APIs (UI, windows, AX) are allowed there, and they
    should stay light-weight because they hold the lock the keyboard hook
    needs.  run() executes on a worker, where input contexts are allowed but
    windows and AX elements are not.

    Actions run concurrently, so a run() that takes minutes no longer holds up
    every other one.  What is still serialized is what has to be: injected
    keystrokes (one `with ctx:` batch at a time) and the clipboard save and
    restore around a paste.

    **The user can stop a running action with Esc**, and an action needs to
    write nothing for that: `wait_for` raises `ActionCancelled`, and a long
    action spends nearly all its time waiting.  Use `check_cancelled()` in a
    stretch of work that has no wait in it.

    ```python
    class Fetch(ThreadedAction):
        def starting(self):          # main thread, before run
            logger.info("fetching...")
        def run(self):               # worker thread - the slow part
            return do_network_call()
        def finished(self, result):  # main thread, after run
            logger.info(f"got {result}")
    ```
    """

    # More than one worker, so a run() that takes minutes stops holding up
    # every other action in the application - the §2.1 bug that made a
    # tens-of-minutes ETL action freeze an unrelated key binding.
    #
    # The single worker was never what kept concurrent actions safe, which is
    # why raising it is not the hazard it looks like: injected keystrokes are
    # serialized by the engine lock (InputContext.__enter__ takes it), and
    # windows and AX elements are reachable only through
    # call_on_main_thread, which serializes on the loop thread. The one thing
    # the pool's shape *was* protecting is the clipboard save/restore in
    # core/fill.py, and that now holds a lock of its own.
    #
    # What does change: two key bindings that used to queue can now overlap.
    # Each `with ctx:` batch stays atomic, so typing cannot interleave mid-
    # batch, but two typing actions started at once will interleave batches.
    thread_pool = ThreadPoolExecutor(max_workers=None,
                                     thread_name_prefix="keyhac-action")

    #: Actions whose run() has not returned, so Esc can reach them.  A set
    #: rather than one slot: nothing stops two long actions running now.
    _running: set = set()
    _running_lock = threading.Lock()

    @classmethod
    def cancel_all(cls) -> int:
        """Ask every running action to stop.  Returns how many were asked.

        Called from the keyboard hook, so it must stay O(1) in the number of
        actions and do no I/O: setting an Event is the whole of it.

        lazydocs: ignore
        """
        with cls._running_lock:
            running = tuple(cls._running)
        for action in running:
            flag = getattr(action, "_cancel_flag", None)
            if flag is not None:
                flag.set()
        return len(running)

    def cancelled(self) -> bool:
        """True once the user has asked this action to stop.

        Check it in a loop that does not wait - `wait_for` already raises
        `ActionCancelled` on its own, and a loop built out of waits needs
        nothing else.
        """
        flag = getattr(self, "_cancel_flag", None)
        return flag is not None and flag.is_set()

    def check_cancelled(self) -> None:
        """Raise `ActionCancelled` if the user has asked this action to stop.

        For a stretch of work with no wait in it - a long parse, a big write -
        where cancellation would otherwise not be noticed until the next wait.
        """
        if self.cancelled():
            raise ActionCancelled(f"{self!r} was cancelled")

    @property
    def keymap(self):
        """The running Keymap, so an action need not import and look it up."""
        from keyhac.core.keymap import Keymap
        return Keymap.get_instance()

    @property
    def ui(self):
        """The action-facing UI API (`keymap.ui`) - see doc/action_api.md.

        An action's most-used object, so it is one attribute away rather than
        two lines of lookup at the top of every run().
        """
        return self.keymap.ui

    def __repr__(self):
        return f"{type(self).__name__}()"

    def __call__(self):
        from keyhac.core.keymap import Keymap
        keymap = Keymap.get_instance()

        with keymap._lock:
            self.starting()

        future = ThreadedAction.thread_pool.submit(self._run_tracked)
        future.add_done_callback(self._done_callback)

    def _run_tracked(self):
        with self.cancellable():
            return self.run()

    @contextlib.contextmanager
    def cancellable(self, name: str | None = None):
        """Make this action reachable by Esc, and record what it produces.

        `name` is what the run is filed under. A caller that knows it - the
        MCP tool, which was handed a name to look the action up by - passes it
        rather than letting the lookup below go through the Keymap singleton,
        which is not necessarily the registry it came from.

        Both ways of starting an action enter this - `_run_tracked` for a key
        press, and the MCP tool that starts one from a chat window - which is
        what makes both stoppable and both retrievable. A run started by a key
        this morning is the one §15.4 wants readable at noon, and it lands here
        by taking the same path.

        The flag is built here rather than in `__init__` because subclasses
        write their own and do not call `super()` - the documented shape, and
        what all six examples do. Deregistration is here too rather than in
        `_finish`, which is handed to the loop thread and may not have run
        yet when the next Esc arrives.

        lazydocs: ignore
        """
        self._cancel_flag = threading.Event()
        _current.action = self
        with ThreadedAction._running_lock:
            ThreadedAction._running.add(self)

        self._run_record = capture.start_run(name or self._registered_name())
        try:
            with capture.capture(self._run_record.output):
                yield self
        except ActionCancelled:
            self._run_record.finish("cancelled", "stopped with Esc")
            raise
        except BaseException as error:                    # noqa: BLE001
            self._run_record.finish(
                "failed",
                "the action raised:\n" + traceback.format_exc()
                + capture.subprocess_detail(error))
            raise
        else:
            self._run_record.finish("finished")
        finally:
            _current.action = None
            with ThreadedAction._running_lock:
                ThreadedAction._running.discard(self)

    def _registered_name(self) -> str:
        """The name this action was registered under, for the run record.

        Reversed out of the registry rather than stored, so an action that was
        never registered still gets a record - under its repr, which is what a
        key binding has instead of a name.
        """
        try:
            from keyhac.core.keymap import Keymap
            registry = getattr(Keymap.get_instance(), "registered_actions", {})
            for name, action in registry.items():
                if action is self:
                    return name
        except Exception:                                 # noqa: BLE001
            pass
        return repr(self)

    def _done_callback(self, future):
        # add_done_callback fires on the pool thread; hand finished() back to
        # the event-loop thread so it joins starting() there instead of
        # landing wherever run() happened to end.  Unwired (library use,
        # tests) call_on_main_thread runs it inline, as this always did.
        from keyhac.core.keymap import Keymap
        Keymap.get_instance().call_on_main_thread(lambda: self._finish(future))

    def _finish(self, future):
        try:
            result = future.result()
            from keyhac.core.keymap import Keymap
            with Keymap.get_instance()._lock:
                self.finished(result)
        except ActionCancelled:
            # Needs its own arm: BaseException would otherwise sail past the
            # handler below and into the event loop.  finished() is skipped on
            # purpose - it reports a result the action never produced.
            logger.info(f"{self!r} cancelled.")
        except Exception:
            print()
            logger.error(f"Threaded action failed:\n{traceback.format_exc()}")

    def starting(self) -> None:
        """Called on the event-loop thread the moment the action triggers.

        Main-thread-only APIs (UI, windows, AX) are allowed here; it runs
        under the engine lock, so keep it light.
        """

    def run(self) -> Any:
        """Called in the thread pool; may block.

        Returns:
            Anything; it is handed to finished().
        """

    def finished(self, result: Any) -> None:
        """Called on the event-loop thread once run() has returned.

        Main-thread-only APIs are allowed here too.

        Args:
            result: Whatever run() returned.
        """


class LaunchApplication(ThreadedAction):
    """Launch (or activate) an application by name."""

    def __init__(self, app_name: str):
        """Build the action.

        Args:
            app_name: Application to launch, named the way the OS resolves
                it: "Terminal.app" on macOS, an executable name or path on
                Windows.
        """
        self.app_name = app_name

    def run(self):
        """lazydocs: ignore"""
        from keyhac.core.keymap import Keymap
        Keymap.get_instance().app_control.launch(self.app_name)

    def __repr__(self):
        return f'LaunchApplication("{self.app_name}")'


class StartRecordingKeys:
    """Start recording keystrokes into the replay buffer.

    Bind it to a key; the recording is played back by PlaybackRecordedKeys.
    """

    def __call__(self):
        from keyhac.core.keymap import Keymap
        Keymap.get_instance().replay_buffer.start_recording()


class StopRecordingKeys:
    """Stop recording and normalize the buffer."""

    def __call__(self):
        from keyhac.core.keymap import Keymap
        Keymap.get_instance().replay_buffer.stop_recording()


class ToggleRecordingKeys:
    """Toggle keystroke recording."""

    def __call__(self):
        from keyhac.core.keymap import Keymap
        Keymap.get_instance().replay_buffer.toggle_recording()


class PlaybackRecordedKeys:
    """Play back the recorded keystrokes.

    The replayed keys run back through the keymap, so recorded bindings expand
    again on playback.
    """

    def __call__(self):
        from keyhac.core.keymap import Keymap
        Keymap.get_instance().replay_buffer.playback()


class InputText:
    """Type a literal string into the focused application."""

    def __init__(self, text: str):
        """Build the action.

        Args:
            text: The text to type; any characters, not just ones the keyboard
                can produce.
        """
        self.text = text

    def __call__(self):
        from keyhac.core.keymap import Keymap
        keymap = Keymap.get_instance()
        # Through the input context so held modifiers are released around the
        # text and restored after (issue #2: with the triggering modifier still
        # physically down, the injected events became system shortcuts).
        with keymap.get_input_context() as ctx:
            ctx.send_text(self.text)

    def __repr__(self):
        return f'InputText("{self.text}")'
