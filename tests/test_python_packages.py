"""keyhac.core.python_packages - ~/.keyhac/requirements.txt and the directory
synced from it (#272)."""

import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest import mock

import pytest

from keyhac import deps_cli
from keyhac.core import python_packages as pp
from keyhac.ui import tray


HOST = {"pyobjc-core": "11.1", "puikit": "0.9.0"}


def _write_dist(site: Path, name: str, version: str, files: dict[str, str],
                extra_record: tuple = ()) -> None:
    """A minimal installed distribution: its files plus a dist-info that
    lists them in RECORD, as pip leaves it."""
    info = site / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\n"
                                   f"Version: {version}\n", encoding="utf-8")
    record = [f"{info.name}/METADATA,,", f"{info.name}/RECORD,,"]
    for rel, text in files.items():
        path = site / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        record.append(f"{rel},,")
    record += [f"{rel},," for rel in extra_record]
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")


@pytest.fixture
def layout(tmp_path, monkeypatch):
    home = Path(os.path.realpath(tmp_path))
    monkeypatch.setattr(sys, "path", list(sys.path))
    return pp.Layout(home)


def write_requirements(layout, text):
    layout.requirements.write_text(text, encoding="utf-8")


def synced(layout, text, host=HOST, **override):
    """A target directory as a sync of ``text`` against ``host`` left it."""
    write_requirements(layout, text)
    layout.target.mkdir(parents=True)
    stamp = {**pp.make_stamp(text, host), **override}
    (layout.target / pp.STAMP).write_text(json.dumps(stamp), encoding="utf-8")


class TestCheck:

    def test_unused_without_a_file_or_a_directory(self, layout):
        assert pp.check(layout, HOST).state == pp.UNUSED

    def test_comments_alone_declare_nothing(self, layout):
        write_requirements(layout, "# requests\n\n")
        assert pp.check(layout, HOST).state == pp.UNUSED

    def test_a_file_never_synced_needs_a_sync(self, layout):
        write_requirements(layout, "requests\n")
        status = pp.check(layout, HOST)
        assert status.state == pp.NEEDS_SYNC
        assert not status.loaded
        assert "Sync Python Packages" in status.message
        assert "keyhac deps sync" in status.message

    def test_in_sync(self, layout):
        synced(layout, "requests\n")
        status = pp.check(layout, HOST)
        assert (status.state, status.loaded) == (pp.IN_SYNC, True)
        assert status.message == ""

    def test_an_edited_file_needs_a_sync_but_stays_loaded(self, layout):
        synced(layout, "requests\n")
        write_requirements(layout, "requests\nrich\n")
        status = pp.check(layout, HOST)
        assert (status.state, status.loaded) == (pp.NEEDS_SYNC, True)
        assert "changed since the last sync" in status.reason

    def test_a_deleted_file_needs_a_sync_to_remove_the_packages(self, layout):
        synced(layout, "requests\n")
        layout.requirements.unlink()
        assert pp.check(layout, HOST).state == pp.NEEDS_SYNC

    def test_changed_host_packages_need_a_sync(self, layout):
        synced(layout, "requests\n")
        status = pp.check(layout, {**HOST, "pyobjc-core": "12.0"})
        assert status.state == pp.NEEDS_SYNC
        assert "Keyhac's own packages" in status.reason

    def test_another_platform_is_not_loaded(self, layout):
        # One config runs on both OSes, so ~/.keyhac may come from the other.
        synced(layout, "requests\n", platform="cpython-314-win32-amd64")
        status = pp.check(layout, HOST)
        assert (status.state, status.loaded) == (pp.FOREIGN, False)

    def test_a_new_python_minor_version_names_the_old_one(self, layout):
        write_requirements(layout, "requests\n")
        (layout.root / "3.13").mkdir(parents=True)
        status = pp.check(pp.Layout(layout.home, version="3.14"), HOST)
        assert status.state == pp.NEEDS_SYNC
        assert "synced for Python 3.13" in status.reason


