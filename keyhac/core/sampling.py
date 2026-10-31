"""Performance > Capture Sampling Data: ten seconds of where Keyhac's main
thread spends its time, written to a folder the user can attach to an issue
(#292).

A worker thread reads the main thread's Python stack every few milliseconds
(``sys._current_frames``). Nothing stops the main thread or runs on it, so
the keyboard hook is not delayed by the capture. External profilers cannot
do this for a signed bundle: the hardened runtime refuses them the process.

The Python stacks cannot see time spent in native code that Python did not
call - AppKit drawing a view from the run loop reads as the event loop
waiting. On macOS the system's ``/usr/bin/sample`` records the native stacks
over the same ten seconds, beside the Python ones. Windows has no tool like
it built in, so there the capture is Python stacks only.
"""

import collections
import os
import subprocess
import sys
import threading
import time

from keyhac.core import log

logger = log.getLogger("Sampling")

#: How long one capture lasts.
SECONDS = 10

#: Between two reads of the main thread's stack.
INTERVAL = 0.005

_MAC_SAMPLE = "/usr/bin/sample"

#: Roughly when Keyhac started: this module is imported while it starts up.
#: An uptime belongs in the summary - #292's cost looked like it grew with it.
_STARTED = time.monotonic()


def _frame_name(frame) -> str:
    code = frame.f_code
    return f"{code.co_qualname} ({os.path.basename(code.co_filename)}:{frame.f_lineno})"


def _stack(frame) -> tuple:
    names = []
    while frame is not None:
        names.append(_frame_name(frame))
        frame = frame.f_back
    return tuple(reversed(names))


def _versions() -> list[str]:
    import keyhac
    lines = [f"Keyhac {keyhac.__version__}"]
    try:
        import puikit
        lines.append(f"PuiKit {getattr(puikit, '__version__', '?')}")
    except ImportError:
        pass
    lines.append(f"Python {sys.version.split()[0]} on {sys.platform}")
    return lines


def _mac_native(seconds: float, path: str) -> str | None:
    """The native stacks of this process over ``seconds``, into ``path``;
    what went wrong, or None."""
    try:
        done = subprocess.run(
            [_MAC_SAMPLE, str(os.getpid()), str(int(seconds)), "-file", path],
            capture_output=True, text=True, timeout=seconds + 60,
            stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return str(e)
    if done.returncode != 0:
        return (done.stderr or done.stdout).strip()[:500] or f"exit {done.returncode}"
    return None


def _default_native():
    if sys.platform == "darwin" and os.path.exists(_MAC_SAMPLE):
        return _mac_native
    return None


class Capture:
    """One capture at a time, into ``<data_dir>/performance/<time>/``:

    - ``summary.txt``: what was captured, the CPU time it took, and the
      Python frames and stacks seen most often
    - ``python-stacks.txt``: every Python stack seen, outermost frame
      first, with its count (``a;b;c 12``, the folded format flame-graph
      tools read)
    - ``native-sample.txt``: macOS only, ``sample``'s report

    ``on_done(folder, error)`` is called from the worker thread when it is
    finished; the caller takes it to the main thread.
    """

    def __init__(self, data_dir: str, seconds: float = SECONDS,
                 interval: float = INTERVAL, native="default"):
        self._root = os.path.join(data_dir, "performance")
        self.seconds, self.interval = seconds, interval
        self._native = _default_native() if native == "default" else native
        self.running = False

    def start(self, on_done) -> None:
        if self.running:
            logger.info("A capture is already running.")
            return
        self.running = True
        folder = os.path.join(self._root, time.strftime("%Y%m%d-%H%M%S"))
        logger.info(f"Capturing sampling data for {self.seconds:g} seconds...")
        main = threading.main_thread().ident

        def worker() -> None:
            try:
                os.makedirs(folder, exist_ok=True)
                self._capture(folder, main)
                error = None
            except Exception as e:
                logger.exception("Capture failed")
                error = e
            self.running = False
            on_done(folder, error)

        threading.Thread(target=worker, name="keyhac-sampling", daemon=True).start()

    def _capture(self, folder: str, main: int) -> None:
        native_error = []
        native_thread = None
        if self._native is not None:
            path = os.path.join(folder, "native-sample.txt")
            native_thread = threading.Thread(
                target=lambda: native_error.append(self._native(self.seconds, path)),
                name="keyhac-sampling-native", daemon=True)
            native_thread.start()

        stacks = collections.Counter()
        frame = None
        cpu, wall = time.process_time(), time.monotonic()
        deadline = wall + self.seconds
        while time.monotonic() < deadline:
            frame = sys._current_frames().get(main)
            if frame is not None:
                stacks[_stack(frame)] += 1
            time.sleep(self.interval)
        cpu, wall = time.process_time() - cpu, time.monotonic() - wall
        del frame

        if native_thread is not None:
            native_thread.join(self.seconds + 90)
        self._write(folder, stacks, cpu, wall,
                    native_error[0] if native_error else None)

    def _write(self, folder, stacks, cpu, wall, native_error) -> None:
        total = sum(stacks.values())
        with open(os.path.join(folder, "python-stacks.txt"), "w", encoding="utf-8") as out:
            for stack, count in stacks.most_common():
                out.write(f"{';'.join(stack)} {count}\n")

        innermost = collections.Counter()
        for stack, count in stacks.items():
            innermost[stack[-1]] += count

        def share(count):
            return f"{count:6d}  {100 * count / total:5.1f}%" if total else f"{count:6d}"

        uptime = time.monotonic() - _STARTED
        lines = [
            "Keyhac sampling data",
            "",
            *_versions(),
            f"Captured {time.strftime('%Y-%m-%d %H:%M:%S')}, "
            f"{uptime / 60:.0f} minutes after Keyhac started",
            f"{wall:.1f} s of the main thread, {total} samples, "
            f"one every {self.interval * 1000:g} ms",
            f"CPU time of the whole process: {cpu:.2f} s ({100 * cpu / wall:.1f}% of one core)",
        ]
        if self._native is None:
            lines.append("Native stacks: not captured on this OS")
        elif native_error:
            lines.append(f"Native stacks: could not be captured: {native_error}")
        else:
            lines.append("Native stacks: native-sample.txt")
        lines += [
            "",
            "A main thread that is idle is waiting in the event loop: its innermost",
            "frame is the loop's own call, and time spent there is time not used.",
            "",
            "Innermost frames, most often first:",
            *(f"{share(count)}  {name}" for name, count in innermost.most_common(30)),
            "",
            "Whole stacks, most often first (innermost frame last):",
        ]
        for stack, count in stacks.most_common(15):
            lines.append("")
            lines.append(share(count))
            lines += [f"    {name}" for name in stack]
        with open(os.path.join(folder, "summary.txt"), "w", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
