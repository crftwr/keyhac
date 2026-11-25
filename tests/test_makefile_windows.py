"""``make`` on Windows when the Windows environment did not reach it.

Git Bash starting MSYS2's make hands it almost no variables. ``py`` (the Python
install manager) then took puikit's checkout for ``%LocalAppData%``: it installed
a Python into it and pointed the user's registry entries at that one. The
Makefile stops instead.

Nothing here can reach the real ``py``: it is not on the PATH these give make,
and a ``py.bat`` that only writes down what it was handed is.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MSYS_BIN = Path(r"C:\msys64\usr\bin")
SYSTEM = Path(os.environ.get("SystemRoot", r"C:\Windows"))

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not (MSYS_BIN / "make.exe").is_file(),
    reason="needs MSYS2's make, on Windows",
)


@pytest.fixture
def make(tmp_path):
    """Run make with almost no environment: the result, and what ``py`` was
    handed if it was asked."""
    tools = tmp_path / "bin"
    tools.mkdir()
    log = tmp_path / "py.log"
    (tools / "py.bat").write_text(
        "@echo off\r\n"
        f'>>"{log}" echo [%LOCALAPPDATA%] [%PYTHON_MANAGER_AUTOMATIC_INSTALL%]\r\n'
        "echo C:\\found\\python.exe\r\n"
    )

    def run(*args, path=(), **env):
        path = os.pathsep.join(map(str, [tools, MSYS_BIN, SYSTEM / "System32", *path]))
        found = shutil.which("py", path=path)
        assert found and Path(found).parent == tools  # never the real one
        done = subprocess.run(
            [str(MSYS_BIN / "make.exe"), *args], cwd=ROOT, capture_output=True, text=True,
            errors="replace", timeout=60,
            env={"PATH": path, "SystemRoot": str(SYSTEM), "HOME": str(tmp_path), **env})
        return done, log.read_text().split("\n")[:-1] if log.exists() else []

    return run


def test_make_stops_when_the_windows_environment_did_not_reach_it(make):
    for target in ("help", "venv", "test"):
        done, asked = make("-n", target)
        assert done.returncode != 0 and "LOCALAPPDATA is empty" in done.stderr
        assert asked == []


def test_a_target_that_needs_no_python_of_its_own_does_not_ask_py(make):
    done, asked = make("-n", "help", LOCALAPPDATA=r"C:\somewhere")
    assert done.returncode == 0, done.stderr
    assert asked == []
