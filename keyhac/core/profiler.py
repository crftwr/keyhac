"""Advanced > Start Profiling: where a running Keyhac spends its time, for
someone working on Keyhac and for someone writing a config (#323).

A worker thread reads every Python thread's stack every few milliseconds
(``sys._current_frames``). Nothing runs on the main thread, and it is never
stopped, so the keyboard hook keeps its deadline; the sampler only competes for
the GIL. External profilers cannot do this for a signed bundle: the hardened
runtime refuses them the process.

Each sample is weighted by the time since the previous one rather than counted
as one. The sampler needs the GIL to take a sample, so a C call that holds the
GIL for 200 ms would otherwise read as one sample of the code that made it.

A handler of a few milliseconds falls between samples, and a key handler is
exactly what a config author asks about, so key handling is timed as well:
Keymap.on_key_handled reports each event's binding and duration. Only bound
keys are told apart. This report is meant to be attached to an issue, and
per-key counts of unbound keys would be most of what someone typed.

The stacks cannot see time in native code that Python did not call - AppKit
drawing a view from the run loop reads as the event loop waiting. Where the
platform has a sampler (keyhac.platform.native_sampler), it records the native
stacks over the same time, beside the Python ones.
"""

import collections
import os
import sys
import threading
import time

from keyhac.core import diagnostics, log

logger = log.getLogger("Profiler")

#: A capture nobody stopped ends by itself after this long.
LIMIT = 600

#: Between two reads of the stacks.
INTERVAL = 0.005

_CONFIG = "config.py"
_EXTENSIONS = "extensions"

#: The origins that are the user's own code.
_YOURS = (_CONFIG, _EXTENSIONS)

_MAIN_THREAD = "MainThread"


class Origins:
    """Which part of the program a piece of code belongs to.

    Keyhac's own modules and the stdlib are told by module name, not by path:
    a bundle's bytecode is compiled on the build machine and its ``co_filename``
    still says so. config.py and extensions are compiled here, from real paths.
    """

    def __init__(self, config_path: str, data_dir: str):
        self._config = os.path.realpath(config_path)
        self._extensions = os.path.realpath(
            os.path.join(data_dir, "extensions")) + os.sep

    def classify(self, filename: str, module: str) -> str:
        path = filename if filename.startswith("<") else os.path.realpath(filename)
        if path == self._config:
            return _CONFIG
        if path.startswith(self._extensions):
            return _EXTENSIONS
        top = module.partition(".")[0]
        if top == "keyhac":
            return "Keyhac"
        if top == "puikit":
            return "PuiKit"
        if "site-packages" in path:
            return "packages"
        if top in sys.stdlib_module_names:
            return "stdlib"
        return "other"


