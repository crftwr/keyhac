"""macOS application control - NSWorkspace / NSRunningApplication."""

import subprocess

from AppKit import NSRunningApplication

from keyhac.platform.base import AppControl

# NSApplicationActivateIgnoringOtherApps is deprecated but still the reliable
# way to bring the previously-focused app back before a programmatic paste.
_ACTIVATE_IGNORING_OTHER_APPS = 1 << 1


class MacAppControl(AppControl):

    def activate_pid(self, pid: int) -> bool:
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return False
        return bool(app.activateWithOptions_(_ACTIVATE_IGNORING_OTHER_APPS))

    def launch(self, app_name: str) -> None:
        subprocess.Popen(["open", "-a", app_name])
