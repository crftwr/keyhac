"""What the macOS system tools can say about this process that Python cannot.

They are Apple-signed with the right to inspect another process, so unlike
py-spy they work on the hardened-runtime bundle. Not every such tool is harmless
to a process running the keyboard hook: doc/dev/design-notes.md ("Developer
reports") has the measurements.
"""

import ctypes
import os
import resource
import signal
import subprocess
import threading

from keyhac.platform.base import ProcessMemory

_SAMPLE = "/usr/bin/sample"
_FOOTPRINT = "/usr/bin/footprint"

#: Milliseconds between `sample`'s stacks. Its default of 1 stopped a test
#: process for up to 18 ms at a time; at 10 it was up to 7.5 ms, which is what
#: a keystroke can meet during a capture.
_SAMPLE_INTERVAL_MS = 10


def sample_native(stop: threading.Event, limit: float, path: str) -> str | None:
    """Write this process's native stacks to ``path`` until ``stop`` is set or
    ``limit`` seconds have passed.

    Returns:
        What went wrong, or None.
    """
    try:
        process = subprocess.Popen(
            [_SAMPLE, str(os.getpid()), str(max(1, int(limit))),
             str(_SAMPLE_INTERVAL_MS), "-file", path],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
    except OSError as e:
        return str(e)
    stop.wait(limit)
    # An interrupted sample writes what it has and exits 0.
    process.send_signal(signal.SIGINT)
    try:
        out, err = process.communicate(timeout=60)
    except subprocess.TimeoutExpired as e:
        process.kill()
        process.communicate()
        return str(e)
    if process.returncode != 0:
        return (err or out).strip()[:500] or f"exit {process.returncode}"
    return None


class _RusageInfoV2(ctypes.Structure):
    """struct rusage_info_v2 of <sys/resource.h>."""
    _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            "user_time", "system_time", "pkg_idle_wkups", "interrupt_wkups",
            "pageins", "wired_size", "resident_size", "phys_footprint",
            "proc_start_abstime", "proc_exit_abstime", "child_user_time",
            "child_system_time", "child_pkg_idle_wkups", "child_interrupt_wkups",
            "child_pageins", "child_elapsed_abstime", "diskio_bytesread",
            "diskio_byteswritten")]


_RUSAGE_INFO_V2 = 2

_libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
_libproc.proc_pid_rusage.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.POINTER(_RusageInfoV2)]
_libproc.proc_pid_rusage.restype = ctypes.c_int


def process_memory() -> ProcessMemory:
    """Physical footprint is what Activity Monitor's Memory column shows, and
    unlike resident size it does not fall when the system compresses or swaps
    pages out, so it is the one that shows accumulation."""
    info = _RusageInfoV2()
    if _libproc.proc_pid_rusage(os.getpid(), _RUSAGE_INFO_V2, ctypes.byref(info)) != 0:
        raise OSError(ctypes.get_errno(), "proc_pid_rusage")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss     # bytes on macOS
    megabytes = lambda n: f"{n / (1024 * 1024):,.1f} MB"
    return ProcessMemory(info.phys_footprint, {
        "Physical footprint": megabytes(info.phys_footprint),
        "Resident": megabytes(info.resident_size),
        "Resident, peak": megabytes(peak),
        "Open file descriptors": str(len(os.listdir("/dev/fd"))),
    })


def write_footprint(path: str) -> str | None:
    """The system's per-category breakdown of this process's memory, into
    ``path``. It is what shows native growth by kind (Core Animation, IOSurface,
    malloc), and among the tools that show that it is the one that does not
    stop the process.

    Returns:
        What went wrong, or None.
    """
    try:
        done = subprocess.run(
            [_FOOTPRINT, str(os.getpid())], capture_output=True, text=True,
            timeout=60, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    if done.returncode != 0:
        return (done.stderr or done.stdout).strip()[:500] or f"exit {done.returncode}"
    with open(path, "w", encoding="utf-8") as out:
        out.write(done.stdout)
    return None
