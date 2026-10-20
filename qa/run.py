"""Keyhac's quality-loop adapter: the scenarios in qa/scenarios against the skill bundle.

    make qa                          # every scenario that needs no screen of yours
    make qa CASE=11                  # one scenario (repeatable: CASE="11 12")

`make qa` is `quality-loop run --foreground`; qa/quality-loop.toml names this
file as the adapter, so `quality-loop run 11` (in the background), `quality-loop watch`, `quality-loop findings` and the conductor
skill in .claude/skills/quality-loop all reach it the same way.

Each scenario gets a room holding this version's bundle and nothing else, an
operator agent that puts up its screen and afterwards reports what it finds,
and this checkout's Keyhac with its MCP endpoint on.

**Nothing of yours is touched.** The run's Keyhac lives in a home of its own,
a temporary directory: its HOME points there, so `~/.keyhac` (config.py,
extensions/, settings, the endpoint file) and `~/Desktop` are the run's, and
a fixture config stands in for config.py there. Your ~/.keyhac is never set
aside, so there is nothing to put back after a run that died. The cost: two
Keyhacs cannot both hold the keyboard, so yours has to be quit for the length
of a run, and the run holds your Keyhac's single-instance lock so it cannot
start halfway through.

The result is FINDINGS.md beside the rooms: problems with severities, no
verdict. The design is crftwr/keyhac#205; the harness is quality-loop
(crftwr/quality-loop). What is Keyhac's own is here: the bundle, the rules,
what a room may reach, the stand-in, the screens and the evidence tools.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import configs  # noqa: E402
import keyhac  # noqa: E402
from quality_loop import adapter, audit, runner, screens, session, standin  # noqa: E402

FIXTURES = ROOT / "examples" / "actions" / "fixtures"

#: What a room may do: read the bundle, write an action, drive Keyhac, keep
#: notes. No Bash - `--restricted` has none, and that is what makes the file
#: tools' confinement real.
ALLOW = ["Read", "Glob", "Grep", "Write", "Edit", "TodoWrite", "mcp__keyhac"]

YOURS_IS_RUNNING = """Your Keyhac is running. Quit it for the length of the run: the run starts
Keyhac of its own, in a home of its own, and two Keyhacs cannot both hold the
keyboard. Nothing in your ~/.keyhac is touched."""


def _title(page: str) -> str:
    import re
    text = (FIXTURES / page).read_text(encoding="utf-8")
    found = re.search(r"<title>([^<]*)", text)
    return found.group(1).strip() if found else page


#: Every screen a scenario may ask for, by name.
SCREENS = {
    "systema": screens.Page("systema_1.html", title=_title("systema_1.html")),
    "systemb": screens.Page("systemb_1.html", title=_title("systemb_1.html")),
    "submit-form": screens.Page("submit.html", title=_title("submit.html")),
    "approval-queue": screens.Page("dialog.html", title=_title("dialog.html")),
    "preferences": screens.Page("settings.html", title=_title("settings.html")),
    "notes": screens.Page("notes.html", title=_title("notes.html")),
    "build-failure": screens.Terminal("build_failure.py", title="build - acme"),
    "deploy": screens.Terminal("deploy.py", title="deploy - acme"),
}


class Home:
    """The run's own home: HOME for its Keyhac, and so its ~/.keyhac and
    ~/Desktop. A temporary directory, outside the checkout (which a room must
    not reach) and outside the run's directory (likewise)."""

    def __init__(self):
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="keyhac-qa-home-")).resolve()
        self.keyhac = self.root / ".keyhac"
        self.config = self.keyhac / "config.py"
        self.extensions = self.keyhac / "extensions"
        self.desktop = self.root / "Desktop"
        #: Where the stand-in keeps the previous fixture between rooms: in the
        #: home, but out of the room's reach (~/.keyhac and Desktop only).
        self.stand_in = self.root / "stand-in"
        for directory in (self.extensions, self.desktop):
            directory.mkdir(parents=True)

    def environment(self) -> dict:
        """HOME for a process that should see this home as ~. USERPROFILE is
        what ~ means to Python on Windows."""
        return {"HOME": str(self.root), "USERPROFILE": str(self.root)}

    def remove(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


#: Files Keyhac keeps beside config.py, never collected as something the room
#: made: moving mcp.json away would take the endpoint with it.
KEYHAC_OWN = ("mcp.json", "settings.json", "clipboard.json", "extensions",
              "instance.lock")


# --- Keyhac itself -----------------------------------------------------------------

def endpoint(home: Home) -> dict | None:
    """The run's Keyhac's live MCP endpoint, or None."""
    published = home.keyhac / "mcp.json"
    try:
        info = json.loads(published.read_text(encoding="utf-8"))
        # Not os.kill(pid, 0) on Windows: there it is TerminateProcess.
        if os.name != "nt":
            os.kill(info["pid"], 0)
        with socket.socket() as probe:
            probe.settimeout(1.0)
            if probe.connect_ex(("127.0.0.1", info["port"])) != 0:
                return None
        return info
    except (OSError, ValueError, KeyError):
        return None


def keyhac_call(home: Home, tool: str, arguments: dict | None = None) -> str:
    """Call one of Keyhac's MCP tools as the runner, not as the room."""
    info = endpoint(home)
    if info is None:
        return "could not reload (Keyhac is not answering)"
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": tool, "arguments": arguments or {}}})
    request = urllib.request.Request(
        f"http://127.0.0.1:{info['port']}/", data=body.encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {info['token']}"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as reply:
            answer = json.loads(reply.read().decode("utf-8"))
    except OSError as error:
        return f"could not reload ({error})"
    content = answer.get("result", {}).get("content", [])
    return "\n".join(part.get("text", "") for part in content) or str(answer)


def hold_your_lock():
    """Your Keyhac's single-instance lock, held for the run, or None if your
    Keyhac is running.

    On macOS the lock is a file under ~/.keyhac, so a Keyhac with another HOME
    takes another lock and would happily start beside yours, both hooking
    every key. Holding the real one here stands in for it - and keeps yours
    from being started halfway through a run. On Windows the lock is a
    session-wide mutex that HOME does not move, so the run's Keyhac meets
    yours on its own (and says so).
    """
    if sys.platform != "darwin":
        return True
    from keyhac.platform.mac import instance
    return instance.acquire_instance_lock()


class KeyhacUp:
    """The run's Keyhac, in its own home, with its endpoint on.

    Started from this checkout (qa/daemon.py) with HOME pointing at the run's
    home, and quit at the end by closing its stdin - killed only if it has not
    gone within the grace. The home goes with it.
    """

    def __init__(self, home: Home, log: pathlib.Path, patience: float = 60.0,
                 grace: float = 15.0):
        self.home, self.log, self.patience, self.grace = home, log, patience, grace
        self.process = None
        self.lock = None

    def __enter__(self):
        self.lock = hold_your_lock()
        if self.lock is None:
            raise SystemExit(YOURS_IS_RUNNING)
        # The first config the run's Keyhac reads is a fixture, never the
        # template a first start would otherwise write.
        self.home.config.write_text(configs.config(configs.DEFAULT), encoding="utf-8",
                                    newline="\n")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        with self.log.open("w", encoding="utf-8") as output:
            self.process = subprocess.Popen(
                [sys.executable, str(HERE / "daemon.py")], cwd=ROOT, stdin=subprocess.PIPE,
                stdout=output, stderr=subprocess.STDOUT,
                env=dict(os.environ, PYTHONIOENCODING="utf-8", **self.home.environment()))
        print(f"keyhac:  started this checkout's Keyhac in {self.home.root} "
              f"(pid {self.process.pid}, log {self.log})")
        deadline = time.monotonic() + self.patience
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                said = self.log.read_text(encoding="utf-8", errors="replace")
                self._release()
                raise SystemExit(YOURS_IS_RUNNING if "already running" in said else
                                 f"Keyhac exited before its MCP server came up:\n{said[-1500:]}")
            if endpoint(self.home) is not None:
                print("keyhac:  MCP server on, no timeout")
                return self
            time.sleep(0.5)
        self.__exit__()
        raise SystemExit(f"Keyhac did not open its MCP server within {self.patience:.0f}s. "
                         f"On macOS the first start from a terminal asks for the "
                         f"Accessibility permission - grant it and run again.")

    def __exit__(self, *exc):
        try:
            self._quit()
        finally:
            self._release()
        return False

    def _quit(self):
        if self.process is None or self.process.poll() is not None:
            return
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(self.grace)
            print("keyhac:  quit")
        except subprocess.TimeoutExpired:
            # The published pid too: a venv's python.exe on Windows is a stub
            # whose child is the real Keyhac.
            try:
                pid = json.loads((self.home.keyhac / "mcp.json").read_text(
                    encoding="utf-8")).get("pid")
                if pid not in (None, self.process.pid):
                    os.kill(pid, signal.SIGTERM)
            except (OSError, ValueError):
                pass
            self.process.kill()
            self.process.wait()
            print(f"keyhac:  did not quit within {self.grace:.0f}s - killed")

    def _release(self):
        if self.lock not in (None, True):
            self.lock.release()
        self.lock = None
        self.home.remove()


# --- the adapter -------------------------------------------------------------------

class Keyhac(adapter.Adapter):
    name = "keyhac"
    version = keyhac.__version__
    checkout = ROOT
    rooms = ROOT / "cleanroom"
    scenarios = HERE / "scenarios"
    skill_name = "keyhac"
    default_variant = "files"
    fixtures = FIXTURES
    screens = SCREENS
    python = sys.executable
    evidence = {"keyhac": {
        "argv": [sys.executable, str(HERE / "evidence.py")],
        "help": "replay | bindings | preserved | calls | writes | events | records | expected | rules"}}
    evidence_help = (
        "`keyhac` is qa/evidence.py. `replay <room> --app Code --keys F1,Fn-F1` loads "
        "the room's config.py in Keyhac's own engine, focuses that application, "
        "presses the keys and answers what the application received; --before uses "
        "the starting config and --platform windows the other OS. `bindings` lists "
        "what the run added, changed or lost; `preserved` the user's # keep: lines; "
        "`calls` the room's Keyhac tool calls with their answers; `writes` the files "
        "the room wrote with its own file tools, the Desktop ones with whether an "
        "action was started after each; `events` the "
        "fixture page's record; `records <room> <file> --expect systema,systemb` a "
        "CSV against the records the screens hold; `expected <screen>` what a "
        "fixture screen holds; `rules <room>` the skill's mechanical rules (waits, "
        "not sleeps; no coordinates; bounded loops; ...) on each action the room "
        "left. Names on this OS: VS Code is Code, TextEdit / "
        "notepad, Terminal / WindowsTerminal, Safari / msedge. A room named `.mcp` "
        "had file tools that reach neither ~/.keyhac nor the Desktop; one with no "
        "suffix had both. Hold every room to "
        f"{HERE / 'scenarios' / '_always.md'} as well as to its scenario.")

    def __init__(self):
        self._foreign: set[str] = set()
        self._home: Home | None = None

    @property
    def home(self) -> Home:
        """The run's home, made when a run first needs it - not when the
        adapter is merely loaded to list or check scenarios."""
        if self._home is None:
            self._home = Home()
        return self._home

    def bundle(self) -> pathlib.Path:
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "build_skill_bundle.py")],
                              cwd=ROOT, capture_output=True, text=True)
        if done.returncode != 0:
            raise SystemExit(f"the skill bundle did not build:\n{done.stdout}{done.stderr}")
        return ROOT / "dist" / f"keyhac-{keyhac.__version__}-skill.zip"

    def rules(self) -> str:
        return (HERE / "RULES.md").read_text(encoding="utf-8").format(
            home=self.home.root, extensions=self.home.extensions,
            desktop=self.home.desktop)

    def session(self, room) -> session.Session:
        info = endpoint(self.home)
        if info is None or "bridge" not in info:
            raise SystemExit("Keyhac's endpoint is not answering, or publishes no bridge")
        # The variant - the scenario's `access` - is the room's label after the
        # scenario's name (runner.label_of): the session is made before the
        # stand-in, the one call that is handed it.
        variant = room.name.partition(".")[2]
        # files (the default): the room's file tools reach ~/.keyhac and the
        # Desktop, as for an agent the user gave the folder. mcp: they reach
        # neither, as in Claude Desktop with no folder added, so config.py and
        # extensions/ are Keyhac's tools' alone. The first rooms had only
        # extensions/ and the Desktop - like neither host - and edited actions
        # directly while config.py went through write_config whole.
        reach = [] if variant == "mcp" else [self.home.keyhac, self.home.desktop]
        # The bridge finds the endpoint under ~/.keyhac: the run's, by HOME.
        return session.Session(
            allow=ALLOW, add_dirs=reach,
            mcp_servers={"keyhac": {"command": info["bridge"],
                                    "env": self.home.environment()}})

    def policy(self, room) -> audit.Policy:
        installed_app = ("/applications/keyhac.app" if os.name == "nt"
                         else "/Applications/Keyhac.app")
        return audit.Policy(
            checkout=ROOT, servers=("keyhac",), foreign=self._foreign,
            forbidden={"site-packages/keyhac": "the installed package",
                       installed_app: "the installed app",
                       str(self.home.stand_in): "the previous fixture, set aside"},
            leaked_skill=lambda command: "keyhac" in command)

    def product(self):
        return KeyhacUp(self.home, self.rooms / "keyhac.log")

    def alive(self) -> bool:
        return endpoint(self.home) is not None

    def runs(self, cases, named=False):
        """One room per scenario, on the config its front matter names
        (`config = "short"`; the template when it names none) and with the
        access it names: `access = "mcp"` is a room whose file tools do not
        reach ~/.keyhac (the fallback through Keyhac's tools), labelled
        `.mcp`; otherwise they do (the setup the skill asks for). Each fixture
        config and each access is some scenario's: running every scenario on
        all three configs made a full run 19 rooms, most of them finding the
        same problems again."""
        found = []
        for case in cases:
            needs = case.meta.get("needs")
            if needs and not named:
                print(f"  skipped: {case.name} - needs {needs}; name it with CASE=")
                continue
            found.append((case, "mcp" if case.meta.get("access") == "mcp" else ""))
        return found

    def stand_in(self, case, variant, room):
        home = self.home
        self._foreign = {p.name for p in home.extensions.glob("*.py")}
        staged = [(FIXTURES / source, home.desktop / name)
                  for source, name in case.meta.get("staged", [])]

        def reload():
            said = keyhac_call(home, "reload_config")
            verdict = said.splitlines()[0] if said else ""
            if verdict != "loaded":
                print(f"  ! reload: {verdict}")

        # Nothing here is yours: the stand-in only swaps one fixture for the
        # next inside the run's home, and collects what the room made.
        return standin.StandIn(
            home=home.keyhac, marker=home.stand_in,
            fixtures={"config.py": configs.config(case.meta.get("config", configs.DEFAULT))},
            evidence=room / "evidence", backups="config.py.bak-*",
            watch=[standin.Watch(home.extensions, "extensions"),
                   standin.Watch(home.desktop, "desktop"),
                   standin.Watch(home.keyhac, "keyhac", skip=KEYHAC_OWN)],
            staged=staged, on_change=reload)

    def restore(self) -> list[str]:
        return ["Nothing to restore: runs use a Keyhac home of their own, and your "
                "~/.keyhac is never set aside."]
