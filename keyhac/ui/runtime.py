"""UI runtime registry: the app's PuiKit backend, set by main() when the
console opens. Chooser/balloon windows are created from it; None while
running headless (--no-ui), in which case UI-dependent actions log an error
instead of opening windows."""

backend = None

#: The app's Settings store, set by main() alongside the backend. The UI
#: state a window remembers between invocations lives here - the chooser's
#: size, which it cannot keep itself because it is a new window every time.
#: None while running headless, where nothing has a window to remember.
settings = None
