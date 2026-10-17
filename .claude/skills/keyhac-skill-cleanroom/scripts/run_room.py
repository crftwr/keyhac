"""Run clean rooms end to end: build, drive, audit, check, and clean up.

    python .claude/skills/keyhac-skill-cleanroom/scripts/run_room.py            # every runnable case
    python .claude/skills/keyhac-skill-cleanroom/scripts/run_room.py --case 3   # one (repeatable)
    python .claude/skills/keyhac-skill-cleanroom/scripts/run_room.py --task "..."
    python .claude/skills/keyhac-skill-cleanroom/scripts/run_room.py --list

or, the way it is meant to be typed, `make cleanroom` (`CASE=3` for one).

**The room session is non-interactive on purpose.** The rule it kept breaking
was the operator's, not the room's: "answer nothing it asks" is a discipline,
and a discipline fails at the first plausible-sounding question. A `-p` session
has nobody to ask, so the rule holds by construction rather than by willpower -
the only questions it can raise are the ones it writes into QUESTIONS.md, which
is the artefact the exercise exists to produce.

**For the length of a run, a fixture stands in for `~/.keyhac/config.py`.**
The room reaches config.py through Keyhac's own tools, and a key-table case is
*about* editing it - so it must be a known file, and it must not be the
operator's. Theirs is moved aside into a marker directory, a template with a
user's edits on it takes its place, and theirs is put back afterwards whatever
happens: an exception, Ctrl-C and SIGTERM all unwind through the same
`finally`. A marker left by a run that could not unwind refuses the next one
and points at `--restore`.

What the operator still has to supply is Keyhac with its MCP switch on, and,
for a task of your own, the screen. The cases with a fixture open theirs.
"""

import argparse
import datetime
import functools
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types
import urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import friction  # noqa: E402
import outcome  # noqa: E402
from open_room import ROOMS, build_room  # noqa: E402
from outcome import EVIDENCE, FIXTURES, ROOT  # noqa: E402
from score_room import score  # noqa: E402

sys.path.insert(0, str(ROOT))
from keyhac.core import paths  # noqa: E402

#: The sentence the room is opened with, and the whole of what it is told.
#: Anything else said here is something the bundle then did not have to carry.
PROMPT = "Read RULES.md, then do TASK.md."

#: What the room may do. A non-interactive session is granted its permissions
#: up front or it is granted nothing: the first run of this script wrote no
#: action at all, because every tool call in a `-p` session with no grant is
#: denied and the room dutifully reported that it could not write a file.
#:
#: The list is the room's job description - read the bundle, write an action,
#: drive Keyhac, keep notes - and deliberately not `bypassPermissions`.
#:
#: There is no Bash here because `--restricted` removes it outright, and that
#: is the point rather than a limitation: a shell can `cat` its way out of the
#: working directories the file tools are confined to, so the confinement is
#: only real while there is no shell. What the room cannot be stopped from
#: doing by construction - reading another author's action in a folder it must
#: be able to write to - `audit()` catches afterwards.
ALLOW = ["Read", "Glob", "Grep", "Write", "Edit", "TodoWrite", "mcp__keyhac"]

KEYHAC_HOME = pathlib.Path(paths.resolve(None).data_dir)
CONFIG = KEYHAC_HOME / "config.py"
EXTENSIONS = KEYHAC_HOME / "extensions"
DESKTOP = pathlib.Path.home() / "Desktop"

#: Where the operator's config.py (and copies of its write_config backups)
#: wait while the fixture stands in. Its existence at start-up means a run
#: ended without putting them back.
STAND_IN = KEYHAC_HOME / "cleanroom-stand-in"

#: Files Keyhac itself keeps beside config.py. Never collected as something
#: the room made, even when one is new: moving mcp.json away would take the
#: endpoint with it. Matched as prefixes, for an atomic write's temp file.
#: config.py is not among them - its write_config backups, config.py.bak-*,
#: are exactly what is collected - and is skipped by its exact name instead.
KEYHAC_OWN = ("mcp.json", "settings.json", "clipboard.json", "extensions",
              STAND_IN.name)

#: The macOS screens: Safari for pages, Terminal for the build and the deploy.
#: Nothing opens one on Windows yet, so a case with a screen is refused there
#: rather than run against whatever happens to be in front.
SCREENS_SUPPORTED = sys.platform == "darwin"

MCP_IS_OFF = """\
Keyhac's MCP endpoint is not answering.

Tick **AI Integration > MCP Server** in the console or the tray menu. It is off
by default and turns itself off 60 minutes after being ticked, so this is
usually the switch rather than anything broken.

Checked before launching because the alternative is a room that writes an
action, cannot run it, and reports that as a finding about the skill."""


def find_claude() -> str:
    """The `claude` binary, which is routinely not on PATH.

    On this author's machines it lives inside the VS Code extension and PATH
    has never had it, so a script that just calls "claude" fails at the last
    step of a run that has already opened a room.
    """
    if override := os.environ.get("CLAUDE_BIN"):
        return override
    if found := shutil.which("claude"):
        return found
    binary = "claude.exe" if os.name == "nt" else "claude"
    candidates = list(pathlib.Path.home().glob(
        f".vscode/extensions/anthropic.claude-code-*/resources/native-binary/{binary}"))
    local = pathlib.Path.home() / ".claude" / "local" / binary
    if local.exists():
        candidates.append(local)
    if not candidates:
        raise SystemExit(
            "no `claude` binary - put it on PATH or set CLAUDE_BIN=/path/to/claude")
    # Newest install wins; version strings sort wrong (2.1.99 > 2.1.251).
    return str(max(candidates, key=lambda p: p.stat().st_mtime))


def mcp_endpoint() -> dict:
    """Keyhac's live MCP endpoint, or a refusal naming the switch.

    `mcp.json` is written per launch and names the bridge, so reading the
    bridge path from it rather than hard-coding one follows the operator from
    a source checkout to the installed app without being told.
    """
    published = KEYHAC_HOME / "mcp.json"
    if not published.exists():
        raise SystemExit(MCP_IS_OFF)
    try:
        info = json.loads(published.read_text(encoding="utf-8"))
        # Not on Windows: os.kill(pid, 0) there is TerminateProcess with exit
        # code 0 - it would *stop* Keyhac rather than ask whether it runs. The
        # port probe below answers the same question on both.
        if os.name != "nt":
            os.kill(info["pid"], 0)
        port = info["port"]
    except (ValueError, KeyError, OSError):
        raise SystemExit(MCP_IS_OFF)
    with socket.socket() as probe:
        probe.settimeout(1.0)
        if probe.connect_ex(("127.0.0.1", port)) != 0:
            raise SystemExit(MCP_IS_OFF)
    return info


