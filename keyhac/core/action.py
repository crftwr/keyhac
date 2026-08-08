"""Threaded actions (ported from keyhac-mac keyhac_action.py).

starting()/finished() run on the event-loop thread under the engine lock
(serialized with the hook); run() executes in a shared single-worker thread
pool.
"""

import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from keyhac.core import log

logger = log.getLogger("Action")


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

    The pool is a single worker shared by every threaded action, so a run()
    that sleeps or loops delays every other one until it returns.

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

    thread_pool = ThreadPoolExecutor(max_workers=1)

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

        future = ThreadedAction.thread_pool.submit(self.run)
        future.add_done_callback(self._done_callback)

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