class Profiler:
    """One capture at a time, into ``<data_dir>/diagnostics/profile-<time>/``:

    - ``summary.txt``: the part to read
    - ``python-stacks.txt``: every stack seen, as ``thread;a;b;c <ms>`` with the
      outermost frame first, the folded format flame-graph tools read
    - ``native-sample.txt``: the native stacks, where the platform has them

    ``on_done(folder, error)`` is called from the worker thread when the report
    is written; the caller takes it to the main thread.
    """

    def __init__(self, data_dir: str, origins: Origins, interval: float = INTERVAL,
                 limit: float = LIMIT, native=None):
        self._data_dir = data_dir
        self._origins = origins
        self.interval, self.limit = interval, limit
        self._native = native
        self._stop = threading.Event()
        self.running = False

    @property
    def stopping(self) -> bool:
        return self.running and self._stop.is_set()

    def start(self, on_done) -> None:
        if self.running:
            logger.info("A profile is already being captured.")
            return
        self.running = True
        self._stop.clear()
        self._keys = collections.defaultdict(lambda: [0, 0.0, 0.0])
        folder = diagnostics.new_folder(self._data_dir, "profile")

        def worker() -> None:
            try:
                os.makedirs(folder, exist_ok=True)
                self._capture(folder)
                error = None
            except Exception as e:
                logger.exception("Profile failed")
                error = e
            self.running = False
            on_done(folder, error)

        threading.Thread(target=worker, name="keyhac-profiler", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def record_key(self, binding: str | None, seconds: float) -> None:
        """Keymap.on_key_handled: runs in the hook, so this is all it does."""
        stat = self._keys[binding]
        stat[0] += 1
        stat[1] += seconds
        stat[2] = max(stat[2], seconds)

    def _capture(self, folder: str) -> None:
        own = {threading.get_ident()}
        native_error = []
        native_thread = None
        if self._native is not None:
            path = os.path.join(folder, "native-sample.txt")

            def native() -> None:
                own.add(threading.get_ident())
                native_error.append(self._native(self._stop, self.limit, path))

            native_thread = threading.Thread(
                target=native, name="keyhac-profiler-native", daemon=True)
            native_thread.start()

        cpu, sampler_cpu, wall = time.process_time(), time.thread_time(), time.monotonic()
        stacks, origins, samples = self._sample(own)
        cpu = time.process_time() - cpu
        sampler_cpu = time.thread_time() - sampler_cpu
        wall = time.monotonic() - wall
        limit_reached = not self._stop.is_set()
        self._stop.set()

        if native_thread is not None:
            # sample symbolicates when it stops, which takes a few seconds.
            native_thread.join(90)
        _Report(stacks, origins, dict(self._keys), samples, cpu, sampler_cpu, wall,
                limit_reached, self.limit, self.interval,
                self._native is not None, native_error[0] if native_error else None,
                ).write(folder)

    def _sample(self, own: set) -> tuple:
        stacks = collections.Counter()
        origins = {}
        names = {}
        names_read = 0.0
        samples = 0
        started = last = time.perf_counter()
        while True:
            stopped = self._stop.wait(self.interval)
            now = time.perf_counter()
            # Also when stopped: if the GIL was held up to here, this last
            # stack is the only thing that will ever account for that time.
            if now - names_read >= 1:
                names_read = now
                names = {t.ident: t.name for t in threading.enumerate()}
            elapsed, last = now - last, now
            frames = sys._current_frames()
            for ident, frame in frames.items():
                if ident not in own:
                    name = names.get(ident, f"thread-{ident}")
                    stacks[name, _stack(frame, origins, self._origins)] += elapsed
            samples += 1
            del frames
            if stopped or now - started >= self.limit:
                return stacks, origins, samples


def _stack(frame, origins: dict, classifier: Origins) -> tuple:
    """Outermost frame first, as (code, line): cheap to take and to compare.
    Names are made at the end, since the sampler holds the GIL while it is here."""
    stack = []
    while frame is not None:
        code = frame.f_code
        if code not in origins:
            origins[code] = classifier.classify(
                code.co_filename, frame.f_globals.get("__name__") or "")
        stack.append((code, frame.f_lineno))
        frame = frame.f_back
    stack.reverse()
    return tuple(stack)


def _frame_text(frame: tuple) -> str:
    code, line = frame
    return f"{code.co_qualname} ({os.path.basename(code.co_filename)}:{line})"


def _function_text(code) -> str:
    return (f"{code.co_qualname} "
            f"({os.path.basename(code.co_filename)}:{code.co_firstlineno})")


class _Report:

    #: How much of each thread summary.txt shows. The rest is in python-stacks.txt.
    INNERMOST = 25
    STACKS_MAIN = 15
    STACKS_OTHER = 5
    YOURS = 25

    def __init__(self, stacks, origins, keys, samples, cpu, sampler_cpu, wall,
                 limit_reached, limit, interval, native_wanted, native_error):
        self.stacks, self.origins, self.keys = stacks, origins, keys
        self.samples, self.cpu, self.sampler_cpu, self.wall = samples, cpu, sampler_cpu, wall
        self.limit_reached, self.limit, self.interval = limit_reached, limit, interval
        self.native_wanted, self.native_error = native_wanted, native_error

    def write(self, folder: str) -> None:
        with open(os.path.join(folder, "python-stacks.txt"), "w", encoding="utf-8") as out:
            for (thread, stack), seconds in self.stacks.most_common():
                frames = ";".join(_frame_text(frame) for frame in stack)
                out.write(f"{thread};{frames} {max(1, round(seconds * 1000))}\n")
        with open(os.path.join(folder, "summary.txt"), "w", encoding="utf-8") as out:
            out.write("\n".join(self._summary()) + "\n")

    def _share(self, seconds: float) -> str:
        share = 100 * seconds / self.wall if self.wall else 0
        return f"{seconds:8.3f} s {share:5.1f}%"

    def _summary(self) -> list[str]:
        lines = diagnostics.header("Keyhac profile")
        lines += [
            f"Captured {self.wall:.1f} s, {self.samples} samples, "
            f"aiming for one every {self.interval * 1000:g} ms",
            f"CPU time of the whole process: {self.cpu:.2f} s "
            f"({100 * self.cpu / self.wall if self.wall else 0:.1f}% of one core), "
            f"of which the sampler: {self.sampler_cpu:.2f} s",
        ]
        if self.limit_reached:
            lines.append(f"Stopped by itself at the limit of {self.limit:g} s.")
        if not self.native_wanted:
            lines.append("Native stacks: not captured on this OS")
        elif self.native_error:
            lines.append(f"Native stacks: could not be captured: {self.native_error}")
        else:
            lines.append("Native stacks: native-sample.txt")
        lines += [
            "",
            "Times are wall-clock, and shares are of the whole capture. A thread that",
            "is waiting still shows as time, in the call it waits in: for the main",
            "thread that is the event loop's own call, and for a worker a queue or",
            "lock. That time is not used.",
        ]
        lines += self._yours()
        lines += self._key_handling()
        for thread in self._threads():
            lines += self._thread(thread)
        return lines

    def _threads(self) -> list[str]:
        totals = collections.Counter()
        for (thread, _), seconds in self.stacks.items():
            totals[thread] += seconds
        return sorted(totals, key=lambda t: (t != _MAIN_THREAD, -totals[t]))

    def _yours(self) -> list[str]:
        functions = collections.Counter()
        deepest = collections.Counter()
        for (thread, stack), seconds in self.stacks.items():
            mine = [frame for frame in stack if self.origins[frame[0]] in _YOURS]
            if mine:
                for code in {code for code, _ in mine}:
                    functions[thread, code] += seconds
                deepest[thread, mine[-1]] += seconds
        lines = ["", "== Your code (config.py and extensions) =="]
        if not functions:
            return lines + [
                "",
                "None of it was on a stack when a sample was taken. A short handler can",
                "fall between samples; the key handling below does not miss it.",
            ]
        lines += ["", "By function, including everything it called:"]
        for (thread, code), seconds in functions.most_common(self.YOURS):
            lines.append(f"{self._share(seconds)}  [{thread}] {_function_text(code)}")
        lines += ["", "By line, the deepest line of your code on the stack:"]
        for (thread, frame), seconds in deepest.most_common(self.YOURS):
            lines.append(f"{self._share(seconds)}  [{thread}] {_frame_text(frame)}")
        return lines

    def _key_handling(self) -> list[str]:
        lines = [
            "", "== Key handling ==", "",
            "Every key event, timed on the main thread inside the hook's deadline. The",
            "OS holds the keyboard until each one returns, and gives up on a hook that",
            "stays too long, so what takes long here belongs in a ThreadedAction.",
            "",
        ]
        if not self.keys:
            return lines + ["No key events during the capture."]
        lines.append(f"{'events':>7} {'total':>10} {'mean':>10} {'slowest':>10}  binding")
        for binding, (count, total, slowest) in sorted(
                self.keys.items(), key=lambda item: -item[1][1]):
            lines.append(
                f"{count:7d} {total * 1000:7.1f} ms {total / count * 1000:7.2f} ms "
                f"{slowest * 1000:7.1f} ms  {binding or '(no binding)'}")
        return lines

    def _thread(self, thread: str) -> list[str]:
        stacks = {stack: seconds for (name, stack), seconds in self.stacks.items()
                  if name == thread}
        origins = collections.Counter()
        innermost = collections.Counter()
        for stack, seconds in stacks.items():
            origins[self.origins[stack[-1][0]]] += seconds
            innermost[stack[-1]] += seconds
        lines = ["", f"== Thread {thread} ==", "", "By origin of the innermost frame:"]
        lines += [f"{self._share(seconds)}  {origin}"
                  for origin, seconds in origins.most_common()]
        lines += ["", "Innermost frames:"]
        lines += [f"{self._share(seconds)}  {_frame_text(frame)}"
                  for frame, seconds in innermost.most_common(self.INNERMOST)]
        limit = self.STACKS_MAIN if thread == _MAIN_THREAD else self.STACKS_OTHER
        lines += ["", "Whole stacks, innermost frame last:"]
        for stack, seconds in sorted(stacks.items(), key=lambda s: -s[1])[:limit]:
            lines += ["", self._share(seconds)]
            lines += [f"    {_frame_text(frame)}" for frame in stack]
        return lines
