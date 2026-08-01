"""macOS application control - NSWorkspace / NSRunningApplication."""

import os
import subprocess

import ApplicationServices as AS
from AppKit import NSApplication, NSRunningApplication

from keyhac.platform.base import AppControl

# NSApplicationActivateIgnoringOtherApps is deprecated but still the reliable
# way to bring the previously-focused app back before a programmatic paste.
_ACTIVATE_IGNORING_OTHER_APPS = 1 << 1

#: Same cap the focus provider and window provider use.
AX_MESSAGING_TIMEOUT = 0.1


class MacAppControl(AppControl):

    def activate_pid(self, pid: int) -> bool:
        if pid == os.getpid():
            # Self-activation (the chooser taking keyboard focus). The
            # cooperative route (activateWithOptions:) is ignored on
            # macOS 14+ whenever the caller is not the active app - which is
            # exactly when the chooser needs it - and the AX route below
            # cannot target our own process (the write would be serviced by
            # the very run loop we are blocking). activateIgnoringOtherApps:
            # is the strongest self-activation AppKit offers; if it ever
            # stops being honored, the fallback is keyhac-mac's trick of
            # having LaunchServices activate us via a registered URL scheme.
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            return True
        # Another app (refocus-then-paste): write AXFrontmost on its
        # application element - honored unconditionally for a trusted
        # process, unlike the cooperative request (same reasoning as
        # MacWindow.activate()). Cooperative call kept as fallback.
        element = AS.AXUIElementCreateApplication(pid)
        AS.AXUIElementSetMessagingTimeout(element, AX_MESSAGING_TIMEOUT)
        if AS.AXUIElementSetAttributeValue(element, "AXFrontmost", True) == 0:
            return True
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None:
            return False
        return bool(app.activateWithOptions_(_ACTIVATE_IGNORING_OTHER_APPS))

    def launch(self, app_name: str) -> None:
        subprocess.Popen(["open", "-a", app_name])
