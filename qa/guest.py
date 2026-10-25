"""The run's Keyhac in a VM: off the host's keyboard and screen (issue #271).

    KEYHAC_QA_GUEST=keyhac-qa-mac \\
    KEYHAC_QA_SHARE="$HOME/.cache/keyhac-qa-guest=/Volumes/My Shared Files/qa" \\
    make qa CASE=13

Everything Claude does stays on the host: the room, the operator, the
evidence, cleanroom/. Only Keyhac moves into the guest, and the run reaches it
the way it reaches a local one:

- **The run's home is on a shared folder.** It is made under the host side of
  KEYHAC_QA_SHARE, so the stand-in, the room's file tools and the evidence
  read and write it as before, and the guest's Keyhac gets the guest side as
  its HOME.
- **The endpoint is forwarded.** Keyhac in the guest listens on the guest's
  127.0.0.1 and publishes the port in mcp.json, on the shared home. An
  `ssh -L` with the same port number on the host makes that file true here
  too, so the host's own keyhac-mcp-bridge, given the home as HOME, reaches
  the guest's Keyhac unchanged - the token never leaves the shared home.
- **The code is this working tree.** keyhac/ and qa/daemon.py are copied to
  a stage on the share. macOS: Keyhac.app runs them, through
  qa/guest_boot/sitecustomize.py, because the guest's Accessibility grant
  is the app's. Windows: the bundle's runtime\\python.exe runs
  qa/daemon.py, which needs no grant.
- **It runs in the desktop session**, where the hook sees input and windows
  can take focus - not in the SSH session, which has neither. macOS starts it
  with `open`, Windows with a scheduled task run as the logged-in user.
- **It quits when a file appears** (KEYHAC_QA_QUIT_FILE, qa/daemon.py): there
  is no stdin to close across a VM.

KEYHAC_QA_GUEST is an SSH host alias; its address, user and key are in
~/.ssh/config and never here (doc/dev/testing.md, "Driving a Windows VM from
the Mac over SSH"). KEYHAC_QA_GUEST_KEYHAC is Keyhac in the guest: Keyhac.app
on macOS (default /Applications/Keyhac.app), the bundle directory build.ps1
makes on Windows (default C:\\Program Files\\Keyhac).

Screens are not moved yet: a scenario that puts one up would put it on the
host. Run those locally for now.
"""

from __future__ import annotations

import json
import ntpath
import os
import pathlib
import posixpath
import shutil
import socket
import subprocess
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


