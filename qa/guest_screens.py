"""The operator's screens, put up in the VM the run's Keyhac is in (#271).

quality-loop loads this file as the adapter's screen backend
(`Keyhac.screen_backend`, quality_loop.screens.load_backend) in place of this
machine's, so `screen_open` puts a page or a terminal up on the guest's
desktop - where the room's Keyhac reads the screen - and not on the host's.

Each operation is quality-loop's own (screens._mac / _windows), run in the
guest by qa/guest_boot/screen_agent.py from the run's stage: over SSH on
macOS, from a scheduled task in the desktop session on Windows. A request
and its answer are files on the share.

Pages are served on the host's 127.0.0.1 by the runner. A reverse tunnel
(`ssh -R`, same port) makes that address the guest's too, so the URL, and the
page's POSTed events, work unchanged. One tunnel per port, started by the
first page that needs it; its pid is kept on the stage, and the run's end
(qa/guest.py, GuestKeyhacUp) ends it.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.parse
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import guest as guests  # noqa: E402

from quality_loop.screens import ScreenError  # noqa: E402

_guest: guests.Guest | None = None
_stage: pathlib.Path | None = None


def configure(settings: dict) -> None:
    """From `Keyhac.screen_backend`: the guest as this run reaches it."""
    global _guest, _stage
    _guest = guests.Guest(settings["alias"], pathlib.Path(settings["host_share"]),
                          settings["guest_share"], settings["keyhac"])
    _guest._windows = settings["windows"]
    _guest.hostname = settings.get("hostname")
    _stage = pathlib.Path(settings["stage"])


def _call(request: dict, patience: float):
    """Run one operation in the guest and answer its value."""
    requests = _stage / "screens"
    requests.mkdir(exist_ok=True)
    name = uuid.uuid4().hex[:12]
    asked, answered = requests / f"{name}.json", requests / f"{name}-answer.json"
    request = dict(request, result=_guest.path(answered))
    asked.write_text(json.dumps(request), encoding="utf-8")
    agent = _stage / "boot" / "screen_agent.py"
    task = f"keyhac-qa-screen-{name}"
    try:
        if _guest.windows:
            said = _guest.run_in_session(_stage, task, agent, _guest.path(asked))
            if "SUCCESS: Attempted to run" not in said.stdout:
                raise ScreenError(f"could not run in the VM's session: {said.stdout}{said.stderr}")
        else:
            # Synchronous over SSH; Safari and Terminal are driven through
            # Apple Events, which the guest's base image has allowed for SSH.
            said = _guest.ssh(f"{guests._sh_quote(_guest.python)} "
                              f"{guests._sh_quote(_guest.path(agent))} "
                              f"{guests._sh_quote(_guest.path(asked))}",
                              timeout=patience + 120)
            if said.returncode != 0 and not answered.exists():
                raise ScreenError(f"the VM did not run it: {said.stderr.strip()[:500]}")
        deadline = time.monotonic() + patience + 60
        while not answered.exists():
            if time.monotonic() > deadline:
                raise ScreenError(f"no answer from the VM within {patience + 60:.0f}s")
            time.sleep(0.5)
        answer = json.loads(answered.read_text(encoding="utf-8"))
    finally:
        if _guest.windows:
            _guest.forget_task(_stage, task)
        for leftover in (asked, answered):
            leftover.unlink(missing_ok=True)
    if not answer["ok"]:
        raise ScreenError(f"in the VM: {answer['error']}")
    return answer["value"]


def _tunnel(port: int) -> None:
    """The host's 127.0.0.1:port as the guest's, once per port."""
    tunnels = _stage / "tunnels"
    tunnels.mkdir(exist_ok=True)
    pid_file = tunnels / f"{port}.pid"
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text()), 0)
            return
        except (OSError, ValueError):
            pass
    process = subprocess.Popen(
        ["ssh", "-N", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
         *_guest._ssh_options(), "-R", f"{port}:127.0.0.1:{port}", _guest.alias],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    pid_file.write_text(str(process.pid))
    time.sleep(2)
    if process.poll() is not None:
        pid_file.unlink(missing_ok=True)
        raise ScreenError(f"could not forward the pages' port {port} into the VM")


def open_page(url: str, title: str, patience: float):
    _tunnel(urllib.parse.urlsplit(url).port)
    return _call({"op": "open_page", "url": url, "title": title, "patience": patience},
                 patience)


def open_terminal(command: list[str], title: str, patience: float):
    # The runner's command is this machine's: its interpreter, and a copy of
    # the script outside the checkout. The guest gets its own interpreter and
    # a copy of the script on the share.
    script = pathlib.Path(command[-1])
    scripts = _stage / "scripts"
    scripts.mkdir(exist_ok=True)
    shutil.copy2(script, scripts / script.name)
    guest_command = [_guest.python, _guest.path(scripts / script.name)]
    return _call({"op": "open_terminal", "command": guest_command, "title": title,
                  "patience": patience}, patience)


def close(kind: str, handle) -> None:
    _call({"op": "close", "kind": kind, "handle": handle}, 15)
