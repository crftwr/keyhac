"""The interpreter line the startup banner and every diagnostics report carry:
which Python, built for which CPU, and whether the GIL is off."""

import os
import platform
import subprocess
import sys
import sysconfig

import pytest

from keyhac.core import diagnostics


class TestPythonText:

    def test_names_the_running_python(self):
        line = diagnostics.python_text()
        assert line.startswith("Python " + platform.python_version())
        assert line.endswith(")")

    def test_a_free_threaded_build_with_the_gil_off(self):
        assert (diagnostics.python_text("3.15.2", True, False, "arm64")
                == "Python 3.15.2 arm64 (free-threaded, GIL off)")

    def test_a_free_threaded_build_that_turned_the_gil_back_on(self):
        # An extension module without free-threading support, or PYTHON_GIL=1.
        assert (diagnostics.python_text("3.15.2", True, True, "x64")
                == "Python 3.15.2 x64 (free-threaded build, GIL on)")

    def test_a_default_build(self):
        assert (diagnostics.python_text("3.14.4", False, True, "x64")
                == "Python 3.14.4 x64 (GIL on)")

    def test_names_the_running_architecture(self):
        assert f" {diagnostics.python_arch()} (" in diagnostics.python_text()

    def test_the_report_header_carries_it(self):
        assert any(line.startswith(diagnostics.python_text())
                   for line in diagnostics.header("Report"))


class TestPythonArch:

    def arch(self, tag, machine=None):
        return diagnostics.python_arch(tag, machine)

    def test_windows_reads_the_interpreter_build_tag(self):
        # An x64 Python emulated on ARM64 reports machine ARM64; it is x64.
        assert self.arch("win-amd64", "ARM64") == "x64"
        assert self.arch("win-arm64", "ARM64") == "arm64"
        assert self.arch("win32", "AMD64") == "x86"

    def test_macos_reads_the_process_machine(self):
        # A universal2 build names no one architecture; Rosetta says x86_64.
        assert self.arch("macosx-11.0-universal2", "arm64") == "arm64"
        assert self.arch("macosx-11.0-universal2", "x86_64") == "x64"

    def test_an_unknown_architecture_keeps_its_own_name(self):
        assert self.arch("linux-riscv64", "riscv64") == "riscv64"


#: What the apps import on each OS: Keyhac's platform layer and UI, PuiKit's
#: backend (numpy with it on Windows), and every third-party package.
_SHIPPED = {
    "darwin": ["keyhac.platform.mac", "puikit.backends.macos_backend"],
    "win32": ["keyhac.platform.win", "puikit.backends.windows_backend", "numpy"],
}


@pytest.mark.skipif(not sysconfig.get_config_var("Py_GIL_DISABLED"),
                    reason="a free-threaded build's question")
@pytest.mark.skipif(sys.platform not in _SHIPPED, reason="what an app ships")
def test_nothing_the_apps_import_turns_the_gil_back_on():
    """An extension module built without free-threading support turns the GIL
    back on when it is imported, with a warning nobody reads, and the build
    runs as a GIL one (#369). In a fresh interpreter, since this one may have
    imported anything by now; PYTHON_GIL is cleared, which would decide it."""
    modules = ["keyhac.main", "keyhac.ui.console", "keyhac.ui.chooser", "puikit",
               "PIL.Image", "migemo", "truststore", *_SHIPPED[sys.platform]]
    script = (f"import sys\n"
              f"for name in {modules!r}: __import__(name)\n"
              f"print(int(sys._is_gil_enabled()))")
    env = {k: v for k, v in os.environ.items() if k != "PYTHON_GIL"}
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "0", f"the GIL is back on:\n{done.stderr}"
