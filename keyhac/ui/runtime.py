"""UI runtime registry: the app's PuiKit backend, set by main() when the
console opens. Chooser/balloon windows are created from it; None while
running headless (--no-ui), in which case UI-dependent actions log an error
instead of opening windows."""

backend = None
