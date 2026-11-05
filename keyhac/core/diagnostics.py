"""What the developer reports (profile, memory) have in common: a folder of
their own under the data directory, and the same few lines of context."""

import os
import sys
import time

#: Roughly when Keyhac started: this module is imported while it starts up.
#: A report needs an uptime, since what it investigates tends to grow with it.
_STARTED = time.monotonic()


def uptime_text() -> str:
    minutes = (time.monotonic() - _STARTED) / 60
    if minutes < 120:
        return f"{minutes:.0f} minutes"
    return f"{minutes / 60:.1f} hours"


def new_folder(data_dir: str, kind: str) -> str:
    """The path of a new report's folder, not created yet:
    ``<data_dir>/diagnostics/<kind>-<time>``."""
    return os.path.join(data_dir, "diagnostics",
                        f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}")


def header(title: str) -> list[str]:
    """The opening lines of a report."""
    import keyhac
    lines = [title, "", f"Keyhac {keyhac.__version__}"]
    try:
        import puikit
        lines.append(f"PuiKit {getattr(puikit, '__version__', '?')}")
    except ImportError:
        pass
    lines.append(f"Python {sys.version.split()[0]} on {sys.platform}")
    lines.append(f"Written {time.strftime('%Y-%m-%d %H:%M:%S')}, "
                 f"{uptime_text()} after Keyhac started")
    return lines
