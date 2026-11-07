"""The run's Keyhac in a VM: off the host's keyboard and screen (issue #271).

    KEYHAC_QA_GUEST=qa-mac make qa CASE=13

The VM layer - the guest over SSH, the shared folder, Tart and VMware
machines, the venv, the desktop session, the screens - is quality-loop's
(`quality_loop.guest`, docs/guests.md there), shared with XeFM (xefm#750).
What is left here is Keyhac's own:

- **What is staged.** This working tree's keyhac/ and qa/daemon.py, and
  PuiKit - ours, so staged rather than installed: the PuiKit this checkout's
  own environment runs - on a stage on the share.
- **How Keyhac starts.** As the test suite runs (`make test-vm`): the
  image's Python - no Keyhac is installed in the image (xefm#750, "Decisions
  on the shared test image") - with the third-party packages from
  pyproject.toml installed into a fresh venv for the run, runs qa/daemon.py
  in the desktop session. macOS: over SSH, which reaches that session, under
  the image's Accessibility and Input Monitoring grant to python.org's Python
  and sshd-keygen-wrapper. Windows: from a scheduled task, which needs no
  grant. Either way the code under test is all this checkout's: Keyhac.app's
  bundled PuiKit, which once ran the staged keyhac/ here, went stale the
  first time the PuiKit pin was raised.
- **How it stops.** It quits when a file appears (KEYHAC_QA_QUIT_FILE,
  qa/daemon.py): there is no stdin to close across a VM.
- **How it is reached.** Keyhac in the guest listens on the guest's
  127.0.0.1 and publishes the port in mcp.json, on the shared home. An
  `ssh -L` with the same port number on the host makes that file true here
  too, so the host's own keyhac-mcp-bridge, given the home as HOME, reaches
  the guest's Keyhac unchanged - the token never leaves the shared home.

KEYHAC_QA_GUEST is an SSH host alias; its address, user and key are in
~/.ssh/config, and what it means on this machine is its table in
~/.config/quality-loop/guests.toml (doc/dev/testing.md, "In a VM").
KEYHAC_QA_SHARE overrides the table's share.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import time
import tomllib
import urllib.request

from quality_loop import guest as ql
from quality_loop.guest import GuestError, TartMachine, VMwareMachine  # noqa: F401

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

#: Ours, so staged from this checkout's environment rather than installed:
#: the packages a run tests along with Keyhac.
STAGED = ("puikit",)

#: Ours that only the test suite needs - staged too in a development test run.
TEST_STAGED = ("quality_loop",)

#: Where a test run's packages of ours come from: this checkout's environment,
#: or PyPI as pyproject.toml names them (a release's run, #316).
PACKAGES = ("local", "pypi")


def _name(requirement: str) -> str:
    head = requirement.split(";")[0]
    for stop in "<>=!~[ ":
        head = head.split(stop)[0]
    return head.strip().lower()


def _requires(name: str) -> list[str]:
    """``name``'s requirements, markers and all. An editable install's
    metadata is as old as the install, so a checkout's (../puikit's) are read
    from its pyproject.toml instead."""
    import importlib.metadata
    import urllib.parse
    distribution = importlib.metadata.distribution(name)
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    if direct.get("dir_info", {}).get("editable"):
        checkout = pathlib.Path(urllib.parse.urlsplit(direct["url"]).path)
        with (checkout / "pyproject.toml").open("rb") as f:
            project = tomllib.load(f)["project"]
        requires = list(project.get("dependencies", []))
        for extra, lines in project.get("optional-dependencies", {}).items():
            requires += [f'{line}; extra == "{extra}"' for line in lines]
        return requires
    return list(distribution.requires or ())


def third_party_requirements(tests: bool = False, packages: str = "local") -> list[str]:
    """What the guest installs. With ``packages="pypi"``, pyproject.toml's
    dependencies as they read - PuiKit from PyPI too, what a user's pip gets.
    With ``"local"``, those that are not ours, and those of what we stage
    (PuiKit's numpy, say - the first Windows run without them stopped at
    `import numpy`), markers and all: pip in the guest leaves out what is for
    another OS. An extra's (``extra == "dev"``) is not the run's. With
    ``tests``, the dev extra too - pytest and quality-loop, whose plugin the
    suite loads - and in a local run quality-loop's own requirements."""
    with (ROOT / "pyproject.toml").open("rb") as f:
        project = tomllib.load(f)["project"]
    wanted = list(project["dependencies"])
    if tests:
        wanted += project["optional-dependencies"]["dev"]
    if packages == "pypi":
        return list(dict.fromkeys(wanted))
    staged = STAGED + (TEST_STAGED if tests else ())
    for name in staged:
        wanted += [r for r in _requires(name.replace("_", "-")) if "extra ==" not in r]
    out = []
    for requirement in wanted:
        if _name(requirement).replace("-", "_") not in staged and requirement not in out:
            out.append(requirement)
    return out