class TestActivate:

    def test_appends_the_directory_to_sys_path(self, layout):
        synced(layout, "x\n")
        pp.activate(layout)
        assert sys.path[-1] == str(layout.target)
        pp.activate(layout)
        assert sys.path.count(str(layout.target)) == 1

    def test_a_foreign_directory_stays_off_the_path(self, layout):
        synced(layout, "x\n", platform="elsewhere")
        pp.activate(layout)
        assert str(layout.target) not in sys.path

    def test_a_staged_sync_is_swapped_in_at_start(self, layout):
        synced(layout, "old\n")
        (layout.target / "old_marker").write_text("")
        layout.staging.mkdir()
        (layout.staging / pp.STAMP).write_text(json.dumps(pp.make_stamp("x", {})))
        (layout.staging / "new_marker").write_text("")
        pp.activate(layout)
        assert (layout.target / "new_marker").exists()
        assert not layout.staging.exists()
        assert not layout.retired.exists()

    def test_an_interrupted_sync_is_discarded(self, layout):
        synced(layout, "old\n")
        layout.staging.mkdir()
        (layout.staging / "half").write_text("")
        pp.activate(layout)
        assert not layout.staging.exists()
        assert (layout.target / pp.STAMP).exists()

    def test_a_config_load_activates_and_reports(self, engine, tmp_path,
                                                monkeypatch):
        # Keymap.configure is what puts the directory on sys.path, before
        # config.py runs, and says when a sync is due.
        synced(pp.Layout(tmp_path), "requests\n", platform=pp.platform_tag())
        (tmp_path / "requirements.txt").write_text("requests\nrich\n")
        warnings = []
        monkeypatch.setattr("keyhac.core.keymap.logger.warning", warnings.append)
        engine(lambda keymap: None)
        assert any("need a sync" in w for w in warnings)
        assert str(pp.Layout(tmp_path).target) in sys.path


class TestPrune:

    def test_host_packages_are_removed_and_the_rest_kept(self, layout):
        site = layout.home / "site"
        _write_dist(site, "pyobjc_core", "11.1",
                    {"objc/__init__.py": "", "objc/_objc.py": ""},
                    extra_record=("../../bin/outside",))
        _write_dist(site, "requests", "2.32.0", {"requests/__init__.py": ""})
        outside = layout.home / "bin" / "outside"
        outside.parent.mkdir()
        outside.write_text("keep me")
        assert pp._prune(site, HOST) == ["pyobjc_core"]
        assert sorted(p.name for p in site.iterdir()) == [
            "requests", "requests-2.32.0.dist-info"]
        assert outside.exists()


def _run_fails(*a, **k):
    return subprocess.CompletedProcess(a, 1)


def _run_ok(*a, **k):
    return subprocess.CompletedProcess(a, 0)


def _pypi(payload, digest):
    index = json.dumps({"urls": [
        {"packagetype": "sdist", "filename": "pip-26.0.tar.gz", "url": "sdist"},
        {"packagetype": "bdist_wheel", "filename": "pip-26.0-py3-none-any.whl",
         "url": "wheel", "digests": {"sha256": digest}}]}).encode()
    return lambda url: index if url == pp.PYPI_PIP_JSON else payload


