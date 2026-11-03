"""This working tree in a VM: the test suite, or Keyhac up for a hand check.

    python3 tools/vm.py test [--packages pypi] <guest> [pytest args...]   # make test-vm GUEST=...
    python3 tools/vm.py run <guest>                                       # make run-vm GUEST=...

<guest> is an SSH host alias with a table in ~/.config/quality-loop/guests.toml
(qa-win, qa-mac; the images are shared with XeFM). Each run starts the VM from
its clean state, stages what is under test (qa/guest.py), and puts the VM away
afterwards. Nothing is left in the image.

**test** stages this working tree as git sees it, installs the third-party
packages into a fresh venv from the image's Python, and runs pytest there with
--desktop through quality-loop's runner (`quality_loop.guest.suite`): the
tests marked `desktop` take the foreground, move windows and send real input,
which on your own machine they skip, and here nobody is at the guest's
desktop. Windows runs pytest in the desktop session from a scheduled task,
with python.exe; macOS over SSH. ``--packages pypi`` installs PuiKit and
quality-loop from PyPI as pyproject.toml names them - a release's run (#316) -
instead of staging this checkout's. The log and the JUnit XML are kept in
temp/vm-test-<guest>-<time>/; the exit code is pytest's.

**run** starts this working tree's Keyhac in the VM's desktop session - on
macOS through Keyhac.app, which holds the Accessibility grant - with a home of
its own on the stage, and waits for Enter. A VMware VM is started headless
and then suspended, for you to open in the Fusion app; a Tart VM shows its
window when the table says `visible = true`.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qa"))

import guest as keyhac_guest  # noqa: E402  (qa/guest.py)
from quality_loop import guest as ql  # noqa: E402
from quality_loop.guest import suite  # noqa: E402


def run_tests(alias: str, pytest_args: list[str], patience: float, packages: str) -> int:
    guest = keyhac_guest.Guest.for_alias(alias)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    results = ROOT / "temp" / f"vm-test-{alias}-{stamp}"
    started = time.monotonic()
    code = 3
    with guest.machine():
        stage = guest.test_stage(packages)
        try:
            print(f"vm:      installing the third-party packages in the guest "
                  f"(ours: {packages})...", flush=True)
            python = guest.venv(stage, keyhac_guest.third_party_requirements(True, packages))
            args = ["--desktop", *(pytest_args or ["tests"])]
            tree = guest.path(stage / "tree")
            separator = ";" if guest.windows else ":"
            environ = {"PYTHONPATH": separator.join([guest.path(stage / "lib"), tree]),
                       "PYTHONIOENCODING": "utf-8"}
            print(f"vm:      pytest {' '.join(args)} in {alias}", flush=True)
            code = suite.run_pytest(guest, stage, python, args, cwd=tree, results=results,
                                    environ=environ,
                                    path=environ["PYTHONPATH"].split(separator),
                                    patience=patience, task="keyhac-test")
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    print(f"vm:      pytest exited {code} in {time.monotonic() - started:.0f}s; "
          f"log and JUnit XML in {results.relative_to(ROOT)}")
    return code


def run_keyhac(alias: str) -> int:
    guest = keyhac_guest.Guest.for_alias(alias)
    machine = guest.machine()
    machine.__enter__()
    stage = None
    try:
        stage = guest.stage()
        home = stage / "home"
        (home / ".keyhac").mkdir(parents=True)
        guest.start(home, stage, stage / "keyhac.log", stage / "quit")
        print(f"vm:      this working tree's Keyhac is starting in {alias}'s desktop "
              f"session; home {guest.path(home)}, log {stage / 'keyhac.log'}")
        if isinstance(machine, ql.VMwareMachine):
            # A headless VM does not show in the Fusion app; a suspended one
            # does, and resumes there.
            machine._vmrun("suspend", machine.vmx)
            print("vm:      suspended - open it in the VMware Fusion app to resume it there")
        elif isinstance(machine, ql.TartMachine) and not guest.settings.get("visible"):
            print("vm:      the table does not say visible = true, so there is no window: "
                  f"reach it with `ssh {alias}`")
        input("vm:      press Enter here when you are done, to put the VM away... ")
        return 0
    except (KeyboardInterrupt, EOFError):
        print()
        return 0
    finally:
        if stage is not None:
            (stage / "quit").touch()
            guest.forget_task(guest.task(stage))
        with contextlib.suppress(Exception):
            machine.__exit__(None, None, None)
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    test = commands.add_parser("test", help="run the test suite in the VM")
    test.add_argument("--packages", choices=keyhac_guest.PACKAGES, default="local",
                      help="ours from this checkout (local, the default) or PyPI")
    test.add_argument("--patience", type=float, default=3600,
                      help="seconds to wait for pytest to end (default 3600)")
    test.add_argument("guest")
    test.add_argument("pytest_args", nargs=argparse.REMAINDER,
                      help="for pytest (default: tests)")
    running = commands.add_parser("run", help="Keyhac up in the VM, for a hand check")
    running.add_argument("guest")
    args = parser.parse_args()
    if args.command == "test":
        return run_tests(args.guest, args.pytest_args, args.patience, args.packages)
    return run_keyhac(args.guest)


if __name__ == "__main__":
    sys.exit(main())