def working_tree() -> list[str]:
    """This checkout's files as git sees them: tracked, and untracked but not
    ignored - so .venv and cleanroom/ stay behind."""
    said = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                          capture_output=True, text=True, check=True)
    return [p for p in said.stdout.split("\0") if p and (ROOT / p).is_file()]


class Guest(ql.Guest):
    """quality-loop's guest, with Keyhac staged and started in it."""

    @classmethod
    def for_alias(cls, alias: str) -> "Guest":
        try:
            return cls.from_settings(alias, ql.settings_for(alias, os.environ.get("KEYHAC_QA_GUESTS")),
                                     os.environ.get("KEYHAC_QA_SHARE"))
        except GuestError as error:
            raise SystemExit(f"{alias}: {error}") from None

    @classmethod
    def from_environment(cls) -> "Guest | None":
        try:
            return super().from_environment(
                "KEYHAC_QA_GUEST", "KEYHAC_QA_SHARE",
                settings_path=os.environ.get("KEYHAC_QA_GUESTS"))
        except GuestError as error:
            raise SystemExit(f"KEYHAC_QA_GUEST: {error}") from None

    @property
    def os_name(self) -> str:
        """What `--platform` takes when this run is replayed: windows or mac."""
        return "windows" if self.windows else "mac"

    def use_arm64_python(self) -> None:
        """From here on, a native ARM64 Python of the image's version instead of
        the image's x64 one, which that Arm VM emulates. NuGet's package is the
        whole interpreter in a zip, with no installer to run in a session with
        no desktop. It lands on the guest's disk, which the next revert drops."""
        if not self.windows:
            raise SystemExit(f"{self.alias}: --arch arm64 is for a Windows guest")
        said = self.ssh(f"& {ql.ps_quote(self.python)} -c \"import platform; "
                        f"print(platform.python_version())\"")
        version = said.stdout.strip()
        package = self.host_share / f"pythonarm64-{version}.zip"
        url = f"https://www.nuget.org/api/v2/package/pythonarm64/{version}"
        print(f"vm:      fetching {url}", flush=True)
        urllib.request.urlretrieve(url, package)
        where = r"$env:LOCALAPPDATA\keyhac-python-arm64"
        python = rf"{where}\tools\python.exe"
        said = self.ssh(f"Expand-Archive -Force -Path {ql.ps_quote(self.path(package))} "
                        f"-DestinationPath \"{where}\"; "
                        f"Write-Output \"{python}\"; "
                        f"& \"{python}\" -c \"import sysconfig; print(sysconfig.get_platform())\"",
                        timeout=300)
        package.unlink()
        lines = said.stdout.strip().splitlines()
        if len(lines) < 2 or lines[-1].strip() != "win-arm64":
            raise SystemExit(f"{self.alias}: no ARM64 Python in the guest:\n"
                             f"{said.stdout}{said.stderr}")
        self.settings["python"] = lines[-2].strip()

    def stage(self, prefix: str = "keyhac-qa-stage-") -> pathlib.Path:
        """This working tree's Keyhac, copied onto the share, beside what
        quality-loop runs in the guest."""
        stage = super().stage(prefix)
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(ROOT / "keyhac", stage / "keyhac", ignore=ignore)
        (stage / "qa").mkdir()
        shutil.copy2(HERE / "daemon.py", stage / "qa" / "daemon.py")
        lib = stage / "lib"
        lib.mkdir()
        for name in STAGED:
            package = pathlib.Path(__import__(name).__file__).parent
            shutil.copytree(package, lib / name, ignore=ignore)
        return stage

    def test_stage(self, packages: str = "local") -> pathlib.Path:
        """For the test suite: quality-loop's stage, with this working tree as
        git sees it in <stage>/tree and, in a development run, our packages in
        <stage>/lib (PuiKit, quality-loop)."""
        stage = ql.Guest.stage(self, "keyhac-test-")
        for name in working_tree():
            target = stage / "tree" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, target)
        (stage / "lib").mkdir()
        if packages == "local":
            ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
            for name in STAGED + TEST_STAGED:
                package = pathlib.Path(__import__(name).__file__).parent
                shutil.copytree(package, stage / "lib" / name, ignore=ignore)
        return stage

    def start(self, home_root: pathlib.Path, stage: pathlib.Path, log: pathlib.Path,
              quit_file: pathlib.Path) -> None:
        """Keyhac from ``stage`` in the guest's desktop session, HOME on the share."""
        home, quit_at = self.path(home_root), self.path(quit_file)
        if self.windows:
            self._start_windows(home, stage, log, quit_at)
            return
        # A clone just booted shows Installer Progress for a few seconds, in
        # front. Keyhac reads the focus once and keeps it until a key event,
        # so a get_focus in those seconds would go on answering the installer.
        self.ssh("for i in $(seq 60); do pgrep -qf 'Installer Progress.app' || exit 0; "
                 "sleep 1; done", timeout=90)
        self._start_mac(home, stage, log, quit_at)

    def _start_mac(self, home: str, stage: pathlib.Path, log: pathlib.Path,
                   quit_at: str) -> None:
        # The image's Python with this run's packages, as `make test-vm` runs
        # the suite. Over SSH, which reaches the desktop session; the image
        # grants python.org's Python and sshd-keygen-wrapper Accessibility and
        # Input Monitoring, which is what the hook and the AX reads need.
        # daemon.py puts the stage in front of sys.path, so the staged keyhac
        # wins; PYTHONPATH carries the staged PuiKit. nohup and the
        # redirections let the SSH session end while Keyhac goes on.
        try:
            python = self.venv(stage, third_party_requirements())
        except GuestError as error:
            raise SystemExit(f"could not start Keyhac in the VM:\n{error}") from None
        env = {"HOME": home, "PYTHONPATH": self.path(stage / "lib"),
               "KEYHAC_QA_QUIT_FILE": quit_at, "KEYHAC_QA_KEEP_FOCUS": "1",
               "PYTHONIOENCODING": "utf-8"}
        assignments = " ".join(f"{name}={ql.sh_quote(value)}"
                               for name, value in env.items())
        command = (f"cd {ql.sh_quote(self.path(stage))} && {assignments} "
                   f"nohup {ql.sh_quote(python)} -s qa/daemon.py "
                   f"> {ql.sh_quote(self.path(log))} 2>&1 < /dev/null &")
        said = self.ssh(command)
        if said.returncode != 0:
            raise SystemExit(f"could not start Keyhac in the VM:\n{said.stderr}")

    def _start_windows(self, home: str, stage: pathlib.Path, log: pathlib.Path,
                       quit_at: str) -> None:
        # The image's Python with this run's packages; pythonw, which opens
        # no console window on the desktop the room looks at. daemon.py puts
        # the stage in front of sys.path, so the staged keyhac wins; lib/
        # carries the staged PuiKit.
        python = self.venv(stage, third_party_requirements())
        try:
            self.launch(stage, self.task(stage), self.windowless(python),
                        script=self.path(stage / "qa" / "daemon.py"), log=log,
                        environ={"HOME": home, "USERPROFILE": home,
                                 "KEYHAC_QA_QUIT_FILE": quit_at,
                                 "KEYHAC_QA_KEEP_FOCUS": "1", "PYTHONIOENCODING": "utf-8"},
                        path=[self.path(stage / "lib")])
        except GuestError as error:
            raise SystemExit(f"could not start Keyhac in the VM:\n{error}") from None

    @staticmethod
    def task(stage: pathlib.Path) -> str:
        return f"keyhac-qa-{stage.name}"