class TestPipCommand:

    def test_the_interpreters_own_pip_comes_first(self, layout):
        assert pp.pip_command("py", layout, run=_run_ok) == ["py", "-m", "pip"]

    def test_else_the_wheel_ensurepip_carries(self, layout, monkeypatch):
        wheel = Path("/lib/ensurepip/_bundled/pip-26.0-py3-none-any.whl")
        monkeypatch.setattr(pp, "_bundled_pip_wheel", lambda: wheel)
        assert pp.pip_command("py", layout, run=_run_fails) == [
            "py", os.path.join(str(wheel), "pip")]

    def test_else_a_wheel_from_pypi_checked_and_cached(self, layout, monkeypatch):
        monkeypatch.setattr(pp, "_bundled_pip_wheel", lambda: None)
        payload = b"wheel bytes"
        opener = _pypi(payload, hashlib.sha256(payload).hexdigest())
        cmd = pp.pip_command("py", layout, run=_run_fails, opener=opener)
        cached = layout.pip_cache / "pip-26.0-py3-none-any.whl"
        assert cmd == ["py", os.path.join(str(cached), "pip")]
        assert cached.read_bytes() == payload

        def never(url):
            raise AssertionError("downloaded twice")

        # Once cached, nothing is downloaded again.
        assert pp.pip_command("py", layout, run=_run_fails, opener=never) == cmd

    def test_a_download_that_does_not_match_its_digest_is_refused(self, layout,
                                                                  monkeypatch):
        monkeypatch.setattr(pp, "_bundled_pip_wheel", lambda: None)
        with pytest.raises(pp.SyncError):
            pp.pip_command("py", layout, run=_run_fails,
                           opener=_pypi(b"tampered", "0" * 64))
        assert not layout.pip_cache.exists()

    def test_pip_never_writes_bytecode_into_the_interpreter(self, layout):
        # The macOS bundle is signed; a .pyc written into it breaks the seal.
        seen = {}

        def run(argv, **kw):
            seen.update(kw["env"])
            return _run_ok()

        pp.pip_command("py", layout, run=run)
        assert seen["PYTHONDONTWRITEBYTECODE"] == "1"


