"""Balloon tooltips - frameless topmost no-activate secondary windows.

Restores keyhac-win's popBalloon/closeBalloon (multi-stroke help, macro
status). v1 placement: top-right of the main screen's work area (caret
placement arrives with the platform caret provider).
"""

from puikit import Panel, WindowStyle
from puikit.widgets import Label

from keyhac.core import log

logger = log.getLogger("Balloon")


class BalloonManager:

    def __init__(self, backend):
        self._backend = backend
        self._balloons = {}  # name -> (handle, cancel_timeout or None)

    def pop(self, name: str, text: str, timeout: float = None) -> None:
        """Show (or replace) a named balloon; timeout in seconds."""
        self.close(name)
        width = min(70, max(14, len(text) + 4))
        win = self._backend.create_window(
            width, 3, style=WindowStyle(frameless=True, topmost=True,
                                        activates=False, resizable=False, tool=True))
        panel = Panel(self._backend, window=win)
        panel.add(Label(f"  {text}"), 0, 1, width - 1, 1)
        panel.render()

        # Top-right of the main screen's work area (portable top-left
        # coordinates on both OSes since puikit PR #80)
        try:
            frames = self._backend.screen_frames()
            if frames:
                _full, (vx, vy, vw, vh) = frames[0]
                base_w, _base_h = self._backend.base_size
                win.move_to_px(vx + vw - width * base_w - 24, vy + 24)
        except Exception:
            logger.debug("Balloon placement unavailable; using default position.")

        cancel = None
        if timeout is not None:
            cancel = self._backend.call_later(timeout, lambda: self.close(name))
        self._balloons[name] = (win, cancel)

    def close(self, name: str = None) -> None:
        """Close one balloon, or all when name is None."""
        names = [name] if name is not None else list(self._balloons)
        for n in names:
            entry = self._balloons.pop(n, None)
            if entry is not None:
                win, cancel = entry
                if cancel is not None:
                    cancel()
                if not win.closed:
                    win.close()