#: Started when no Keyhac is answering: this checkout's Keyhac with the
#: endpoint on and no timeout, quitting when its stdin closes.
DAEMON = pathlib.Path(__file__).resolve().with_name("keyhac_daemon.py")


class KeyhacSession:
    """A Keyhac with its MCP endpoint on, for the length of a ``with`` block.

    One that is already answering is used as it is and left running - it is
    the operator's. Otherwise this checkout's Keyhac is started with the
    endpoint on and no timeout (keyhac_daemon.py), and quit on the way out:
    gracefully, by closing its stdin, and killed only if it has not gone
    within the grace period. Its log goes beside the rooms.
    """

    def __init__(self, log: pathlib.Path, patience: float = 60.0, grace: float = 15.0):
        self.log = log
        self.patience = patience
        self.grace = grace
        self.process: subprocess.Popen | None = None

    def __enter__(self):
        if _endpoint_alive():
            print("keyhac:  using the Keyhac already running, and leaving it running")
            return self
        self.log.parent.mkdir(parents=True, exist_ok=True)
        output = self.log.open("w", encoding="utf-8")
        self._started = time.time()
        self.process = subprocess.Popen(
            [sys.executable, str(DAEMON)], cwd=ROOT, stdin=subprocess.PIPE,
            stdout=output, stderr=subprocess.STDOUT,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        output.close()
        print(f"keyhac:  started this checkout's Keyhac (pid {self.process.pid}, "
              f"log {self.log})")
        deadline = time.monotonic() + self.patience
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                said = self.log.read_text(encoding="utf-8", errors="replace")
                running = "already running" in said
                raise SystemExit(
                    "Keyhac is already running with its MCP server off. Tick "
                    "AI Integration > MCP Server in it, or quit it so the run "
                    "can start its own." if running else
                    f"Keyhac exited ({self.process.returncode}) before its MCP "
                    f"server came up:\n{said[-1500:]}")
            if self._ours_is_answering():
                print("keyhac:  MCP server on, no timeout")
                return self
            time.sleep(0.5)
        self._stop()
        raise SystemExit(
            f"Keyhac did not open its MCP server within {self.patience:.0f}s "
            f"(log: {self.log}). On macOS the first start from a terminal asks "
            f"for the Accessibility permission - grant it and run again.")

    def _ours_is_answering(self) -> bool:
        """The endpoint answers from an mcp.json written since this Keyhac was
        started - not the stale file one killed earlier left behind.

        By time rather than by pid: a venv's python.exe on Windows is a stub
        that runs the real interpreter as its child, so the pid Keyhac
        publishes is never the one this process holds.
        """
        try:
            written = (KEYHAC_HOME / "mcp.json").stat().st_mtime
        except OSError:
            return False
        return written >= self._started - 1.0 and _endpoint_alive()

    def __exit__(self, *exc):
        self._stop()
        return False

    def _stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        try:
            self.process.stdin.close()      # keyhac_daemon.py quits on EOF
        except OSError:
            pass
        try:
            self.process.wait(self.grace)
            print("keyhac:  quit")
        except subprocess.TimeoutExpired:
            # The pid Keyhac published as well as the one held here: on
            # Windows the venv's python.exe is a stub whose child is the real
            # Keyhac, and killing only the stub would orphan it, hook and all.
            try:
                published = json.loads((KEYHAC_HOME / "mcp.json").read_text(encoding="utf-8"))
                if published.get("pid") not in (None, self.process.pid):
                    os.kill(published["pid"], signal.SIGTERM)
            except (OSError, ValueError):
                pass
            self.process.kill()
            self.process.wait()
            print(f"keyhac:  did not quit within {self.grace:.0f}s - killed")


def keyhac_call(tool: str, arguments: dict | None = None) -> str:
    """Call one of Keyhac's MCP tools as the runner, not as the room - to put
    the live keymap in step with config.py around a run."""
    endpoint = mcp_endpoint()
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments or {}}})
    request = urllib.request.Request(
        f"http://127.0.0.1:{endpoint['port']}/", data=body.encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {endpoint['token']}"}, method="POST")
    with urllib.request.urlopen(request, timeout=60) as reply:
        answer = json.loads(reply.read().decode("utf-8"))
    content = answer.get("result", {}).get("content", [])
    return "\n".join(part.get("text", "") for part in content) or str(answer)


# --- the operator's state, stood in for and put back -----------------------------

def refuse_stale_stand_in() -> None:
    if STAND_IN.exists():
        raise SystemExit(
            f"{STAND_IN} exists: a clean-room run ended without putting your "
            f"config.py back. Check that what is in it is yours, then run this "
            f"script with --restore.")


def restore() -> int:
    """Put back what a run that could not unwind left in the stand-in."""
    if not STAND_IN.exists():
        print("nothing to restore")
        return 0
    saved = STAND_IN / "config.py"
    if saved.exists():
        if CONFIG.exists():
            aside = CONFIG.with_name("config.py.cleanroom-fixture")
            os.replace(CONFIG, aside)
            print(f"moved the fixture aside to {aside}")
        os.replace(saved, CONFIG)
        print(f"restored {CONFIG}")
    for backup in sorted(STAND_IN.glob("config.py.bak-*")):
        if not (KEYHAC_HOME / backup.name).exists():
            os.replace(backup, KEYHAC_HOME / backup.name)
            print(f"restored {backup.name}")
    shutil.rmtree(STAND_IN)
    _reload_quietly()
    return 0


def _entries(directory: pathlib.Path) -> set[str]:
    return {p.name for p in directory.iterdir()} if directory.is_dir() else set()


def _reload_quietly() -> str:
    try:
        return keyhac_call("reload_config")
    except (SystemExit, OSError, ValueError) as error:
        return f"could not reload ({error})"


class Stage:
    """The operator's machine, made into the room's starting state for the
    length of a ``with`` block - and given back on the way out.

    On the way in: config.py moves aside and the fixture takes its place, the
    case's input files go on the Desktop, and what is already in every
    directory the room can write to is noted. On the way out, everything new
    in those directories - not only ``*.py``: a state file, a backup, a helper
    package - moves into the room's ``evidence/``, and the operator's own
    files come back. The collection runs inside the ``finally`` as well as the
    restore, because a run interrupted before collecting is the one that
    leaves a test action in the chooser.
    """

    def __init__(self, room: pathlib.Path, case, keep: bool = False,
                 fixture: str = outcome.DEFAULT_FIXTURE):
        self.room = room
        self.case = case
        self.keep = keep
        self.evidence = room / EVIDENCE
        self.fixture_name = fixture
        self.fixture = outcome.fixture_config(fixture)
        self.foreign: set[str] = set()

    def __enter__(self):
        refuse_stale_stand_in()
        for fixture, name in self.case.STAGED:
            if (DESKTOP / name).exists():
                raise SystemExit(
                    f"~/Desktop/{name} already exists, and this case puts its "
                    f"own {name} there. Move yours out of the way first - the "
                    f"run would otherwise write into it.")
        self.before = {"extensions": _entries(EXTENSIONS),
                       "desktop": _entries(DESKTOP),
                       "keyhac": _entries(KEYHAC_HOME)}
        self.foreign = {name for name in self.before["extensions"]
                        if name.endswith(".py")}
        STAND_IN.mkdir()
        self._had_config = CONFIG.exists()
        if self._had_config:
            os.replace(CONFIG, STAND_IN / "config.py")
        # write_config keeps five backups and prunes the oldest, so a room's
        # writes could delete the operator's own. Copies wait in the stand-in
        # and any that went missing come back.
        for backup in KEYHAC_HOME.glob("config.py.bak-*"):
            shutil.copy2(backup, STAND_IN / backup.name)
        CONFIG.write_text(self.fixture, encoding="utf-8", newline="\n")
        for fixture, name in self.case.STAGED:
            shutil.copy2(FIXTURES / fixture, DESKTOP / name)
            print(f"  staged:  ~/Desktop/{name}")
        # The live keymap follows the file, so describe_keymap and read_config
        # tell the room about the same configuration.
        said = _reload_quietly()
        # reload_config's first line is its verdict (issue #197); the log
        # under it is shown only when the verdict is not "loaded".
        verdict = said.splitlines()[0] if said else ""
        trouble = [] if verdict == "loaded" else [
            next((line for line in said.splitlines() if line.startswith("ERROR")),
                 verdict)]
        print(f"  config:  your config.py is aside in {STAND_IN.name}/; the "
              f"{self.fixture_name} fixture is loaded"
              + (f" - {trouble[0]}" if trouble else ""))
        return self

    def __exit__(self, *exc):
        try:
            self.collect()
        finally:
            self._give_back()
        return False

    def collect(self) -> None:
        """Move everything the run made into the room's evidence."""
        self.evidence.mkdir(parents=True, exist_ok=True)
        (self.evidence / "config.before.py").write_text(self.fixture, encoding="utf-8")
        if CONFIG.exists():
            shutil.copy2(CONFIG, self.evidence / "config.after.py")
        moves = [
            # `ActionsSource` imports every module in extensions/, so a test
            # action left behind shows up in the chooser and can break its
            # listing. Copied as evidence, and removed unless --keep.
            (EXTENSIONS, "extensions", self.before["extensions"], not self.keep),
            # The Desktop is a granted directory, so every room can read what
            # every previous room wrote there: case 5 once opened case 1 and
            # 2's finished output on its way to its own task. Moved, not
            # deleted - what a run produced is its evidence.
            (DESKTOP, "desktop", self.before["desktop"], True),
            # write_config's backups of the fixture, and whatever else the room
            # left beside config.py.
            (KEYHAC_HOME, "keyhac", self.before["keyhac"], True),
        ]
        for directory, name, before, remove in moves:
            for entry in sorted(_entries(directory) - before):
                if directory == KEYHAC_HOME and (entry == CONFIG.name
                                                 or entry.startswith(KEYHAC_OWN)):
                    continue
                if directory != KEYHAC_HOME and entry.startswith("."):
                    continue
                if entry == "__pycache__":
                    shutil.rmtree(directory / entry, ignore_errors=True)
                    continue
                source = directory / entry
                target = self.evidence / name / entry
                target.parent.mkdir(parents=True, exist_ok=True)
                if remove:
                    shutil.move(str(source), target)
                elif source.is_dir():
                    shutil.copytree(source, target)
                else:
                    shutil.copy2(source, target)
                print(f"  kept:    {directory.name}/{entry} -> "
                      f"{target.relative_to(self.room)}")

    def _give_back(self) -> None:
        saved = STAND_IN / "config.py"
        if self._had_config:
            os.replace(saved, CONFIG)
        else:
            CONFIG.unlink(missing_ok=True)
        for backup in STAND_IN.glob("config.py.bak-*"):
            if not (KEYHAC_HOME / backup.name).exists():
                os.replace(backup, KEYHAC_HOME / backup.name)
        shutil.rmtree(STAND_IN, ignore_errors=True)
        said = _reload_quietly()
        if said.startswith("could not reload"):
            print(f"  !! your config.py is back on disk but Keyhac still runs the "
                  f"fixture: {said}. Reload it from the tray menu.")
        else:
            print("  config:  yours is back, and reloaded")


# --- the screen ----------------------------------------------------------------

def open_screen(case, events: list) -> None:
    """Put the screen the task talks about in front of the room.

    Pages are served from a copy outside the checkout, because the address bar
    is part of the screen: opened from `examples/actions/fixtures/` the room is
    shown a `file:///…/projects/keyhac/…` URL, which tells it exactly where the
    repository it may not read is. The copy is not put in the room either: a
    real target application does not hand over its own source, and a room that
    can read the fixture's HTML can skip the accessibility tree the skill is
    about.

    What a page reports - an approval, a submission and the form's answer to
    it - lands in ``events``, which is the screen's own record of the run.
    """
    screen = case.SCREEN or {}
    if not screen:
        return
    if not SCREENS_SUPPORTED:
        raise SystemExit(f"{case.NAME} needs its screen put up, and only the "
                         f"macOS runner can open one (Safari / Terminal).")
    if terminal := screen.get("terminal"):
        _open_terminal(*terminal)
        return
    # A run that ended early leaves its fixture windows up - and the next room
    # meets two windows of the same site and spends its run deciding which to
    # drive (2026-10-16, case 1: most of that room's questions were this).
    closed = close_fixture_windows()
    if closed:
        print(f"  closed:  {closed} fixture window(s) left by an earlier run")
    # The whole set: the pages link to each other, and pagination is the task.
    served = pathlib.Path(tempfile.mkdtemp(prefix="keyhac-cleanroom-screen-"))
    shutil.copytree(FIXTURES, served, dirs_exist_ok=True)
    origin = _serve(served, events)
    for name in screen.get("pages", []):
        _load(f"{origin}/{name}", _title_of(served / name), name)
        print(f"  screen:  {name} (Safari, {origin}/{name})")
    time.sleep(1.0)  # Safari needs a moment before the tree is readable.


def close_screen(case) -> None:
    """The room's screen is the runner's to take down, as it was the runner's
    to put up: left open, it is the next room's second window."""
    if SCREENS_SUPPORTED and (case.SCREEN or {}).get("pages"):
        close_fixture_windows()


def close_fixture_windows() -> int:
    """Close the Safari windows showing a fixture page; how many it closed.

    Only windows whose page is served from 127.0.0.1 *and* is one of the
    fixture files - never a window of the operator's, which would have to be
    on that host and named like a fixture to match.
    """
    fixtures = sorted(p.name for p in FIXTURES.glob("*.html"))
    names = ", ".join(f'"{name}"' for name in fixtures)
    script = f'''set fixtures to {{{names}}}
    set closed to 0
    if application "Safari" is running then
        tell application "Safari"
            -- From the last window back: closing one renumbers the rest,
            -- and a forward walk skips the window after each close.
            repeat with i from (count of windows) to 1 by -1
                set w to window i
                try
                    set u to URL of current tab of w
                    if u starts with "http://127.0.0.1:" then
                        repeat with f in fixtures
                            if u ends with ("/" & f) then
                                close w
                                set closed to closed + 1
                                exit repeat
                            end if
                        end repeat
                    end if
                end try
            end repeat
        end tell
    end if
    return closed'''
    asked = subprocess.run(["osascript", "-e", script], capture_output=True,
                           text=True, check=False)
    try:
        return int(asked.stdout.strip() or 0)
    except ValueError:
        return 0


def _title_of(page: pathlib.Path) -> str:
    """The window name the fixture will take once it has loaded."""
    found = re.search(r"<title>([^<]*)", page.read_text(encoding="utf-8"))
    return found.group(1).strip() if found else page.name


def _safari_windows() -> str:
    """Safari's window names, as one string to look for a title in."""
    asked = subprocess.run(
        ["osascript", "-e",
         'tell application "Safari" to get name of every window'],
        capture_output=True, text=True, check=False)
    return asked.stdout


def _load(url: str, title: str, name: str,
          attempts: int = 3, patience: float = 8.0) -> None:
    """Put `url` in a Safari window of its own, and prove that it is there.

    Every part of this was paid for by a run. `make new document with
    properties {URL:...}` returns a document and does not load it. A plain
    two-step sets the URL before the new document is ready. And a `file:///`
    under the temp directory is refused outright - "outside the sandbox" -
    which is why the fixtures are served over a local HTTP origin instead,
    the shape a real target application has anyway.

    Case 4 ran without any of this: Safari sat on its Start Page, the room
    hunted for its task across every application the operator had open -
    reading Gmail and Claude on the way - and wrote an action it could never
    exercise. So the load is waited for by the title the page will take, and a
    screen that never arrives refuses the run instead of buying it.
    """
    for _ in range(attempts):
        subprocess.run(["osascript", "-e", f'''tell application "Safari"
            activate
            set fixture to make new document
            delay 0.5
            set URL of fixture to "{url}"
        end tell'''], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + patience
        while time.monotonic() < deadline:
            if title in _safari_windows():
                return
            time.sleep(0.5)
        print(f"  ! {name} did not load as \"{title}\" - retrying")
    raise SystemExit(
        f"{name} never appeared in Safari as \"{title}\".\n\n"
        f"Safari has: {_safari_windows().strip() or '(no windows)'}\n\n"
        f"A modal sheet left over a window blocks this and shows up nowhere "
        f"else. Clear Safari and run again - refusing rather than opening a "
        f"room that would spend its run looking for a screen that is not "
        f"there, which is what case 4 did.")


def _open_terminal(script: str, title: str, patience: float = 15.0) -> None:
    """Run a fixture script in a Terminal window, and wait for its title.

    Copied out of the checkout first, for the same reason the pages are served
    over HTTP: a terminal shows its command line, and a path under the
    repository tells the room where the source it may not read is.
    """
    served = pathlib.Path(tempfile.mkdtemp(prefix="keyhac-cleanroom-screen-"))
    shutil.copy2(FIXTURES / script, served / script)
    (served / script).chmod(0o755)

    subprocess.run(["osascript", "-e", f'''tell application "Terminal"
        activate
        do script "bash {served / script}"
    end tell'''], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.monotonic() + patience
    while time.monotonic() < deadline:
        named = subprocess.run(
            ["osascript", "-e",
             'tell application "Terminal" to get name of every window'],
            capture_output=True, text=True, check=False).stdout
        if title in named:
            print(f"  screen:  {script} (Terminal, \"{title}\")")
            return
        time.sleep(0.5)
    raise SystemExit(
        f"{script} never appeared as a Terminal window called \"{title}\". "
        f"Terminal may need Automation permission for this shell.")


def _serve(directory: pathlib.Path, events: list) -> str:
    """Serve the fixtures over HTTP, and hand back their origin.

    Not `file://`: Safari refuses one under the temp directory outright, and
    the copy is in the temp directory precisely so that the address bar does
    not read out the checkout's path to the room. An HTTP origin says nothing
    but a port number, and it is what the applications these actions really
    drive look like.

    A POST to `event` is a page reporting what was done on it; it is appended
    to ``events`` and answered with nothing. The server is a daemon thread,
    so it lives exactly as long as the run.
    """
    lock = threading.Lock()

    class Screen(SimpleHTTPRequestHandler):
        def log_message(self, *args, **kwargs):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                event = json.loads(self.rfile.read(length).decode("utf-8"))
            except ValueError:
                event = None
            if isinstance(event, dict):
                with lock:
                    events.append(event)
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Screen, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


# --- the session -----------------------------------------------------------------

def session_command(bridge: str, interactive: bool = False) -> list[str]:
    """The flags that make a room inside the checkout a clean room.

    `--restricted` is the load-bearing one and was measured, not assumed: a
    session started in a room under `cleanroom/` and asked what project it is
    in - reading nothing - answers UNKNOWN with it and names Keyhac2, its two
    languages and its launcher without it. It also hides the checkout's own
    skills (`keyhac-skill-cleanroom` among them, which describes this whole
    exercise and holds the scoring key) and removes Bash, which is how the
    file tools' confinement to the working directories stays true.

    That confinement is why the two `--add-dir`s are here: the room writes its
    action to extensions/ and reads back what it produced on the Desktop, and
    neither is reachable otherwise. Reading *other* actions in that folder is
    still forbidden - a rule the audit checks, since a directory grant cannot
    express "this file but not that one".

    `--permission-mode dontAsk` makes "everything not granted is refused"
    explicit rather than leaving it to `-p`'s default, and
    `--no-session-persistence` keeps the rooms out of the session list - the
    transcript is already kept in the room, so the stored copy is a duplicate.
    """
    command = [
        find_claude(),
        "--restricted",
        "--add-dir", str(EXTENSIONS), "--add-dir", str(DESKTOP),
        # Exactly one server, built from the running Keyhac. Whatever else the
        # operator has connected is not part of the product's public interface.
        "--mcp-config", json.dumps({"mcpServers": {"keyhac": {"command": bridge}}}),
        "--strict-mcp-config",
        # `--restricted` ignores the settings files, this one included, so the
        # room's permissions have to be handed to it directly.
        "--settings", json.dumps({"permissions": {"allow": ALLOW}}),
        "--permission-mode", "dontAsk",
    ]
    if interactive:
        return command
    return command + ["-p", PROMPT, "--no-session-persistence"]


def session_env(room: pathlib.Path) -> dict:
    """The operator's environment, with git stopped at the room.

    The room sits inside this checkout, so a `git log` from it reads the
    repository's history - and the commit titles say what every skill change
    was for. Nothing in the room can run git today (`--restricted` removes
    Bash), but that is one flag, and XeFM's clean room, built on this one, had
    a room open with `git log` on 2026-10-17 (crftwr/xefm#650, issue #203).
    With the ceiling at the room's parent, git answers "not a git repository"
    instead; `audit()` reports any call that tries.

    Not `stdin`: the session's stdin is closed where it is started, because a
    `-p` session with an open one waits three seconds for piped input and
    writes a warning to the top of the transcript.
    """
    return dict(os.environ, GIT_CEILING_DIRECTORIES=str(room.resolve().parent))


def printable_command(room: pathlib.Path, bridge: str) -> str:
    """The same session, for a person to paste - flags and all.

    Printing a bare `cd <room> && claude` is how a room in the checkout gets
    opened without `--restricted`, which is the failure this arrangement trades
    on not happening.
    """
    parts = [shlex.quote(part) for part in session_command(bridge, interactive=True)]
    lines, index = [], 0
    while index < len(parts):  # keep each flag on one line with its value
        flag, value = parts[index], parts[index + 1:index + 2]
        if flag.startswith("--") and value and not value[0].startswith("--"):
            lines.append(f"{flag} {value[0]}")
            index += 2
        else:
            lines.append(flag)
            index += 1
    ceiling = shlex.quote(str(room.parent))
    return (f"cd {shlex.quote(str(room))} && \\\n        "
            f"GIT_CEILING_DIRECTORIES={ceiling} \\\n        "
            + " \\\n        ".join(lines))


def run_session(room: pathlib.Path, bridge: str, model: str | None,
                max_turns: int) -> int:
    """Drive the room, teeing the transcript to disk and a digest to stdout."""
    command = session_command(bridge) + [
        "--max-turns", str(max_turns),
        "--output-format", "stream-json", "--verbose",
    ]
    if model:
        command += ["--model", model]

    # Written outside the room and moved in at the end. Left in the room it is
    # a file the room can read, and the first case-2 run did exactly that -
    # grepping its own log for `"type":"text"` to reconstruct the task its
    # prompt referred to. The harness's own record is not part of the room.
    transcript = pathlib.Path(tempfile.gettempdir()) / f"cleanroom-{room.name}.jsonl"
    print(f"  running: {room.name} (transcript -> {transcript})")
    print()
    global _started, _last_event
    _started = _last_event = time.monotonic()
    try:
        with subprocess.Popen(command, cwd=room, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", bufsize=1,
                              env=session_env(room)) as session, \
                transcript.open("w", encoding="utf-8") as log:
            heartbeat = threading.Thread(target=_heartbeat, args=(session,), daemon=True)
            heartbeat.start()
            try:
                for line in session.stdout:
                    log.write(line)
                    _last_event = time.monotonic()
                    _report(line, room)
            except BaseException:
                # Ctrl-C or SIGTERM: the session goes with the run, so nothing
                # is still writing into the directories being collected.
                session.terminate()
                raise
            return session.wait()
    finally:
        if transcript.exists():
            shutil.move(transcript, room / "transcript.jsonl")


#: When the run started, and when it last said anything - the two numbers the
#: heartbeat needs and the digest prints.
_started = _last_event = 0.0

#: Set when the heartbeat finds Keyhac gone. A run that loses the endpoint is
#: inconclusive rather than clean: it measured an outage.
_endpoint_died = False


def _clock() -> str:
    seconds = int(time.monotonic() - _started)
    return f"{seconds // 60:>2}:{seconds % 60:02d}"


def _endpoint_alive() -> bool:
    """Whether Keyhac is still answering, without raising about it."""
    try:
        mcp_endpoint()
        return True
    except SystemExit:
        return False


def _heartbeat(session, every: float = 20.0) -> None:
    """Say the run is alive while it is thinking, and stop it when Keyhac is not.

    A model between tool calls writes nothing at all, so a long turn and a hung
    process look identical - which is the question an operator watching this
    actually has. A line every 20 quiet seconds answers it, and says how quiet
    it has been so a real hang is still visible as a number that keeps growing.

    The endpoint is checked on the same beat because case 5 spent twenty-three
    minutes and three dollars after Keyhac had already died - it segfaulted
    three minutes in, and the room went on writing an action it could no longer
    run, reporting the outage as its finding. Two failed checks in a row rather
    than one, so a blip does not end a good run; a room that cannot reach
    Keyhac cannot measure the skill, and stopping is cheaper than finishing.
    """
    missed = 0
    while session.poll() is None:
        time.sleep(every / 4)
        quiet = time.monotonic() - _last_event
        if quiet >= every:
            print(f"  [{_clock()}] … thinking ({int(quiet)}s since the last step)")
            globals()["_last_event"] = time.monotonic()
            missed = 0 if _endpoint_alive() else missed + 1
            if missed >= 2:
                print(f"  [{_clock()}] !! Keyhac stopped answering - ending the "
                      f"run, which cannot measure anything from here")
                globals()["_endpoint_died"] = True
                session.terminate()
                return


def _hint(tool: str, given: dict, room: pathlib.Path) -> str:
    """The part of a tool call worth one line.

    Paths get their tail, not their head. Six consecutive reads inside the room
    printed as an identical 60-character prefix of the room's own path, which
    is six lines saying nothing - the operator watching could not tell them
    apart or tell whether anything was moving.
    """
    for key in ("file_path", "path", "notebook_path"):
        if value := given.get(key):
            shown = str(value)
            return shown[len(str(room)) + 1:] if shown.startswith(str(room)) else shown
    for key in ("pattern", "command", "app", "action", "title", "role", "text", "name"):
        if value := given.get(key):
            return str(value)[:70]
    return ""


def _report(line: str, room: pathlib.Path = pathlib.Path()) -> None:
    """One line per thing the room did, so a long run is watchable."""
    try:
        event = json.loads(line)
    except ValueError:
        return
    if event.get("type") == "assistant":
        for part in event.get("message", {}).get("content", []):
            if part.get("type") == "tool_use":
                print(f"  [{_clock()}] · {part['name']} "
                      f"{_hint(part['name'], part.get('input', {}), room)}".rstrip())
            elif part.get("type") == "text" and part.get("text", "").strip():
                first = part["text"].strip().splitlines()[0]
                print(f"  [{_clock()}]   {first[:110]}")
    elif event.get("type") == "user":
        # A refusal is the one tool *result* worth a line: it is what a wrong
        # permission list or a reach outside the room looks like from here, and
        # three went past unseen in the first case-2 run. Keyed on `is_error`
        # rather than on the wording - the real one reads "… is outside …;
        # --restricted confines the file tools to the working directories",
        # which matches none of the words you would think to grep for.
        for part in event.get("message", {}).get("content", []):
            if part.get("type") == "tool_result" and part.get("is_error"):
                said = str(part.get("content", "")).replace("\n", " ")
                print(f"  [{_clock()}] ! refused - {said[:120]}")
    elif event.get("type") == "result":
        print()
        print(f"  turns:   {event.get('num_turns', '?')}, "
              f"{event.get('duration_ms', 0) / 1000:.0f}s, "
              f"${event.get('total_cost_usd', 0):.2f}")


# --- the audit -------------------------------------------------------------------

#: Input fields that say where a tool was aimed. Everything else a tool carries
#: - a file's contents, a note, a message - is what it said, not where it went.
AIMED_AT = ("file_path", "path", "notebook_path", "pattern", "command", "glob")

#: A shell command that runs git: at the start, or after a separator.
_RUNS_GIT = re.compile(r"(?:^|[;&|(`]|\$\()\s*(?:\S*/)?git(?:\.exe)?\b")

#: What git answers once GIT_CEILING_DIRECTORIES has stopped it at the room.
_NOT_A_REPOSITORY = "not a git repository"


#: An absolute path in either spelling: /Users/... or C:\Users\... (or C:/...),
#: and \Users\... - rooted on the current drive, which Windows accepts too.
_ABSOLUTE = re.compile(r"(?:[A-Za-z]:[\\/]|[\\/])[^\s\"'`;|&)]+")


def _norm(path: str) -> str:
    """A path in one spelling for comparison: forward slashes, and lowercase on
    Windows, where C:\\Users and c:/users are the same place."""
    path = path.replace("\\", "/")
    return path.lower() if os.name == "nt" else path


def _paths_in(tool_input: dict) -> list[str]:
    """The paths a tool call was pointed at, absolute ones only."""
    aimed = []
    for key in AIMED_AT:
        value = tool_input.get(key)
        if isinstance(value, str):
            value = value.replace("~/", str(pathlib.Path.home()) + "/")
            aimed += _ABSOLUTE.findall(value)
    return aimed


def _isolation_held(transcript: pathlib.Path):
    """Check from the session's own first line that the flags did their job.

    A room inside the checkout is clean because of `--restricted`, and a
    guarantee that rests on a flag should be read back rather than believed.
    The init event says which tools and skills the session actually got, so all
    three of these are answerable from the record: no Bash means restricted
    mode took effect, no `keyhac` slash command means the checkout's own skills
    stayed hidden, and a connected server means the room could really drive
    Keyhac rather than quietly reporting that it could not.
    """
    events = list(outcome.transcript_events(transcript))
    if not events:
        yield "the session produced no output at all"
        return
    # Looked for, not assumed to be first: a rate_limit_event can come ahead
    # of it, and reading line one as the init event disqualified a clean run
    # (2026-10-17) for an MCP server that was in fact connected.
    init = next((e for e in events if e.get("type") == "system"
                 and e.get("subtype") == "init"), None)
    if init is None:
        yield "the session produced no init event"
        return
    if "Bash" in init.get("tools", []):
        yield ("the session had Bash: it was not started with --restricted, so "
               "the checkout's CLAUDE.md was in context from the first token")
    if leaked := [c for c in init.get("slash_commands", []) if "keyhac" in str(c)]:
        yield f"the checkout's own skills were visible to the room: {leaked}"
    servers = {s.get("name"): s.get("status") for s in init.get("mcp_servers", [])}
    if servers.get("keyhac") != "connected":
        yield (f"Keyhac's MCP server was {servers.get('keyhac', 'absent')}, so "
               f"whatever the room reported about running an action is about "
               f"the endpoint being off, not about the skill")


def audit(room: pathlib.Path, foreign: set[str]) -> tuple[list[str], list[str]]:
    """Read the transcript for the rules the room was asked to keep.

    RULES.md forbids the checkout, the installed package and other people's
    actions, and until there was a transcript the only evidence it had been
    obeyed was that it said so. A rule you cannot check is a rule you are
    assuming, and this run's whole output is a claim about what a reader of the
    bundle alone would do.

    What is scanned is where a tool was *pointed* - the path-bearing fields of
    its input - and not the rest of what it typed. Scanning the whole input
    disqualified the first real run: the room had read the screen, Safari's
    address bar showed the fixture's `file:///…` URL because the runner opened
    it out of the checkout, and the room quoted that URL into its own notes as
    the screen it wrote against. Reading a path off the screen and repeating it
    is not reaching for the checkout; it is doing the task.

    Results are not scanned at all, for the same reason one step earlier: a
    window title or an editor's tab is full of paths the room never asked for.

    The room lives *in* the checkout, so "mentions the checkout path" means
    nothing on its own: every legitimate read of the bundle says it too. What
    is checked is a path under the checkout and outside the room.

    **A refusal is not a breach.** The first case-2 run was disqualified for a
    `Glob` of a sibling room that the confinement turned down flat, so nothing
    was read and nothing was learned - the measurement was intact and the
    verdict said otherwise. Reaching and being refused is reported, because it
    is the confinement doing its job in public, but only a call that came back
    with something disqualifies a run.

    Returns:
        What disqualifies the run, and what was refused.
    """
    room = room.resolve()  # every comparison below is against an absolute path
    transcript = room / "transcript.jsonl"
    if not transcript.exists():
        return ["no transcript - the room was never driven from here"], []

    events = list(outcome.transcript_events(transcript))
    refused_ids = {part["tool_use_id"]
                   for event in events if event.get("type") == "user"
                   for part in event.get("message", {}).get("content", [])
                   if part.get("type") == "tool_result" and part.get("is_error")}
    answers = {part["tool_use_id"]: outcome._result_text(part.get("content"))
               for event in events if event.get("type") == "user"
               for part in event.get("message", {}).get("content", [])
               if part.get("type") == "tool_result"}
    breaches = list(_isolation_held(transcript))
    refused = []
    forbidden = {
        "site-packages/keyhac": "the installed package",
        "/applications/keyhac.app" if os.name == "nt" else "/Applications/Keyhac.app":
            "the installed app",
        _norm(str(STAND_IN)): "the operator's own config.py, set aside",
    }
    checkout, inside = _norm(str(ROOT)), _norm(str(room))
    for event in events:
        if event.get("type") != "assistant":
            continue
        for part in event.get("message", {}).get("content", []):
            if part.get("type") != "tool_use":
                continue
            landed = breaches if part.get("id") not in refused_ids else refused
            command = part.get("input", {}).get("command")
            if isinstance(command, str) and _RUNS_GIT.search(command):
                # Refused when git found no repository - the ceiling held -
                # and a breach otherwise: it read this checkout's history.
                answered = answers.get(part.get("id"), "")
                (refused if part.get("id") in refused_ids
                 or _NOT_A_REPOSITORY in answered else breaches).append(
                    f"{part['name']} ran git: {command[:80]}")
            for aimed in _paths_in(part.get("input", {})):
                spelled = _norm(aimed)
                for path, what in forbidden.items():
                    if path in spelled:
                        landed.append(f"{part['name']} reached into {what}: {aimed}")
                if spelled.startswith(checkout) and not spelled.startswith(inside):
                    landed.append(f"{part['name']} reached into the checkout: {aimed}")
                if pathlib.PurePath(aimed.replace("\\", "/")).name in foreign:
                    landed.append(
                        f"{part['name']} read another author's action: {aimed}")
    return sorted(set(breaches)), sorted(set(refused))


def _lost_the_endpoint(room: pathlib.Path) -> bool:
    """Whether the transcript shows Keyhac going away mid-run."""
    transcript = room / "transcript.jsonl"
    if not transcript.exists():
        return False
    return any(call.keyhac and call.is_error and "could not reach Keyhac" in call.result
               for call in outcome.tool_calls(transcript))


def _lost_the_model(room: pathlib.Path) -> bool:
    """Whether the session ended because it could not reach the model.

    Case 7 on 2026-10-17: the machine slept with the room open, woke with no
    network, and the session's last word was "API Error: Can't reach the API
    server". Its result event still says `success` - with `is_error` set - and
    the outcome check, which reads only what the run left behind, passed it.
    A run the model never finished measured the network, not the skill.
    """
    transcript = room / "transcript.jsonl"
    if not transcript.exists():
        return False
    for event in outcome.transcript_events(transcript):
        if event.get("type") == "assistant" and event.get("error"):
            return True
        if event.get("type") == "result" and event.get("is_error"):
            return True
    return False


# --- one case, and all of them ---------------------------------------------------

def _own_task(task: str):
    """A task of the operator's, shaped like a case so it runs the same way:
    no screen of its own, config edits allowed, and nothing to check."""
    return types.SimpleNamespace(
        NAME="task", NUMBER=0, TITLE=task.strip().splitlines()[0][:60],
        PROMPT=task, MUST="", SCREEN=None, STAGED=(), NEEDS=None,
        EDITS_CONFIG=True, check=lambda o: [])


def room_name(case, fixture: str) -> str:
    """A room's directory: the case, and the config it was given unless that
    was the default - two fixtures of one case are two rooms."""
    return case.NAME if fixture == outcome.DEFAULT_FIXTURE else f"{case.NAME}.{fixture}"


def runs(cases: list, fixtures: list[str]) -> list[tuple]:
    """``(case, fixture)`` pairs to run. Only a case that edits config.py is
    run on more than one fixture: an action case reads the config at most,
    and a second room for it would measure the same thing again."""
    return [(case, name) for case in cases
            for name in (fixtures if case.EDITS_CONFIG else [outcome.DEFAULT_FIXTURE])]


def run_case(case, parent: pathlib.Path, args, bridge: str,
             fixture: str = outcome.DEFAULT_FIXTURE) -> dict:
    """Build, drive, collect, audit and check one room."""
    global _endpoint_died
    _endpoint_died = False
    shown = "" if fixture == outcome.DEFAULT_FIXTURE else f" [config: {fixture}]"
    print(f"== {case.NAME}: {case.TITLE}{shown}")
    room = build_room(case.PROMPT, parent / room_name(case, fixture))
    print(f"  room:    {room}")
    events: list = []
    exit_code = None
    with Stage(room, case, keep=args.keep, fixture=fixture) as stage:
        try:
            open_screen(case, events)
            exit_code = run_session(room, bridge, args.model, args.max_turns)
        finally:
            close_screen(case)
            stage.evidence.mkdir(parents=True, exist_ok=True)
            (stage.evidence / "screen-events.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
            (stage.evidence / "run.json").write_text(json.dumps(
                {"case": case.NAME, "platform": outcome.this_platform(),
                 "fixture": fixture}),
                encoding="utf-8")
    if exit_code:
        print(f"  ! the room session exited {exit_code}")

    lost_the_model = _lost_the_model(room)
    inconclusive = _endpoint_died or _lost_the_endpoint(room) or lost_the_model
    breaches, refused = audit(room, stage.foreign)
    for turned_down in refused:
        print(f"  refused (the confinement held): {turned_down}")
    for breach in breaches:
        print(f"  DISQUALIFIED: {breach}")
    found = outcome.Outcome.from_room(room)
    failures = outcome.failures(case, found)
    for failure in failures:
        print(f"  FAIL: {failure}")
    if not breaches and not failures:
        print("  checks: pass")
    if inconclusive:
        what = "the model's API" if lost_the_model else "Keyhac"
        print(f"  INCONCLUSIVE: {what} stopped answering during the run, so what the")
        print("  room reported past that point is about the outage.")
    print()
    stumbles = friction.incidents(room / "transcript.jsonl")
    (room / "FRICTION.md").write_text(friction.report(room, failures), encoding="utf-8")
    questions = score(room, case)
    return {"name": room_name(case, fixture), "title": case.TITLE, "room": room,
            "fixture": fixture,
            "breaches": breaches, "failures": failures, "questions": questions,
            "friction": stumbles, "inconclusive": inconclusive,
            "scored": bool(getattr(case, "MUST", ""))}


def _verdict(r: dict) -> str:
    if r["breaches"]:
        return "DISQUALIFIED"
    if r["inconclusive"]:
        return "INCONCLUSIVE"
    if r["failures"]:
        return "FAIL"
    return "pass" if r["scored"] else "unscored"


def write_findings(parent: pathlib.Path, results: list[dict]) -> pathlib.Path:
    """FINDINGS.md: every improvement point of the run, one section per case.

    What a run is *for* is this list - the outcome check's failures, where the
    room stumbled (friction.py, read off the record), and what it noticed it
    had to guess (QUESTIONS.md) - and it used to be spread across one terminal
    log and a QUESTIONS.md per room. Gathered here, in the run's directory,
    so it can be triaged in one pass and kept: `git add -f` it when it is
    worth keeping, as a room's QUESTIONS.md is.
    """
    lines = [f"# Clean-room findings: {parent.name}", "",
             "Each case's outcome-check failures, the friction read off its "
             "transcript, and the questions it wrote. Triage every item as a "
             "skill gap, out of scope, or tooling (SKILL.md, Score it); only "
             "skill gaps change the skill. Friction that no question mentions "
             "is the gap nobody noticed.", ""]
    for r in results:
        room = r["room"]
        config = outcome.fixture(r.get("fixture", outcome.DEFAULT_FIXTURE))
        lines += [f"## {r['name']} - {_verdict(r)}", "",
                  f"{r['title']}. Config: {config.name} ({config.describe}). "
                  f"Room: `{room}`", ""]
        for breach in r["breaches"]:
            lines.append(f"- **disqualified** - {breach}")
        for failure in r["failures"]:
            lines.append(f"- **outcome check** - {failure}")
        lines += [incident.line() for incident in r["friction"]]
        asked = [line[3:].strip() for line in
                 _read(room / "QUESTIONS.md").splitlines() if line.startswith("## ")]
        lines += [f"- **question** - {q}" for q in asked]
        if not (r["breaches"] or r["failures"] or r["friction"] or asked):
            lines.append("- nothing recorded - zero is not a pass; read the transcript")
        lines.append("")
    findings = parent / "FINDINGS.md"
    findings.write_text("\n".join(lines), encoding="utf-8")
    return findings


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def summary(results: list[dict]) -> int:
    """One line per room - verdict, friction, questions, where it is - and the
    exit code."""
    print("== summary")
    worst = 0
    for r in results:
        verdict = _verdict(r)
        if verdict in ("DISQUALIFIED", "INCONCLUSIVE"):
            worst = 2
        elif verdict == "FAIL":
            worst = max(worst, 1)
        print(f"  {verdict:<12} {r['name']:<30} {len(r['friction']):>2} friction "
              f"{r['questions']:>2} question(s)  {r['room']}")
    print()
    print("  FINDINGS.md beside the rooms is the result: zero questions is not")
    print("  a pass. Triage each item before acting on it - see SKILL.md, Score it.")
    return worst


def main(argv: list[str] | None = None) -> int:
    # Line buffering, because the progress display is the point of it. Piped to
    # a file - a log, a CI job, anything not a terminal - Python block-buffers
    # stdout, and the first thing that disappears is the heartbeat that exists
    # to say the run is alive. A watched run showed nothing for four minutes
    # while its transcript grew to 224KB.
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--case", action="append",
                        help="run only this case - number, file stem or name; "
                             "repeatable. Default: every case that needs no "
                             "screen of the operator's")
    source.add_argument("--task", help="a prompt of your own, intent only")
    source.add_argument("--list", action="store_true", help="list the cases")
    source.add_argument("--restore", action="store_true",
                        help="put back a config.py a run could not")
    parser.add_argument("--fixture", action="append",
                        help="the config.py a key-table case finds - "
                             + ", ".join(outcome.FIXTURE_CONFIGS)
                             + ", or 'all'; repeatable. Default: template. "
                               "Action cases always get the template")
    parser.add_argument("--model", help="model for the room sessions")
    parser.add_argument("--max-turns", type=int, default=150)
    parser.add_argument("--keep", action="store_true",
                        help="leave the action in extensions/ as well")
    parser.add_argument("--dry-run", action="store_true",
                        help="build the rooms and stop, for a session you want "
                             "to drive by hand")
    args = parser.parse_args(argv)

    if args.restore:
        return restore()
    if args.task:
        cases = [_own_task(args.task)]
    else:
        cases = outcome.load_cases(args.case)
    fixtures = args.fixture or [outcome.DEFAULT_FIXTURE]
    if "all" in fixtures:
        fixtures = list(outcome.FIXTURE_CONFIGS)
    for name in fixtures:
        outcome.fixture(name)          # refuses a name that is not one
    if args.list:
        for case in cases:
            why = (f"needs {case.NEEDS}" if case.NEEDS
                   else "macOS screen" if case.SCREEN and not SCREENS_SUPPORTED
                   else "")
            print(f"  {case.NAME:<32} {case.TITLE}" + (f"   [{why}]" if why else ""))
        print()
        print("  configs a key-table case can be given (--fixture):")
        for config in outcome.FIXTURE_CONFIGS.values():
            print(f"  {config.name:<32} {config.describe}")
        return 0
    if not args.case and not args.task:
        # Every case, in order - except those that need a screen only the
        # operator can put up (Slack, their own mail), which run when named.
        skipped = [c for c in cases if c.NEEDS or (c.SCREEN and not SCREENS_SUPPORTED)]
        cases = [c for c in cases if c not in skipped]
        for case in skipped:
            why = case.NEEDS or "a screen only the macOS runner can open"
            print(f"  skipped: {case.NAME} - needs {why}; name it with --case")

    import keyhac
    parent = ROOMS / f"{keyhac.__version__}-{datetime.datetime.now():%Y%m%d-%H%M%S}"
    if args.dry_run:
        # A room driven by hand is driven against whatever Keyhac you have up:
        # nothing here starts one, because nothing here would stop it again.
        bridge = mcp_endpoint()["bridge"]
        for case, fixture in runs(cases, fixtures):
            print(f"  room:    {build_room(case.PROMPT, parent / room_name(case, fixture))}")
        print()
        print(f"    {printable_command(parent / '<room>', bridge)}")
        print()
        print(f"  and say only:  {PROMPT}")
        print("  Every flag there is load-bearing: a plain `claude` in that")
        print("  directory reads the checkout's CLAUDE.md, and the room's own")
        print("  CLAUDE.md is there to tell such a session to stop.")
        print("  Your config.py is NOT swapped for the fixture in a dry run.")
        return 0

    refuse_stale_stand_in()
    find_claude()
    # SIGTERM unwinds like Ctrl-C, so Stage puts the operator's config back,
    # collects what the room made and quits the Keyhac this run started,
    # rather than dying with all three undone.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    print(f"rooms:   {parent}")
    results = []
    try:
        with KeyhacSession(parent / "keyhac.log"):
            bridge = mcp_endpoint()["bridge"]
            print()
            for case, fixture in runs(cases, fixtures):
                results.append(run_case(case, parent, args, bridge, fixture))
    except KeyboardInterrupt:
        print("\n  interrupted - the room in progress was collected, your "
              "config.py put back, and a Keyhac this run started quit")
    if not results:
        return 130
    findings = write_findings(parent, results)
    print(f"  findings: {findings}")
    return summary(results)


if __name__ == "__main__":
    raise SystemExit(main())