class GuestKeyhacUp:
    """qa/run.py's KeyhacUp, with Keyhac in the VM.

    No single-instance lock is held on the host: the run's Keyhac hooks the
    guest's keyboard, so yours can keep running. The guest's own Keyhac, if
    one is up, is another matter - on Windows its mutex stops this one, and
    the log says so.
    """

    def __init__(self, guest: Guest, home, config_text: str, log: pathlib.Path,
                 patience: float = 300.0, grace: float = 45.0):
        # patience covers the Windows venv: pip in the guest, x64 under
        # emulation on Arm, before Keyhac has started at all.
        self.guest, self.home, self.config_text = guest, home, config_text
        self.log, self.patience, self.grace = log, patience, grace
        self.stage = None
        self.forwarding = None

    @property
    def _guest_log(self) -> pathlib.Path:
        return self.stage / "keyhac.log"

    def __enter__(self):
        self.home.config.write_text(self.config_text, encoding="utf-8", newline="\n")
        self.stage = self.guest.stage()
        self.guest.start(self.home.root, self.stage, self._guest_log, self.stage / "quit")
        print(f"keyhac:  started this checkout's Keyhac in the VM {self.guest.alias} "
              f"({self.guest.os_name}), home {self.home.root}, log {self._guest_log}")
        published = self.home.keyhac / "mcp.json"
        deadline = time.monotonic() + self.patience
        while time.monotonic() < deadline:
            try:
                info = json.loads(published.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = None
            if info:
                return self._forward(info)
            said = self._said()
            if "already running" in said or "Traceback" in said:
                self.__exit__()
                raise SystemExit(f"Keyhac in the VM did not come up:\n{said[-1500:]}")
            time.sleep(1)
        said = self._said()
        self.__exit__()
        raise SystemExit(f"Keyhac in the VM did not open its MCP server within "
                         f"{self.patience:.0f}s:\n{said[-1500:]}")

    def _forward(self, info: dict):
        port = info["port"]
        self.forwarding = self.guest.forward(port)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.forwarding.poll() is not None:
                error = self.forwarding.stderr.read().decode(errors="replace")
                self.__exit__()
                raise SystemExit(f"could not forward the VM's port {port}: {error}")
            if ql.listening(port):
                print(f"keyhac:  MCP server on, no timeout; the VM's port {port} forwarded")
                return self
            time.sleep(0.3)
        self.__exit__()
        raise SystemExit(f"the VM's port {port} did not come through the forward")

    def _said(self) -> str:
        try:
            return self._guest_log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def __exit__(self, *exc):
        try:
            self._quit()
        finally:
            if self.forwarding is not None:
                self.forwarding.terminate()
                self.forwarding.wait()
            if self.stage is not None:
                self.log.parent.mkdir(parents=True, exist_ok=True)
                self.log.write_text(self._said(), encoding="utf-8")
                self.guest.forget_task(self.guest.task(self.stage))
                ql.end_tunnels(self.stage)
                shutil.rmtree(self.stage, ignore_errors=True)
            self.home.remove()
        return False

    def _quit(self):
        if self.stage is None:
            return
        published = self.home.keyhac / "mcp.json"
        try:
            pid = json.loads(published.read_text(encoding="utf-8")).get("pid")
        except (OSError, ValueError):
            pid = None
        (self.stage / "quit").touch()
        # Keyhac says "stopped" as the last thing it logs. Not mcp.json's
        # absence: on an SMB share the host's view of a deleted file lags.
        # The grace covers a Windows guest running x64 Python emulated.
        deadline = time.monotonic() + self.grace
        while time.monotonic() < deadline:
            if "Keyhac 2 stopped." in self._said():
                print("keyhac:  quit")
                return
            time.sleep(0.5)
        if pid is not None:
            self.guest.kill(pid)
        print(f"keyhac:  did not quit within {self.grace:.0f}s - killed")
