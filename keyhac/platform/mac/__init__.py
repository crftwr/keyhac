"""macOS platform implementation (PyObjC)."""


def check_accessibility(prompt: bool = True) -> bool:
    """Check (and optionally prompt for) the Accessibility permission the
    event tap requires."""
    import ApplicationServices as AS
    options = {AS.kAXTrustedCheckOptionPrompt: prompt}
    return bool(AS.AXIsProcessTrustedWithOptions(options))


def accessibility_settings_path() -> str:
    """Where the Accessibility list is, spelled as this macOS spells it:
    System Settings arrived in macOS 13; 12 and earlier have System
    Preferences, with the pane named the other way round."""
    import platform
    try:
        major = int(platform.mac_ver()[0].split(".")[0])
    except ValueError:
        major = 13
    if major >= 13:
        return "System Settings > Privacy & Security > Accessibility"
    return "System Preferences > Security & Privacy > Privacy > Accessibility"


def create_platform():
    from keyhac.platform.mac.hook import MacInputHook
    from keyhac.platform.mac.focus import MacFocusProvider
    from keyhac.platform.mac.loop import MacEventLoop
    return MacInputHook(), MacFocusProvider(), MacEventLoop()


def acquire_instance_lock():
    from keyhac.platform.mac.instance import acquire_instance_lock
    return acquire_instance_lock()


def notify_already_running():
    from keyhac.platform.mac.instance import notify_already_running
    notify_already_running()
