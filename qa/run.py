"""Keyhac's quality-loop: run the scenarios in qa/scenarios against the skill bundle.

    make qa                          # every scenario that needs no screen of yours
    make qa CASE=11                  # one scenario (repeatable: CASE="11 12")
    make qa CASE=11 CONFIGS=short    # only these fixture configs
    make qa ARGS=--restore           # put back a config.py a run could not

Each scenario gets a room holding this version's bundle and nothing else, an
operator agent that puts up its screen and afterwards reports what it finds,
and a Keyhac with its MCP endpoint on - this checkout's, started here when
none is running, and quit at the end. For the length of a room your
config.py is set aside and a fixture stands in for it. The result is
FINDINGS.md beside the rooms: problems with severities, no verdict.

The design is crftwr/keyhac#205; the harness is quality-loop
(crftwr/quality-loop). What is Keyhac's own is here: the bundle, the rules,
what a room may reach, the stand-in, the screens and the evidence tools.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import configs  # noqa: E402
import keyhac  # noqa: E402
from keyhac.core import paths  # noqa: E402
from quality_loop import adapter, audit, runner, screens, session, standin  # noqa: E402

KEYHAC_HOME = pathlib.Path(paths.resolve(None).data_dir)
CONFIG = KEYHAC_HOME / "config.py"
EXTENSIONS = KEYHAC_HOME / "extensions"
DESKTOP = pathlib.Path.home() / "Desktop"
FIXTURES = ROOT / "examples" / "actions" / "fixtures"

#: Where the operator's config.py (and copies of its write_config backups)
#: wait. Beside config.py, which the room cannot reach: it is given
#: extensions/ and the Desktop, never ~/.keyhac itself.
STAND_IN = KEYHAC_HOME / "cleanroom-stand-in"

#: Files Keyhac keeps beside config.py, never collected as something the room
#: made: moving mcp.json away would take the endpoint with it.
KEYHAC_OWN = ("mcp.json", "settings.json", "clipboard.json", "extensions", STAND_IN.name)

#: What a room may do: read the bundle, write an action, drive Keyhac, keep
#: notes. No Bash - `--restricted` has none, and that is what makes the file
#: tools' confinement real.
ALLOW = ["Read", "Glob", "Grep", "Write", "Edit", "TodoWrite", "mcp__keyhac"]

MCP_IS_OFF = """\
Keyhac is running with its MCP endpoint off. Tick AI Integration > MCP Server
in it, or quit it so the run can start its own: two Keyhacs cannot both hold
the keyboard."""


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
    "build-failure": screens.Terminal("build_failure.py", title="build - acme"),
    "deploy": screens.Terminal("deploy.py", title="deploy - acme"),
}


# --- Keyhac itself -----------------------------------------------------------------

def endpoint() -> dict | None:
    """Keyhac's live MCP endpoint, or None."""
    published = KEYHAC_HOME / "mcp.json"
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


def keyhac_call(tool: str, arguments: dict | None = None) -> str:
    """Call one of Keyhac's MCP tools as the runner, not as the room."""
    info = endpoint()
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