class TestKeyhacPython:

    def test_a_real_interpreter_is_itself(self, monkeypatch):
        monkeypatch.setattr(sys, "executable", "/venv/bin/python3.14")
        assert pp.keyhac_python() == "/venv/bin/python3.14"

    def test_the_macos_bundle_runs_the_frameworks_python(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(sys, "executable", "/Applications/Keyhac.app/Contents/MacOS/Keyhac")
        framework = "/Applications/Keyhac.app/Contents/Frameworks/Python.framework/Versions/3.14"
        monkeypatch.setattr(sys, "base_prefix", framework)
        assert pp.keyhac_python() == os.path.join(framework, "bin", "python3")

    def test_the_windows_bundle_runs_the_embedded_python(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "executable", str(tmp_path / "Keyhac.exe"))
        monkeypatch.setattr(pp.paths, "bundle_dir", lambda: str(tmp_path))
        assert pp.keyhac_python() == os.path.join(str(tmp_path), "runtime", "python.exe")


class _FakePip:
    """Stands in for ``subprocess.Popen`` running pip: installs the given
    distributions into ``--target`` and prints, or fails with ``output``."""

    def __init__(self, dists=(), code=0, output=("Successfully installed",)):
        self.dists, self.code, self.output = dists, code, output
        self.argv = None

    def __call__(self, argv, **kwargs):
        self.argv, self.kwargs = argv, kwargs
        target = Path(argv[argv.index("--target") + 1])
        if self.code == 0:
            for name, version, files in self.dists:
                _write_dist(target, name, version, files)
        proc = mock.Mock()
        proc.stdout = io.StringIO("".join(line + "\n" for line in self.output))
        proc.wait.return_value = self.code
        return proc


def _no_pip(*a, **k):
    raise AssertionError("pip ran")


class TestSync:

    @pytest.fixture(autouse=True)
    def host(self, monkeypatch):
        monkeypatch.setattr(pp, "host_distributions", lambda layout: dict(HOST))

    def sync(self, layout, popen):
        lines = []
        result = pp.sync(layout, python="py", log=lines.append,
                         run=_run_ok, popen=popen)
        return result, lines

    def test_installs_prunes_stamps_and_loads(self, layout):
        write_requirements(layout, "requests\n")
        fake = _FakePip(dists=[("requests", "2.32.0", {"requests/__init__.py": ""}),
                               ("pyobjc_core", "11.1", {"objc/__init__.py": ""})])
        result, lines = self.sync(layout, fake)
        assert result.installed == ["requests 2.32.0"]
        assert result.pruned == ["pyobjc_core"]
        assert not result.pending_restart
        assert lines == ["Successfully installed"]
        assert pp.check(layout, HOST).state == pp.IN_SYNC
        assert str(layout.target) in sys.path
        assert not (layout.target / "objc").exists()
        # pip ran with the host packages as constraints, from the data dir.
        argv = fake.argv
        assert argv[:4] == ["py", "-m", "pip", "install"]
        assert argv[argv.index("-r") + 1] == str(layout.requirements)
        assert fake.kwargs["cwd"] == str(layout.home)

    def test_constraints_pin_the_host_versions(self, layout):
        write_requirements(layout, "requests\n")
        seen = {}

        def popen(argv, **kw):
            seen["constraints"] = Path(argv[argv.index("-c") + 1]).read_text()
            return _FakePip()(argv, **kw)

        self.sync(layout, popen)
        assert seen["constraints"] == "puikit==0.9.0\npyobjc-core==11.1\n"

    def test_a_removed_line_removes_its_package(self, layout):
        write_requirements(layout, "a\nb\n")
        self.sync(layout, _FakePip(dists=[("a", "1", {"a.py": ""}),
                                          ("b", "1", {"b.py": ""})]))
        write_requirements(layout, "a\n")
        result, _ = self.sync(layout, _FakePip(dists=[("a", "1", {"a.py": ""})]))
        assert result.installed == ["a 1"]
        assert not (layout.target / "b.py").exists()

    def test_an_empty_file_removes_the_directory_without_pip(self, layout):
        write_requirements(layout, "a\n")
        self.sync(layout, _FakePip(dists=[("a", "1", {"a.py": ""})]))
        write_requirements(layout, "")
        result, _ = self.sync(layout, _no_pip)
        assert result.installed == []
        assert not layout.root.exists()
        assert pp.check(layout, HOST).state == pp.UNUSED

    def test_nothing_declared_and_nothing_synced_creates_nothing(self, layout):
        result, _ = self.sync(layout, _no_pip)
        assert result.installed == []
        assert not layout.root.exists()

    def test_a_conflict_with_a_host_package_names_it_and_changes_nothing(self, layout):
        synced(layout, "old\n")
        write_requirements(layout, "pyobjc-core>=99\n")
        fake = _FakePip(code=1, output=(
            "ERROR: Cannot install pyobjc-core>=99 because these package "
            "versions have conflicting dependencies.",
            "The conflict is caused by:",
            "    The user requested pyobjc-core>=99",
            "    The user requested (constraint) pyobjc-core==11.1",
            "ERROR: ResolutionImpossible"))
        with pytest.raises(pp.SyncError) as caught:
            self.sync(layout, fake)
        assert ("The user requested pyobjc-core>=99; The user requested "
                "(constraint) pyobjc-core==11.1") in str(caught.value)
        assert not layout.staging.exists()
        assert (pp.read_stamp(layout.target)["requirements"]
                == pp.make_stamp("old\n", HOST)["requirements"])

    def test_a_target_in_use_is_replaced_at_the_next_start(self, layout, monkeypatch):
        synced(layout, "old\n")
        write_requirements(layout, "a\n")
        real_replace = os.replace

        def replace(src, dst):
            if Path(src) == layout.target:
                raise PermissionError("in use")
            return real_replace(src, dst)

        monkeypatch.setattr(pp.os, "replace", replace)
        result, _ = self.sync(layout, _FakePip(dists=[("a", "1", {"a.py": ""})]))
        monkeypatch.setattr(pp.os, "replace", real_replace)
        assert result.pending_restart
        assert layout.staging.is_dir()
        pp.activate(layout)
        assert (layout.target / "a.py").exists()
        assert pp.check(layout, HOST).state == pp.IN_SYNC


def _wheel(directory: Path, name: str, version: str, module: str) -> Path:
    """A real, installable pure-Python wheel - no build backend, no index."""
    path = directory / f"{name}-{version}-py3-none-any.whl"
    info = f"{name}-{version}.dist-info"
    files = {
        f"{module}.py": "VALUE = 42\n",
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n"
                         "Tag: py3-none-any\n",
    }
    record = []
    for rel, text in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(text.encode()).digest())
        record.append(f"{rel},sha256={digest.rstrip(b'=').decode()},{len(text.encode())}")
    record.append(f"{info}/RECORD,,")
    with zipfile.ZipFile(path, "w") as archive:
        for rel, text in files.items():
            archive.writestr(rel, text)
        archive.writestr(f"{info}/RECORD", "\n".join(record) + "\n")
    return path


