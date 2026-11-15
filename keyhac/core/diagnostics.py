"""What the developer reports (profile, memory) have in common: a folder of
their own under the data directory, and the same few lines of context."""

import os
import platform
import sys
import sysconfig
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
    lines.append(f"{python_text()} on {sys.platform}")
    lines.append(f"Written {time.strftime('%Y-%m-%d %H:%M:%S')}, "
                 f"{uptime_text()} after Keyhac started")
    return lines


def python_text(version: str | None = None, free_threaded: bool | None = None,
                gil: bool | None = None, arch: str | None = None) -> str:
    """The Python Keyhac runs on: its version, the CPU architecture it was
    built for, and whether the GIL is off - ``Python 3.15.2 arm64
    (free-threaded, GIL off)``. It is what tells a free-threaded app build
    from one that is not, and an x64 Python running emulated on an ARM
    machine from a native one. A free-threaded build can still run with the
    GIL on (an extension module that does not support running without it
    turns it back on, as does ``PYTHON_GIL=1``), so build and state are both
    told. The arguments stand in for the running interpreter in tests."""
    if version is None:
        version = platform.python_version()
    if arch is None:
        arch = python_arch()
    if free_threaded is None:
        free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    if gil is None:
        gil = getattr(sys, "_is_gil_enabled", lambda: True)()
    if not free_threaded:
        state = "GIL on"
    elif gil:
        state = "free-threaded build, GIL on"
    else:
        state = "free-threaded, GIL off"
    arch = f" {arch}" if arch else ""
    return f"Python {version}{arch} ({state})"


#: The spellings each OS reports, to the one name a user knows the CPU by.
_ARCH_NAMES = {
    "amd64": "x64", "x86_64": "x64", "x64": "x64",
    "arm64": "arm64", "aarch64": "arm64",
    "win32": "x86", "x86": "x86", "i386": "x86", "i686": "x86",
}


def python_arch(platform_tag: str | None = None, machine: str | None = None) -> str:
    """The CPU architecture the running Python was built for: ``x64``,
    ``arm64``, ``x86``, or the OS's own word for anything else.

    On Windows that is the interpreter's build tag (``win-amd64``,
    ``win-arm64``, ``win32``), not ``platform.machine()``: an x64 Python
    emulated on an ARM64 machine must say x64, and the build tag is fixed
    when the interpreter is built. Elsewhere ``platform.machine()`` already
    names the process's architecture (``x86_64`` under Rosetta), where the
    build tag can be ``universal2``. The arguments stand in for the running
    interpreter in tests."""
    if platform_tag is None:
        platform_tag = sysconfig.get_platform()
    if platform_tag.startswith("win"):
        raw = platform_tag.split("-", 1)[-1]
    else:
        raw = machine if machine is not None else platform.machine()
    return _ARCH_NAMES.get(raw.lower(), raw)
