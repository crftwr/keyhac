"""Threaded actions (ported from keyhac-mac keyhac_action.py).

starting()/finished() run under the engine lock (serialized with the hook);
run() executes in a shared single-worker thread pool.
"""

import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from keyhac.core import log

logger = log.getLogger("Action")


class ThreadedAction:
    """Base class for time-consuming key actions.

    Derive and implement starting(), run(), finished().  run() executes in a
    thread pool; starting()/finished() are for light-weight work and run
    under exclusive control with the keyboard hook.
    """

    thread_pool = ThreadPoolExecutor(max_workers=1)

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
        try:
            result = future.result()
            from keyhac.core.keymap import Keymap
            with Keymap.get_instance()._lock:
                self.finished(result)
        except Exception:
            print()
            logger.error(f"Threaded action failed:\n{traceback.format_exc()}")

    def starting(self) -> None:
        """Called immediately when the action triggers (under the engine lock)."""

    def run(self) -> Any:
        """Called in the thread pool; may block."""

    def finished(self, result: Any) -> None:
        """Called after run() (under the engine lock)."""


class LaunchApplication(ThreadedAction):
    """Launch (or activate) an application by name."""

    def __init__(self, app_name: str):
        self.app_name = app_name

    def run(self):
        from keyhac.core.keymap import Keymap
        Keymap.get_instance().app_control.launch(self.app_name)

    def __repr__(self):
        return f'LaunchApplication("{self.app_name}")'


class StartRecordingKeys:
    """Start recording keystrokes into the replay buffer."""

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
    """Play back the recorded keystrokes (re-evaluated by the keymap)."""

    def __call__(self):
        from keyhac.core.keymap import Keymap
        Keymap.get_instance().replay_buffer.playback()


class InputText:
    """Type a literal string into the focused application."""

    def __init__(self, text: str):
        self.text = text

    def __call__(self):
        from keyhac.core.keymap import Keymap
        keymap = Keymap.get_instance()
        with keymap._lock:
            keymap._hook.send_text(self.text)

    def __repr__(self):
        return f'InputText("{self.text}")'