class Guest:
    """One VM, reached as an SSH host alias, sharing a folder with the host."""

    def __init__(self, alias: str, host_share: pathlib.Path, guest_share: str,
                 keyhac: str | None = None):
        self.alias = alias
        self.host_share = host_share.resolve()
        self.guest_share = guest_share
        self._keyhac = keyhac
        self._windows: bool | None = None

    @classmethod
    def from_environment(cls) -> "Guest | None":
        alias = os.environ.get("KEYHAC_QA_GUEST")
        if not alias:
            return None
        share = os.environ.get("KEYHAC_QA_SHARE", "")
        host, _, guest = share.partition("=")
        if not host or not guest:
            raise SystemExit("KEYHAC_QA_GUEST needs KEYHAC_QA_SHARE=<host dir>=<guest dir>: "
                             "a folder shared with the VM, as each side sees it")
        host_share = pathlib.Path(host).expanduser()
        if not host_share.is_dir():
            raise SystemExit(f"KEYHAC_QA_SHARE: {host_share} is not a directory")
        return cls(alias, host_share, guest, os.environ.get("KEYHAC_QA_GUEST_KEYHAC"))

    # --- talking to it ---------------------------------------------------------

    def ssh(self, command: str, timeout: float = 60) -> subprocess.CompletedProcess:
        return subprocess.run(["ssh", "-o", "BatchMode=yes", self.alias, command],
                              capture_output=True, text=True, timeout=timeout)

    @property
    def windows(self) -> bool:
        """Whether the guest is Windows - asked once. A Windows guest's shell
        is PowerShell, where `uname` is no command."""
        if self._windows is None:
            said = self.ssh("uname -s")
            if said.returncode == 255:
                raise SystemExit(f"cannot reach the VM as {self.alias!r}:\n{said.stderr}")
            self._windows = said.stdout.strip() != "Darwin"
        return self._windows

    @property
    def platform(self) -> str:
        return "windows" if self.windows else "mac"

    @property
    def keyhac(self) -> str:
        if self._keyhac:
            return self._keyhac
        return r"C:\Program Files\Keyhac" if self.windows else "/Applications/Keyhac.app"

    def path(self, host_path: pathlib.Path) -> str:
        """A path under the host side of the share, as the guest sees it."""
        parts = pathlib.Path(host_path).resolve().relative_to(self.host_share).parts
        join = ntpath.join if self.windows else posixpath.join
        return join(self.guest_share, *parts)

    def mkdtemp(self, prefix: str) -> pathlib.Path:
        return pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=self.host_share)).resolve()

    # --- Keyhac in it ------------------------------------------------------------

    def stage(self) -> pathlib.Path:
        """This working tree's Keyhac, copied onto the share."""
        stage = self.mkdtemp("keyhac-qa-stage-")
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        shutil.copytree(ROOT / "keyhac", stage / "keyhac", ignore=ignore)
        (stage / "qa").mkdir()
        shutil.copy2(HERE / "daemon.py", stage / "qa" / "daemon.py")
        shutil.copytree(HERE / "guest_boot", stage / "boot", ignore=ignore)
        return stage

    def start(self, home_root: pathlib.Path, stage: pathlib.Path, log: pathlib.Path,
              quit_file: pathlib.Path) -> None:
        """Keyhac from ``stage`` in the guest's desktop session, HOME on the share."""
        home, quit_at = self.path(home_root), self.path(quit_file)
        if self.windows:
            self._start_windows(home, stage, log, quit_at)
        else:
            env = {"HOME": home, "PYTHONPATH": self.path(stage / "boot"),
                   "KEYHAC_QA_STAGE": self.path(stage), "KEYHAC_QA_QUIT_FILE": quit_at}
            # -n: a new instance even if one is up, so it gets this environment.
            args = ["open", "-n", "-g"]
            for name, value in env.items():
                args += ["--env", f"{name}={value}"]
            args += ["--stdout", self.path(log), "--stderr", self.path(log),
                     "-a", self.keyhac]
            said = self.ssh(" ".join(_sh_quote(a) for a in args))
            if said.returncode != 0:
                raise SystemExit(f"could not start Keyhac in the VM:\n{said.stderr}")

    def _start_windows(self, home: str, stage: pathlib.Path, log: pathlib.Path,
                       quit_at: str) -> None:
        # A scheduled task, run interactively, is how a process gets into the
        # logged-in user's desktop session from SSH. Its command is a .cmd on
        # the stage that sets the environment the task cannot.
        python = ntpath.join(self.keyhac, "runtime", "python.exe")
        # The bundle's python.exe alone finds only the standard library:
        # Keyhac.exe builds the rest of the path itself (windows_app/src/
        # launcher.c), and build.ps1 removes the embeddable's ._pth. So the
        # same two entries, in the launcher's order; daemon.py then puts the
        # stage in front of app\, so the staged keyhac wins over the bundle's.
        search = ";".join(ntpath.join(self.keyhac, part)
                          for part in (r"Lib\site-packages", "app"))
        script = "\r\n".join([
            "@echo off",
            f'set "HOME={home}"',
            f'set "USERPROFILE={home}"',
            f'set "KEYHAC_QA_QUIT_FILE={quit_at}"',
            f'set "PYTHONPATH={search}"',
            'set "PYTHONNOUSERSITE=1"',
            'set "KEYHAC_QA_KEEP_FOCUS=1"',
            'set "PYTHONIOENCODING=utf-8"',
            f'"{python}" "{self.path(stage / "qa" / "daemon.py")}" > "{self.path(log)}" 2>&1',
            ""])
        (stage / "start.cmd").write_bytes(script.encode("utf-8"))
        task = f"keyhac-qa-{stage.name}"
        said = self.ssh(
            f"schtasks /create /tn '{task}' /tr 'cmd /c \"\"{self.path(stage / 'start.cmd')}\"\"' "
            f"/sc once /st 00:00 /it /f; schtasks /run /tn '{task}'")
        if "SUCCESS: Attempted to run" not in said.stdout:
            raise SystemExit(f"could not start Keyhac in the VM:\n{said.stdout}{said.stderr}")

    def forget_task(self, stage: pathlib.Path) -> None:
        if self.windows:
            self.ssh(f"schtasks /delete /tn 'keyhac-qa-{stage.name}' /f")

    def kill(self, pid: int) -> None:
        if self.windows:
            self.ssh(f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue")
        else:
            self.ssh(f"kill -INT {int(pid)}; sleep 3; kill -9 {int(pid)} 2>/dev/null; true")

    def forward(self, port: int) -> subprocess.Popen:
        """The guest's 127.0.0.1:port as the host's, until terminated."""
        return subprocess.Popen(
            ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
             "-L", f"{port}:127.0.0.1:{port}", self.alias],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _sh_quote(word: str) -> str:
    return "'" + word.replace("'", "'\\''") + "'"


def _listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(1.0)
        return probe.connect_ex(("127.0.0.1", port)) == 0


class GuestKeyhacUp:
    """qa/run.py's KeyhacUp, with Keyhac in the VM.

    No single-instance lock is held on the host: the run's Keyhac hooks the
    guest's keyboard, so yours can keep running. The guest's own Keyhac, if
    one is up, is another matter - on Windows its mutex stops this one, and
    the log says so.
    """

    def __init__(self, guest: Guest, home, config_text: str, log: pathlib.Path,
                 patience: float = 180.0, grace: float = 45.0):
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
              f"({self.guest.platform}), home {self.home.root}, log {self._guest_log}")
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
            if _listening(port):
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
                self.guest.forget_task(self.stage)
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
        # The grace covers a Windows guest running the x64 bundle emulated.
        deadline = time.monotonic() + self.grace
        while time.monotonic() < deadline:
            if "Keyhac 2 stopped." in self._said():
                print("keyhac:  quit")
                return
            time.sleep(0.5)
        if pid is not None:
            self.guest.kill(pid)
        print(f"keyhac:  did not quit within {self.grace:.0f}s - killed")
