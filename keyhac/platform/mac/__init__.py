"""macOS platform implementation (PyObjC)."""


def check_accessibility(prompt: bool = True) -> bool:
    """Check (and optionally prompt for) the Accessibility permission the
    event tap requires."""
    import ApplicationServices as AS
    options = {AS.kAXTrustedCheckOptionPrompt: prompt}
    return bool(AS.AXIsProcessTrustedWithOptions(options))


def create_platform():
    from keyhac.platform.mac.hook import MacInputHook
    from keyhac.platform.mac.focus import MacFocusProvider
    from keyhac.platform.mac.loop import MacEventLoop
    return MacInputHook(), MacFocusProvider(), MacEventLoop()