class KeyhacUp:
    """A Keyhac with its endpoint on, for the length of the run.

    One already answering is used and left running - it is the operator's.
    Otherwise this checkout's is started (qa/daemon.py) and quit at the end by
    closing its stdin, and killed only if it has not gone within the grace.
    """

    def __init__(self, log: pathlib.Path, patience: float = 60.0, grace: float = 15.0):
        self.log, self.patience, self.grace = log, patience, grace
        self.process = None

    def __enter__(self):
        if endpoint() is not None:
            print("keyhac:  using the Keyhac already running, and leaving it running")
            return self
        self.log.parent.mkdir(parents=True, exist_ok=True)
        started = time.time()
        with self.log.open("w", encoding="utf-8") as output:
            self.process = subprocess.Popen(
                [sys.executable, str(HERE / "daemon.py")], cwd=ROOT, stdin=subprocess.PIPE,
                stdout=output, stderr=subprocess.STDOUT,
                env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        print(f"keyhac:  started this checkout's Keyhac (pid {self.process.pid}, log {self.log})")
        deadline = time.monotonic() + self.patience
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                said = self.log.read_text(encoding="utf-8", errors="replace")
                raise SystemExit(MCP_IS_OFF if "already running" in said else
                                 f"Keyhac exited before its MCP server came up:\n{said[-1500:]}")
            try:
                fresh = (KEYHAC_HOME / "mcp.json").stat().st_mtime >= started - 1.0
            except OSError:
                fresh = False
            if fresh and endpoint() is not None:
                print("keyhac:  MCP server on, no timeout")
                return self
            time.sleep(0.5)
        self.__exit__()
        raise SystemExit(f"Keyhac did not open its MCP server within {self.patience:.0f}s. "
                         f"On macOS the first start from a terminal asks for the "
                         f"Accessibility permission - grant it and run again.")

    def __exit__(self, *exc):
        if self.process is None or self.process.poll() is not None:
            return False
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
                pid = json.loads((KEYHAC_HOME / "mcp.json").read_text(encoding="utf-8")).get("pid")
                if pid not in (None, self.process.pid):
                    os.kill(pid, signal.SIGTERM)
            except (OSError, ValueError):
                pass
            self.process.kill()
            self.process.wait()
            print(f"keyhac:  did not quit within {self.grace:.0f}s - killed")
        return False


# --- the adapter -------------------------------------------------------------------

class Keyhac(adapter.Adapter):
    name = "keyhac"
    version = keyhac.__version__
    checkout = ROOT
    rooms = ROOT / "cleanroom"
    scenarios = HERE / "scenarios"
    skill_name = "keyhac"
    fixtures = FIXTURES
    screens = SCREENS
    python = sys.executable
    evidence = {"keyhac": {
        "argv": [sys.executable, str(HERE / "evidence.py")],
        "help": "replay | bindings | preserved | calls | events | records | expected"}}
    evidence_help = (
        "`keyhac` is qa/evidence.py. `replay <room> --app Code --keys F1,Fn-F1` loads "
        "the room's config.py in Keyhac's own engine, focuses that application, "
        "presses the keys and answers what the application received; --before uses "
        "the starting config and --platform windows the other OS. `bindings` lists "
        "what the run added, changed or lost; `preserved` the user's # keep: lines; "
        "`calls` the room's Keyhac tool calls with their answers; `events` the "
        "fixture page's record; `records <room> <file> --expect systema,systemb` a "
        "CSV against the records the screens hold; `expected <screen>` what a "
        "fixture screen holds. Names on this OS: VS Code is Code, TextEdit / "
        "notepad, Terminal / WindowsTerminal, Safari / msedge. Hold every room to "
        f"{HERE / 'scenarios' / '_always.md'} as well as to its scenario.")

    def __init__(self, only_configs: list[str] | None = None, keep: bool = False):
        self.only_configs = only_configs
        self.keep = keep
        self._foreign: set[str] = set()

    def bundle(self) -> pathlib.Path:
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "build_skill_bundle.py")],
                              cwd=ROOT, capture_output=True, text=True)
        if done.returncode != 0:
            raise SystemExit(f"the skill bundle did not build:\n{done.stdout}{done.stderr}")
        return ROOT / "dist" / f"keyhac-{keyhac.__version__}-skill.zip"

    def rules(self) -> str:
        return (HERE / "RULES.md").read_text(encoding="utf-8")

    def session(self, room) -> session.Session:
        info = endpoint()
        if info is None or "bridge" not in info:
            raise SystemExit("Keyhac's endpoint is not answering, or publishes no bridge")
        return session.Session(allow=ALLOW, add_dirs=[EXTENSIONS, DESKTOP],
                               mcp_servers={"keyhac": {"command": info["bridge"]}})

    def policy(self, room) -> audit.Policy:
        installed_app = ("/applications/keyhac.app" if os.name == "nt"
                         else "/Applications/Keyhac.app")
        return audit.Policy(
            checkout=ROOT, servers=("keyhac",), foreign=self._foreign,
            forbidden={"site-packages/keyhac": "the installed package",
                       installed_app: "the installed app",
                       str(STAND_IN): "the operator's own config.py, set aside"},
            leaked_skill=lambda command: "keyhac" in command)

    def product(self):
        return KeyhacUp(self.rooms / "keyhac.log")

    def alive(self) -> bool:
        return endpoint() is not None

    def runs(self, cases, named=False):
        found = []
        for case in cases:
            needs = case.meta.get("needs")
            if needs and not named:
                print(f"  skipped: {case.name} - needs {needs}; name it with CASE=")
                continue
            variants = case.meta.get("configs", [configs.DEFAULT]) \
                if case.meta.get("edits_config") else [configs.DEFAULT]
            if self.only_configs:
                variants = [v for v in variants if v in self.only_configs]
            found += [(case, "" if v == configs.DEFAULT else v) for v in variants]
        return found

    def stand_in(self, case, variant, room):
        self._foreign = {p.name for p in EXTENSIONS.glob("*.py")}
        staged = [(FIXTURES / source, DESKTOP / name)
                  for source, name in case.meta.get("staged", [])]

        def reload():
            said = keyhac_call("reload_config")
            verdict = said.splitlines()[0] if said else ""
            if verdict != "loaded":
                print(f"  ! reload: {verdict}")

        return standin.StandIn(
            home=KEYHAC_HOME, marker=STAND_IN,
            fixtures={"config.py": configs.config(variant or configs.DEFAULT)},
            evidence=room / "evidence", backups="config.py.bak-*",
            watch=[standin.Watch(EXTENSIONS, "extensions", move=not self.keep),
                   standin.Watch(DESKTOP, "desktop"),
                   standin.Watch(KEYHAC_HOME, "keyhac", skip=KEYHAC_OWN)],
            staged=staged, on_change=reload)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", action="append", help="a scenario by number or name")
    parser.add_argument("--configs", help="comma-separated fixture configs: "
                        + ", ".join(configs.DESCRIBED))
    parser.add_argument("--model", help="model for the rooms")
    parser.add_argument("--operator-model", help="model for the operators")
    parser.add_argument("--keep", action="store_true", help="leave what a room wrote "
                        "in extensions/ as well as collecting it")
    parser.add_argument("--restore", action="store_true",
                        help="put back a config.py a run could not")
    args = parser.parse_args(argv)
    if args.restore:
        for line in standin.restore(STAND_IN, KEYHAC_HOME, ["config.py"],
                                    on_change=lambda: keyhac_call("reload_config")):
            print(line)
        return 0
    names = [n for case in args.case or [] for n in case.split()] or None
    product = Keyhac(only_configs=args.configs.split(",") if args.configs else None,
                     keep=args.keep)
    runner.run(product, names, model=args.model, operator_model=args.operator_model)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
