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
  is the app's. Windows: the bundle's runtime\\pythonw.exe runs
  qa/daemon.py through qa/guest_boot/winstart.py, which needs no grant.
- **It runs in the desktop session**, where the hook sees input and windows
  can take focus - not in the SSH session, which has neither. macOS starts it
  with `open -g`, Windows with a scheduled task run as the logged-in user.
- **It leaves the foreground where it was.** A room's first get_focus should
  find the desktop - Finder, or Explorer - not Keyhac's console: `open -g`
  never brings it forward, and on Windows qa/daemon.py hands the foreground
  back once the console is up.
- **It quits when a file appears** (KEYHAC_QA_QUIT_FILE, qa/daemon.py): there
  is no stdin to close across a VM.

KEYHAC_QA_GUEST is an SSH host alias; its address, user and key are in
~/.ssh/config and never here (doc/dev/testing.md, "Driving a Windows VM from
the Mac over SSH"). KEYHAC_QA_GUEST_KEYHAC is Keyhac in the guest: Keyhac.app
on macOS (default /Applications/Keyhac.app), the bundle directory build.ps1
makes on Windows (default C:\\Program Files\\Keyhac) - its interpreter and
third-party packages, which the staged keyhac/ runs on.

The VM should be clean when a run starts: a Tart clone of a base image, or
a VMware snapshot reverted to. When ~/.config/keyhac-qa/guests.toml names the
VM for the alias, the run does that itself (`Guest.machine`).

Screens are not moved yet: a scenario that puts one up would put it on the
host. Run those locally for now.
"""

from __future__ import annotations

import contextlib
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
import tomllib
import urllib.parse

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent


#: Per-machine settings, by SSH alias - outside the repository, since they
#: name this machine's VMs, paths and Keychain items. KEYHAC_QA_GUESTS moves it.
GUESTS = pathlib.Path("~/.config/keyhac-qa/guests.toml")


def _settings(alias: str) -> dict:
    where = pathlib.Path(os.environ.get("KEYHAC_QA_GUESTS", GUESTS)).expanduser()
    try:
        with where.open("rb") as f:
            return tomllib.load(f).get(alias, {})
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as error:
        raise SystemExit(f"{where}: {error}")


def _secret(value: str) -> str:
    """``keychain:<service>`` read from the login Keychain; anything else as
    it is. Read when a run needs it, never written anywhere."""
    if not value.startswith("keychain:"):
        return value
    service = value.removeprefix("keychain:")
    said = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                          capture_output=True, text=True)
    if said.returncode != 0:
        raise SystemExit(f"no Keychain item {service!r}: add it with "
                         f"security add-generic-password -a <account> -s {service} -w")
    return said.stdout.rstrip("\n")


class Guest:
    """One VM, reached as an SSH host alias, sharing a folder with the host.

    Configured by KEYHAC_QA_GUEST (the alias) and, for that alias, either the
    environment (KEYHAC_QA_SHARE, KEYHAC_QA_GUEST_KEYHAC) or its table in
    ~/.config/keyhac-qa/guests.toml, which can also name the VM itself, so
    the run starts it from a clean state (`Machine`):

        [keyhac-qa-mac]
        share = "~/.cache/keyhac-qa-guest=/Volumes/My Shared Files/qa"
        tart = "<base VM>"                  # cloned per run, deleted after

        [keyhac-qa-win]
        share = "~/mnt/qa-win=C:\\qa"
        vmx = "~/Virtual Machines.localized/<VM>.vmwarevm/<VM>.vmx"
        snapshot = "<snapshot>"             # reverted to before the run
        vm_password = "keychain:<service>"  # the VM's encryption password
        smb = "//<user>@<guest>/<share>"    # remounted on share's host side
        smb_password = "keychain:<service>"

    Without a VM named, the run uses the guest as it is, already running.
    """

    def __init__(self, alias: str, host_share: pathlib.Path, guest_share: str,
                 keyhac: str | None = None, settings: dict | None = None):
        self.alias = alias
        self.host_share = host_share.resolve()
        self.guest_share = guest_share
        self._keyhac = keyhac
        self._windows: bool | None = None
        self.settings = settings or {}
        #: Set while a run's clone is up: its address, which the alias's
        #: HostName does not know.
        self.hostname: str | None = None

    @classmethod
    def from_environment(cls) -> "Guest | None":
        alias = os.environ.get("KEYHAC_QA_GUEST")
        if not alias:
            return None
        settings = _settings(alias)
        share = os.environ.get("KEYHAC_QA_SHARE") or settings.get("share", "")
        host, _, guest = share.partition("=")
        if not host or not guest:
            raise SystemExit("KEYHAC_QA_GUEST needs KEYHAC_QA_SHARE=<host dir>=<guest dir>, "
                             f"or share = in its table in {GUESTS}: a folder shared with "
                             "the VM, as each side sees it")
        host_share = pathlib.Path(host).expanduser()
        if not host_share.is_dir():
            raise SystemExit(f"the share's host side, {host_share}, is not a directory")
        return cls(alias, host_share, guest,
                   os.environ.get("KEYHAC_QA_GUEST_KEYHAC") or settings.get("keyhac"),
                   settings)

    # --- talking to it ---------------------------------------------------------

    def _ssh_options(self) -> list[str]:
        if self.hostname is None:
            return []
        # A clone answers with its base's host key at a new address each time.
        return ["-o", f"HostName={self.hostname}", "-o", f"HostKeyAlias={self.alias}",
                "-o", "StrictHostKeyChecking=accept-new"]

    def ssh(self, command: str, timeout: float = 60) -> subprocess.CompletedProcess:
        return subprocess.run(["ssh", "-o", "BatchMode=yes", *self._ssh_options(),
                               self.alias, command],
                              capture_output=True, text=True, timeout=timeout)

    def reachable(self, patience: float) -> bool:
        """Whether SSH gets in within ``patience`` seconds."""
        deadline = time.monotonic() + patience
        while time.monotonic() < deadline:
            try:
                if self.ssh("echo up", timeout=15).stdout.strip() == "up":
                    return True
            except subprocess.TimeoutExpired:
                pass
            time.sleep(3)
        return False

    def machine(self):
        """A context manager for the run: the VM up from a clean state, and
        put away afterwards, when the settings name one."""
        if "tart" in self.settings:
            return TartMachine(self)
        if "vmx" in self.settings:
            return VMwareMachine(self)
        return contextlib.nullcontext()

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
        # quality-loop's own screen backends, for qa/guest_boot/screen_agent.py:
        # the package's __init__ and screens/, which need nothing else.
        import quality_loop
        package = pathlib.Path(quality_loop.__file__).parent
        (stage / "ql" / "quality_loop").mkdir(parents=True)
        shutil.copy2(package / "__init__.py", stage / "ql" / "quality_loop" / "__init__.py")
        shutil.copytree(package / "screens", stage / "ql" / "quality_loop" / "screens",
                        ignore=ignore)
        return stage

    @property
    def python(self) -> str:
        """The guest's interpreter, for a script a terminal screen runs:
        Keyhac's own, since a clean guest has no other."""
        if self.windows:
            return ntpath.join(self.keyhac, "runtime", "python.exe")
        return posixpath.join(self.keyhac, "Contents/Frameworks/Python.framework/"
                                           "Versions/Current/bin/python3")

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
        # logged-in user's desktop session from SSH. It runs pythonw.exe, which
        # opens no console window on that desktop; qa/guest_boot/winstart.py
        # sets up from env.json what a task cannot pass.
        settings = {
            "log": self.path(log),
            "daemon": self.path(stage / "qa" / "daemon.py"),
            "environ": {"HOME": home, "USERPROFILE": home,
                        "KEYHAC_QA_QUIT_FILE": quit_at, "KEYHAC_QA_KEEP_FOCUS": "1",
                        "PYTHONIOENCODING": "utf-8"},
            # The bundle's interpreter alone finds only the standard library:
            # Keyhac.exe builds the rest of the path itself (windows_app/src/
            # launcher.c), and build.ps1 removes the embeddable's ._pth. So the
            # same two entries, in the launcher's order; daemon.py then puts
            # the stage in front of app\, so the staged keyhac wins.
            "path": [ntpath.join(self.keyhac, r"Lib\site-packages"),
                     ntpath.join(self.keyhac, "app")],
        }
        (stage / "boot" / "env.json").write_text(json.dumps(settings), encoding="utf-8")
        said = self.run_in_session(stage, self._task(stage),
                                   stage / "boot" / "winstart.py")
        if "SUCCESS: Attempted to run" not in said.stdout:
            raise SystemExit(f"could not start Keyhac in the VM:\n{said.stdout}{said.stderr}")

    def run_in_session(self, stage: pathlib.Path, task: str, script: pathlib.Path,
                       *arguments: str) -> subprocess.CompletedProcess:
        """Windows: start ``script`` (on the stage) with the bundle's
        pythonw.exe in the logged-in user's desktop session, and return.

        A scheduled task, run interactively, is how a process gets into that
        session from SSH, where a window it opens is seen and EnumWindows
        finds it. pythonw opens no console window on that desktop. The task
        is XML, not schtasks /tr: the program and its arguments stay apart,
        so nothing has to survive PowerShell's quoting of a native command
        line - and the ScheduledTasks cmdlets need CIM, which refuses a
        standard user over SSH. The task is left defined; forget_task
        removes it."""
        definition = stage / "boot" / f"{task}.xml"
        definition.write_text(_task_xml(
            ntpath.join(self.keyhac, "runtime", "pythonw.exe"),
            " ".join(f'"{a}"' for a in ("-s", self.path(script), *arguments))),
            encoding="utf-16")
        quoted = _ps_quote(task)
        return self.ssh(f"schtasks /create /tn {quoted} /xml "
                        f"{_ps_quote(self.path(definition))} /f; schtasks /run /tn {quoted}")

    @staticmethod
    def _task(stage: pathlib.Path) -> str:
        return f"keyhac-qa-{stage.name}"

    def forget_task(self, stage: pathlib.Path, task: str | None = None) -> None:
        if self.windows:
            self.ssh(f"schtasks /delete /tn {_ps_quote(task or self._task(stage))} /f")

    def kill(self, pid: int) -> None:
        if self.windows:
            self.ssh(f"Stop-Process -Id {int(pid)} -Force -ErrorAction SilentlyContinue")
        else:
            self.ssh(f"kill -INT {int(pid)}; sleep 3; kill -9 {int(pid)} 2>/dev/null; true")

    def forward(self, port: int) -> subprocess.Popen:
        """The guest's 127.0.0.1:port as the host's, until terminated."""
        return subprocess.Popen(
            ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
             *self._ssh_options(), "-L", f"{port}:127.0.0.1:{port}", self.alias],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


class TartMachine:
    """A Tart VM cloned from the base for this run, and deleted after it.

    Its share is ``tart run --dir``, tagged with the last part of the share's
    guest side (``/Volumes/My Shared Files/qa`` -> ``qa``). Shown in a window
    when ``visible = true``; watch it, but do not type into it.
    """

    def __init__(self, guest: Guest, patience: float = 240.0):
        self.guest, self.patience = guest, patience
        self.base = guest.settings["tart"]
        self.name = f"{self.base}-run-{os.getpid()}"
        self.process = None

    def _tart(self, *args, check=True) -> subprocess.CompletedProcess:
        said = subprocess.run(["tart", *args], capture_output=True, text=True)
        if check and said.returncode != 0:
            raise SystemExit(f"tart {' '.join(args)}: {said.stderr.strip()}")
        return said

    def __enter__(self):
        self._tart("clone", self.base, self.name)
        tag = posixpath.basename(self.guest.guest_share.rstrip("/"))
        args = ["tart", "run", f"--dir={tag}:{self.guest.host_share}", self.name]
        if not self.guest.settings.get("visible"):
            args.insert(2, "--no-graphics")
        self.process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + self.patience
        while time.monotonic() < deadline:
            said = self._tart("ip", self.name, check=False)
            if said.returncode == 0 and said.stdout.strip():
                self.guest.hostname = said.stdout.strip()
                break
            time.sleep(2)
        if self.guest.hostname is None or not self.guest.reachable(
                max(10.0, deadline - time.monotonic())):
            self.__exit__()
            raise SystemExit(f"the clone {self.name} of {self.base} did not come up")
        print(f"vm:      {self.name}, a clone of {self.base}, at {self.guest.hostname}")
        return self

    def __exit__(self, *exc):
        self._tart("stop", self.name, check=False)
        if self.process is not None:
            try:
                self.process.wait(30)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self._tart("delete", self.name, check=False)
        self.guest.hostname = None
        print(f"vm:      {self.name} deleted")
        return False


class VMwareMachine:
    """A VMware Fusion VM reverted to a snapshot before the run.

    A snapshot taken while the VM was running comes back running, signed in
    and with SSH up, after `vmrun start`. The SMB share that was mounted on
    the host does not survive the revert, so it is mounted again. The VM is
    shut down afterwards, its share unmounted first: the next run reverts it
    anyway, and a VM `vmrun` left running headless is one the Fusion app
    cannot open. ``visible`` has
    no say here: `vmrun` can only resume it without a window. ``vm_password``
    is the VM's encryption password, which `vmrun` needs for a VM with a
    vTPM.
    """

    VMRUN = "/Applications/VMware Fusion.app/Contents/Library/vmrun"

    def __init__(self, guest: Guest, patience: float = 180.0):
        self.guest, self.patience = guest, patience
        settings = guest.settings
        self.vmx = str(pathlib.Path(settings["vmx"]).expanduser())
        self.snapshot = settings["snapshot"]
        self.password = settings.get("vm_password")

    def _vmrun(self, *args, check: bool = True) -> bool:
        command = [self.VMRUN, "-T", "fusion"]
        if self.password:
            command += ["-vp", _secret(self.password)]
        # Into a file, not a pipe: `vmrun start` hands its stdout on to the
        # vmware-vmx it starts, so a pipe would not close until the VM stops.
        with tempfile.TemporaryFile("w+") as output:
            said = subprocess.run(command + list(args), stdin=subprocess.DEVNULL,
                                  stdout=output, stderr=subprocess.STDOUT, text=True)
            output.seek(0)
            if said.returncode != 0 and check:
                raise SystemExit(f"vmrun {args[0]}: {output.read().strip()}")
            return said.returncode == 0

    def __enter__(self):
        self._vmrun("revertToSnapshot", self.vmx, self.snapshot)
        # nogui: start with no argument means gui, which Fusion refuses for a
        # VM a running-state snapshot left suspended ("The operation is not
        # supported"). Opening Fusion afterwards shows the running VM.
        self._vmrun("start", self.vmx, "nogui")
        if not self.guest.reachable(self.patience):
            raise SystemExit(f"{self.guest.alias} did not answer over SSH after the revert "
                             f"to {self.snapshot!r}")
        self._mount()
        self._wait_for_the_clock()
        print(f"vm:      {self.guest.alias} reverted to {self.snapshot!r}")
        return self

    def _mount(self) -> None:
        url = self.guest.settings.get("smb")
        if not url:
            return
        mount = str(self.guest.host_share)
        subprocess.run(["umount", mount], capture_output=True)
        password = self.guest.settings.get("smb_password")
        if password:
            user, _, where = url.removeprefix("//").partition("@")
            url = f"//{user}:{urllib.parse.quote(_secret(password), safe='')}@{where}"
        # -N: never prompt. Without smb_password, the Keychain's internet
        # password for the server (Finder's "Remember") has to do.
        said = subprocess.run(["mount_smbfs", "-N", url, mount],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if said.returncode != 0:
            raise SystemExit(f"could not mount the share on {mount}: {said.stderr.strip()}")

    def _wait_for_the_clock(self, patience: float = 60.0) -> None:
        """A snapshot comes back with the time it was taken; VMware Tools
        sets it right within seconds. Wait for that, so the run's own record
        of times means something."""
        deadline = time.monotonic() + patience
        while time.monotonic() < deadline:
            said = self.guest.ssh("[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()"
                                  if self.guest.windows else "date +%s")
            try:
                if abs(int(said.stdout.strip()) - time.time()) < 30:
                    return
            except ValueError:
                pass
            time.sleep(3)
        print("vm:      the guest's clock is still off - carrying on")

    def __exit__(self, *exc):
        if self.guest.settings.get("smb"):
            subprocess.run(["umount", str(self.guest.host_share)], capture_output=True)
        # hard: the snapshot it is reverted to next time is all that counts.
        if self._vmrun("stop", self.vmx, "hard", check=False):
            print(f"vm:      {self.guest.alias} stopped")
        else:
            print(f"vm:      could not stop {self.guest.alias} - stop it before opening Fusion")
        return False


def _sh_quote(word: str) -> str:
    return "'" + word.replace("'", "'\\''") + "'"


def _ps_quote(word: str) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + word.replace("'", "''") + "'"


def _task_xml(command: str, arguments: str) -> str:
    """A scheduled task that runs ``command`` in the creating user's desktop
    session (InteractiveToken), with no time limit. XML, not schtasks /tr:
    the program and its arguments stay apart, so nothing has to survive
    PowerShell's quoting of a native command line."""
    from xml.sax.saxutils import escape
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _end_tunnels(stage: pathlib.Path) -> None:
    """The reverse tunnels qa/guest_screens.py started for the screens'
    pages: it records each one's pid on the stage, and the operator that
    started them is gone by now."""
    for pid_file in (stage / "tunnels").glob("*.pid"):
        try:
            os.kill(int(pid_file.read_text()), 15)
        except (OSError, ValueError):
            pass


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
                _end_tunnels(self.stage)
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
