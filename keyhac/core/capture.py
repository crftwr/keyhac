"""What an action produced, kept where something can come back for it.

Two things live here because they are the same thing seen twice.

**Capture** collects an action's output while it runs - `logger.info`, `print`,
a library's warnings - so a model can read its own failure instead of the
operator copying a traceback out of a console window.

**The run record** keeps that output, and how the run ended, per action. An
action takes minutes; the transport that starts it answers in one message
(the endpoint is POST-only JSON-RPC, so there is no channel to stream
progress over). So starting and collecting are separate steps, and this
is where the second one looks. That also answers the other half: an
action that fails at nine in the morning has its traceback here when somebody
asks about it at noon.

**Memory only, one run per action.** A record holds window titles and element
names, which is the material §9's trace-privacy rules cover, and the answer
there was deliberately conservative. Nothing is written to disk and nothing
accumulates.
"""

from __future__ import annotations

import contextlib
import logging
import sys
import threading
import time

#: Ceiling on what one run keeps. A run that logs a line per row over hundreds
#: of rows would otherwise fill a context window with the middle of its own
#: progress bar; the tail is where the failure is, so that is the end kept.
MAX_CAPTURE = 20_000

#: Captures currently collecting log records. Global rather than per-thread on
#: purpose: an action's `starting()` and `finished()` run on the loop thread
#: while `run()` does not, so a thread filter would drop exactly the two halves
#: that report what happened. Two overlapping runs therefore see each other's
#: log lines, which is a small price - records carry their logger's name - for
#: not losing the halves that say how the run ended.
_captures: list = []

#: The MCP server's own logger, which is the one thing on the `keyhac` tree
#: that is never an action's output: it reports the calls made *about* the
#: action, one line each. A run lasts minutes and a model polls it, so without
#: this every `get_action_result` comes back quoting the calls that read it.
#: Named rather than imported - `keyhac.mcp` imports this module, not the
#: reverse - and matched exactly, since it is produced in exactly one place.
_MCP_LOGGER = "keyhac.MCP"

#: Where `print()` on *this* thread goes. Thread-scoped where the log handler
#: is not, and the difference is not fussiness: a run lasts minutes, and a
#: global stdout tee spent all of them absorbing every unrelated print in the
#: process into whichever action happened to be running. An action's own
#: print() happens on the thread running its run(), so the thread is exactly
#: the right scope. The cost is a print() from starting()/finished() - which
#: run elsewhere - and those log instead.
_stream_target = threading.local()

#: Guards the stream swap only. The list needs no lock - append and remove are
#: atomic - but two runs starting together could both believe they were first
#: and install a tee over a tee.
_captures_lock = threading.Lock()


class Bounded:
    """A buffer that keeps the last MAX_CAPTURE characters and says so."""

    def __init__(self):
        self._parts: list[str] = []
        self._size = 0
        self.dropped = 0

    def write(self, text: str) -> int:
        self._parts.append(text)
        self._size += len(text)
        while self._size > MAX_CAPTURE and len(self._parts) > 1:
            gone = self._parts.pop(0)
            self._size -= len(gone)
            self.dropped += len(gone)
        return len(text)

    def getvalue(self) -> str:
        body = "".join(self._parts)
        if self.dropped:
            return (f"[{self.dropped} earlier characters dropped - this is the "
                    f"tail of the run]\n{body}")
        return body


class _Handler(logging.Handler):
    def __init__(self, buffer: Bounded):
        super().__init__()
        self.buffer = buffer

    def emit(self, record):
        self.buffer.write(self.format(record) + "\n")


class _Tee:
    """`sys.stdout`, plus every active capture.

    Wraps rather than replaces, because print() must keep reaching the console
    window - the operator watching it is not who capture is for.
    """

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def write(self, text) -> int:
        buffer = getattr(_stream_target, "buffer", None)
        if buffer is not None:
            buffer.write(str(text))
        return self._wrapped.write(text)

    def flush(self) -> None:
        self._wrapped.flush()

    def isatty(self) -> bool:
        return False

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


