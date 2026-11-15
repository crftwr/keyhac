"""The interpreter line the startup banner and every diagnostics report carry:
which Python, built for which CPU, and whether the GIL is off."""

import platform

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