def _has_pip() -> bool:
    if pp._bundled_pip_wheel() is not None:
        return True
    return subprocess.run([sys.executable, "-m", "pip", "--version"],
                          capture_output=True).returncode == 0


@pytest.mark.skipif(not _has_pip(), reason="no pip")
def test_real_pip_installs_an_importable_package(layout, monkeypatch):
    """The whole sync through the real pip, offline: a local wheel and
    ``--no-index``."""
    _wheel(layout.home, "keyhac_t272_demo", "1.0", "keyhac_t272_demo")
    write_requirements(layout,
                       "--no-index\n./keyhac_t272_demo-1.0-py3-none-any.whl\n")
    result = pp.sync(layout, python=sys.executable)
    assert result.installed == ["keyhac_t272_demo 1.0"]
    monkeypatch.delitem(sys.modules, "keyhac_t272_demo", raising=False)
    import keyhac_t272_demo
    assert keyhac_t272_demo.VALUE == 42
    assert pp.check(layout).state == pp.IN_SYNC


class TestDepsCli:

    def test_status_exit_codes(self, layout, capsys):
        assert deps_cli.main(["status"], layout=layout) == 0
        write_requirements(layout, "a\n")
        assert deps_cli.main(["status"], layout=layout) == 1

    def test_a_failed_sync_exits_nonzero(self, layout, monkeypatch, capsys):
        def boom(*a, **k):
            raise pp.SyncError("boom")

        monkeypatch.setattr(pp, "sync", boom)
        assert deps_cli.main(["sync"], layout=layout) == 1
        assert "sync failed: boom" in capsys.readouterr().out

    def test_config_names_the_data_directory(self, tmp_path, capsys):
        (tmp_path / "requirements.txt").write_text("a\n")
        assert deps_cli.main(["-c", str(tmp_path / "config.py"), "status"]) == 1
        assert str(tmp_path / "requirements.txt") in capsys.readouterr().out


class _Keymap:
    """What PackageSync needs of a Keymap: the data directory, the main
    thread, and a config reload."""

    def __init__(self, home):
        self.extensions_dir = str(home / "extensions")
        self.reloads = 0

    def call_on_main_thread(self, callback):
        callback()

    def configure(self):
        self.reloads += 1


class TestTraySync:

    def run(self, package_sync):
        package_sync.start()
        # The worker hands its outcome to call_on_main_thread, which runs it
        # inline here; wait for the thread to have done so.
        for thread in __import__("threading").enumerate():
            if thread.name == "keyhac-deps-sync":
                thread.join(5)

    def test_a_sync_reloads_the_config(self, layout):
        keymap = _Keymap(layout.home)
        seen = []

        def sync(layout, log):
            seen.append(layout.home)
            log("pip says")
            return pp.SyncResult(["a 1"], [])

        package_sync = tray.PackageSync(keymap, sync=sync)
        self.run(package_sync)
        assert seen == [layout.home]
        assert keymap.reloads == 1
        assert not package_sync.running

    def test_a_failed_sync_changes_nothing(self, layout):
        keymap = _Keymap(layout.home)

        def sync(layout, log):
            raise pp.SyncError("boom")

        package_sync = tray.PackageSync(keymap, sync=sync)
        self.run(package_sync)
        assert keymap.reloads == 0
        assert not package_sync.running

    def test_a_restart_pending_sync_does_not_reload(self, layout):
        keymap = _Keymap(layout.home)
        package_sync = tray.PackageSync(
            keymap, sync=lambda layout, log: pp.SyncResult([], [], pending_restart=True))
        self.run(package_sync)
        assert keymap.reloads == 0