@contextlib.contextmanager
def capture(buffer: Bounded | None = None):
    """Collect everything the enclosed code logs or prints.

    Three kinds of output used to reach the console window and not the model;
    two of them now do both.

    - **print()**, which the shipped config.py template teaches on the same
      line as the logger. Teed rather than redirected, so it still reaches the
      console.
    - **Loggers outside the `keyhac` tree** - `getLogger(__name__)` in a module
      under `extensions/`, and any library the action imports.
    - **Subprocess stderr** is the one this cannot reach: a child writes to a
      real file descriptor, not to Python's `sys.stderr`. It survives only on
      the exception, which is why callers surface `CalledProcessError.stderr`
      separately and the skill says to pass `capture_output=True`.
    """
    buffer = buffer if buffer is not None else Bounded()
    handler = _Handler(buffer)
    handler.setFormatter(
        logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    handler.addFilter(lambda record: record.name != _MCP_LOGGER)

    # Both, and it is not belt-and-braces. `keyhac` is configured with
    # propagate=False (core/log.py), so a record from the documented
    # getLogger() never reaches root - attaching only there captured nothing.
    # Because it does not propagate, nothing is emitted twice either.
    loggers = (logging.getLogger(), logging.getLogger("keyhac"))
    for target in loggers:
        target.addHandler(handler)

    # A root handler only sees what root's level admits, and the default is
    # WARNING - which would drop every logger.info() an action writes. INFO
    # rather than DEBUG: DEBUG would pull in the debug chatter of every library
    # the action imports, and a model is reading this.
    level = loggers[0].level
    if level == logging.NOTSET or level > logging.INFO:
        loggers[0].setLevel(logging.INFO)

    previous = getattr(_stream_target, "buffer", None)
    _stream_target.buffer = buffer
    with _captures_lock:
        _captures.append(buffer)
        first = len(_captures) == 1 and not isinstance(sys.stdout, _Tee)
        streams = (sys.stdout, sys.stderr) if first else None
        if first:
            sys.stdout, sys.stderr = _Tee(sys.stdout), _Tee(sys.stderr)
    try:
        yield buffer
    finally:
        _stream_target.buffer = previous
        for target in loggers:
            target.removeHandler(handler)
        loggers[0].setLevel(level)
        with _captures_lock:
            try:
                _captures.remove(buffer)
            except ValueError:
                pass
            if streams is not None and not _captures:
                sys.stdout, sys.stderr = streams


def subprocess_detail(error: BaseException) -> str:
    """What a failed subprocess said, which capture cannot see.

    Saying so when it said nothing is worth more than silence: "returned 1"
    with no reason is where the run-read-fix loop stalls, and the fix is a line
    in the action rather than a mystery.
    """
    output = getattr(error, "stderr", None) or getattr(error, "output", None)
    if isinstance(output, bytes):
        output = output.decode("utf-8", "replace")
    if output:
        return f"\nthe subprocess wrote to stderr:\n{output.strip()}\n"
    if getattr(error, "returncode", None) is not None:
        return ("\n(the subprocess left no stderr here - it was run without "
                "capture_output=True, so what it said went to the terminal "
                "Keyhac was started from and nowhere this can reach)\n")
    return ""


# -- the run record ----------------------------------------------------------

#: The last run of each action, by the name it was registered under.  One
#: entry per action: a second run replaces the first rather than piling up.
_runs: dict = {}
_runs_lock = threading.Lock()

#: Signalled whenever any run ends, so a reader waiting for one is woken
#: instead of polling for it.
_finished = threading.Condition()


class ActionRun:
    """How one run of one action went, and what it said while going."""

    def __init__(self, name: str):
        self.name = name
        self.status = "running"
        self.output = Bounded()
        self.detail: str | None = None      # traceback, or why it stopped
        self.started = time.monotonic()
        self.ended: float | None = None

    @property
    def running(self) -> bool:
        return self.status == "running"

    @property
    def seconds(self) -> float:
        return (self.ended or time.monotonic()) - self.started

    def finish(self, status: str, detail: str | None = None) -> None:
        self.status = status
        self.detail = detail
        self.ended = time.monotonic()
        with _finished:
            _finished.notify_all()

    def report(self, level: str | None = None, tail: int | None = None) -> str:
        """The whole of what a caller came back for.

        Args:
            level: Lowest log severity to include - "DEBUG", "INFO", "WARNING",
                "ERROR" or "CRITICAL".  None keeps everything.  Filters only
                captured *log* lines (recognised by the "LEVEL [logger]" shape
                the capture formatter writes); print() output, the status head
                and the traceback always come through.
            tail: Only the last `tail` lines of the output.  None keeps all of
                it.

        Both cuts announce themselves in the output, so a caller reading a
        filtered report knows there was more and how to get it.
        """
        head = (f"{self.name}: {self.status} after {self.seconds:.1f}s"
                if not self.running else
                f"{self.name}: still running after {self.seconds:.1f}s")
        body = _trimmed(self.output.getvalue().strip(), level, tail)
        parts = [head]
        if body:
            parts.append(body)
        if self.detail:
            parts.append(self.detail.strip())
        if self.running:
            parts.append("(call get_action_result again for the rest, or "
                         "cancel_action to stop it)")
        return "\n\n".join(parts)


#: Severities in order, as the capture formatter opens a log line with them
#: ("DEBUG [keyhac.Keymap] PASSTHRU : ...").  What `report(level=)` names and
#: what the line filter matches on.
_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _trimmed(body: str, level: str | None, tail: int | None) -> str:
    """`body` with low-severity log lines dropped and only the tail kept.

    Issue #71: a run that types its way through a form leaves thousands of
    keymap DEBUG lines around the two INFO lines that say what happened, and
    the model reading the report pays for all of them.  Filtered at read time
    rather than capture time, so asking again with `level="DEBUG"` can still
    produce the lines an INFO-level read hid.

    Matched on the "LEVEL [" opening the capture formatter writes.  A
    multi-line log record loses only its first line - the continuation lines
    are indistinguishable from print() output, which must never be dropped.
    """
    notes = []
    if level is not None:
        name = str(level).upper()
        if name not in _LEVEL_NAMES:
            raise ValueError(f"level must be one of "
                             f"{', '.join(_LEVEL_NAMES)}, not {level!r}")
        drop = tuple(f"{below} [" for below in
                     _LEVEL_NAMES[:_LEVEL_NAMES.index(name)])
        if drop and body:
            lines = body.splitlines()
            kept = [line for line in lines if not line.startswith(drop)]
            if len(kept) < len(lines):
                notes.append(f"[{len(lines) - len(kept)} log line(s) below "
                             f"{name} hidden - level='DEBUG' returns "
                             f"everything]")
                body = "\n".join(kept)
    if tail is not None:
        if tail < 1:
            raise ValueError("tail must be a positive number of lines")
        lines = body.splitlines()
        if len(lines) > tail:
            notes.append(f"[showing the last {tail} of {len(lines)} lines - "
                         f"omit tail= for all of them]")
            body = "\n".join(lines[-tail:])
    return "\n".join(notes + ([body] if body else []))


def start_run(name: str) -> ActionRun:
    """Record that `name` has begun, replacing whatever it did last time."""
    run = ActionRun(name)
    with _runs_lock:
        _runs[name] = run
    return run


def get_run(name: str) -> ActionRun | None:
    with _runs_lock:
        return _runs.get(name)


def running_names() -> set:
    with _runs_lock:
        return {name for name, run in _runs.items() if run.running}


def wait_for_run(name: str, seconds: float) -> ActionRun | None:
    """Block up to `seconds` for `name`'s run to end, then report either way.

    Waiting rather than returning immediately is what keeps a fast action fast:
    the caller pays two round trips instead of one, but no extra latency. And
    "still running" is a normal answer here rather than a timeout, which is the
    difference between this and a transport that gives up.
    """
    run = get_run(name)
    if run is None:
        return None
    deadline = time.monotonic() + max(0.0, seconds)
    with _finished:
        while run.running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            _finished.wait(remaining)
    return run
