"""The test suite in a VM, desktop tests included (#293).

    make test-vm GUEST=qa-win                          # everything, --desktop
    make test-vm GUEST=qa-mac ARGS="tests/test_mac_window.py"

<guest> is an SSH host alias with a table in ~/.config/quality-loop/guests.toml
(qa-win, qa-mac; the images are shared with XeFM). The run starts the VM from
its clean state, stages this working tree as git sees it and the PuiKit this
checkout's environment runs, installs the third-party packages into a fresh
venv from the image's Python (qa/guest.py), runs pytest there with --desktop,
and puts the VM away afterwards. Nothing is left in the image.

On your own machine `pytest` skips the tests marked `desktop` (tests/conftest.py):
they take the foreground, move windows and send real input. Here nobody is at
the guest's desktop, so they run. Windows runs pytest in the desktop session
from a scheduled task, with python.exe; macOS over SSH.

The log and the JUnit XML are kept in temp/vm-test-<guest>-<time>/; the exit
code is pytest's.
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "qa"))

import guest as keyhac_guest  # noqa: E402  (qa/guest.py)
from quality_loop import guest as ql  # noqa: E402

#: What the suite needs beyond Keyhac's own requirements: pyproject's dev
#: extra (test_qa needs quality-loop, and skips without it).
TEST_REQUIREMENTS = ["pytest>=8", "quality-loop>=0.4.1"]


def working_tree() -> list[str]:
    """This checkout's files as git sees them: tracked, and untracked but not
    ignored - so .venv and cleanroom/ stay behind."""
    said = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                          capture_output=True, text=True, check=True)
    return [p for p in said.stdout.split("\0") if p and (ROOT / p).is_file()]


def stage(guest) -> pathlib.Path:
    """quality-loop's stage, with the tree in <stage>/tree and PuiKit in
    <stage>/lib."""
    where = ql.Guest.stage(guest, "keyhac-test-")
    for name in working_tree():
        target = where / "tree" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in keyhac_guest.STAGED:
        package = pathlib.Path(__import__(name).__file__).parent
        shutil.copytree(package, where / "lib" / name, ignore=ignore)
    return where


def _follow(log: pathlib.Path, done: pathlib.Path, patience: float) -> int:
    """Print ``log`` as it grows until ``done`` holds an exit code."""
    position, deadline, finished_at = 0, time.monotonic() + patience, None
    while True:
        finished = done.exists()
        chunk = ""
        if log.exists():
            with log.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(position)
                chunk = f.read()
                position = f.tell()
            if chunk:
                sys.stdout.write(chunk)
                sys.stdout.flush()
        if finished:
            # Over SMB the log's last writes can show up after the exit code
            # does: read on until it has stopped growing.
            if chunk or (time.monotonic() - finished_at if finished_at else 0) < 3:
                finished_at = finished_at or time.monotonic()
                time.sleep(1)
                continue
            return int(done.read_text(encoding="utf-8").strip() or 3)
        if time.monotonic() > deadline:
            print(f"\nvm:      no end within {patience:.0f}s", flush=True)
            return 3
        time.sleep(1)


def run_tests(alias: str, pytest_args: list[str], patience: float) -> int:
    guest = keyhac_guest.Guest.from_settings(alias, ql.settings_for(alias))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    results = ROOT / "temp" / f"vm-test-{alias}-{stamp}"
    started = time.monotonic()
    code = 3
    with guest.machine():
        where = stage(guest)
        task = f"keyhac-test-{where.name}"
        log, done, junit = where / "pytest.log", where / "exit-code", where / "junit.xml"
        try:
            print("vm:      installing the third-party packages in the guest...", flush=True)
            python = guest.venv(where, keyhac_guest.third_party_requirements()
                                + TEST_REQUIREMENTS)
            args = ["-p", "no:cacheprovider", "--desktop", f"--junitxml={guest.path(junit)}",
                    *(pytest_args or ["tests"])]
            runner = guest.path(where / "tree" / "tools" / "vm_guest_pytest.py")
            tree = guest.path(where / "tree")
            separator = ";" if guest.windows else ":"
            environ = {"PYTHONPATH": separator.join([guest.path(where / "lib"), tree]),
                       "PYTHONIOENCODING": "utf-8"}
            print(f"vm:      pytest --desktop {' '.join(args[4:])} in {alias}", flush=True)
            if guest.windows:
                # python.exe, not pythonw: a test that runs a console program
                # needs a console to inherit.
                guest.launch(where, task, python, script=runner, log=log, environ=environ,
                             path=environ["PYTHONPATH"].split(";"), cwd=tree,
                             argv=[guest.path(done), *args])
                code = _follow(log, done, patience)
            else:
                command = (f"cd {ql.sh_quote(tree)} && "
                           + " ".join(f"{k}={ql.sh_quote(v)}" for k, v in environ.items())
                           + f" {ql.sh_quote(python)} {ql.sh_quote(runner)} "
                           f"{ql.sh_quote(guest.path(done))} "
                           + " ".join(ql.sh_quote(a) for a in args))
                with log.open("w", encoding="utf-8") as out:
                    process = subprocess.Popen(
                        ["ssh", "-o", "BatchMode=yes", *guest.ssh_options(), guest.alias, command],
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, text=True, errors="replace")
                    for line in process.stdout:
                        sys.stdout.write(line)
                        out.write(line)
                    process.wait()
                code = int(done.read_text().strip()) if done.exists() else 3
        finally:
            results.mkdir(parents=True, exist_ok=True)
            for kept in (log, junit):
                if kept.exists():
                    shutil.copy2(kept, results / kept.name)
            if guest.windows:
                guest.forget_task(task)
            shutil.rmtree(where, ignore_errors=True)
    print(f"vm:      pytest exited {code} in {time.monotonic() - started:.0f}s; "
          f"log and JUnit XML in {results.relative_to(ROOT)}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("guest")
    parser.add_argument("--patience", type=float, default=3600,
                        help="seconds to wait for pytest to end (default 3600)")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER,
                        help="for pytest (default: tests)")
    args = parser.parse_args()
    return run_tests(args.guest, args.pytest_args, args.patience)


if __name__ == "__main__":
    sys.exit(main())
