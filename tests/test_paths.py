"""keyhac.core.paths - config/data directory resolution, incl. Windows
portable mode (a config.py next to Keyhac.exe)."""

import os
import sys

import pytest

from keyhac.core import paths


def _make_bundle(root, with_config=True):
    """A directory shaped like the Windows bundle windows_app/build.ps1
    assembles: Keyhac.exe at the root, the app package under app\\keyhac."""
    (root / "app" / "keyhac").mkdir(parents=True)
    exe = root / "Keyhac.exe"
    exe.write_text("")
    if with_config:
        (root / "config.py").write_text("def configure(keymap):\n    pass\n")
    return exe


class TestDefaults:

    def test_default_is_the_home_directory(self, monkeypatch):
        monkeypatch.setattr(paths, "portable_dir", lambda: None)
        resolved = paths.resolve()
        assert resolved.config_path == os.path.join(paths.default_data_dir(), "config.py")
        assert resolved.data_dir == paths.default_data_dir()
        assert resolved.portable is False

    def test_explicit_config_puts_state_beside_it(self, tmp_path, monkeypatch):
        # An explicit --config wins over portable mode, so a sandboxed run
        # stays sandboxed even when launched from a portable install.
        monkeypatch.setattr(paths, "portable_dir", lambda: str(tmp_path / "bundle"))
        target = tmp_path / "sandbox" / "config.py"
        resolved = paths.resolve(str(target))
        assert resolved.config_path == str(target)
        assert resolved.data_dir == str(tmp_path / "sandbox")
        assert resolved.portable is False

    def test_state_files_sit_beside_the_config(self, tmp_path):
        resolved = paths.resolve(str(tmp_path / "config.py"))
        assert resolved.state_file("clipboard.json") == str(tmp_path / "clipboard.json")
        assert resolved.state_file("settings.json") == str(tmp_path / "settings.json")

    def test_relative_config_is_absolute(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        resolved = paths.resolve("config.py")
        assert os.path.isabs(resolved.config_path)
        assert resolved.config_path == str(tmp_path / "config.py")


class TestBundleDetection:
    """bundle_dir() keys on the bundle's layout, not on the exe's name."""

    def test_none_off_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        assert paths.bundle_dir() is None
        assert paths.portable_dir() is None
        assert paths.legacy_windows_data_dir() is None

    def test_recognizes_the_bundle_layout(self, tmp_path, monkeypatch):
        exe = _make_bundle(tmp_path)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "executable", str(exe))
        assert paths.bundle_dir() == str(tmp_path)

    def test_a_renamed_launcher_still_finds_its_bundle(self, tmp_path, monkeypatch):
        _make_bundle(tmp_path)
        renamed = tmp_path / "MyKeys.exe"
        renamed.write_text("")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "executable", str(renamed))
        assert paths.bundle_dir() == str(tmp_path)

    def test_the_bundled_interpreter_finds_the_root_above_it(self, tmp_path,
                                                             monkeypatch):
        """The MCP bridge runs <root>\\runtime\\python.exe, not Keyhac.exe.
        Looking only at that directory left the bridge outside portable mode
        while the daemon was inside it: it read ~/.keyhac/mcp.json and the
        daemon wrote <root>\\mcp.json, so a stdio client got "endpoint is not
        available" with Keyhac running."""
        _make_bundle(tmp_path)
        runtime = tmp_path / "runtime"
        runtime.mkdir()
        (runtime / "python.exe").write_text("")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "executable", str(runtime / "python.exe"))
        assert paths.bundle_dir() == str(tmp_path)
        assert paths.resolve().data_dir == str(tmp_path)

    def test_a_plain_interpreter_is_not_a_bundle(self, tmp_path, monkeypatch):
        # `python -m keyhac` from a checkout: sys.executable is a (possibly
        # venv) python.exe whose directory has no app\keyhac in it, so a
        # stray config.py sitting next to it must not turn portable mode on.
        scripts = tmp_path / "Scripts"
        scripts.mkdir()
        (scripts / "python.exe").write_text("")
        (scripts / "config.py").write_text("")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
        assert paths.bundle_dir() is None
        assert paths.portable_dir() is None


class TestPortableMode:

    def test_config_next_to_the_exe_turns_it_on(self, tmp_path, monkeypatch):
        exe = _make_bundle(tmp_path)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "executable", str(exe))

        resolved = paths.resolve()
        assert resolved.portable is True
        assert resolved.data_dir == str(tmp_path)
        assert resolved.config_path == str(tmp_path / "config.py")
        # State follows the config, so nothing lands in the user profile.
        assert resolved.state_file("clipboard.json") == str(tmp_path / "clipboard.json")

    def test_a_bundle_without_a_config_is_not_portable(self, tmp_path, monkeypatch):
        # The whole opt-in is dropping config.py next to the exe; an ordinary
        # installed Keyhac has none there and keeps using ~/.keyhac.
        exe = _make_bundle(tmp_path, with_config=False)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "executable", str(exe))
        assert paths.portable_dir() is None
        assert paths.resolve().portable is False

    def test_a_config_directory_does_not_count(self, tmp_path, monkeypatch):
        exe = _make_bundle(tmp_path, with_config=False)
        (tmp_path / "config.py").mkdir()
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "executable", str(exe))
        assert paths.portable_dir() is None


class TestLegacyWindowsDataDir:

    def test_found_when_it_exists(self, tmp_path, monkeypatch):
        appdata = tmp_path / "AppData" / "Roaming"
        (appdata / "Keyhac").mkdir(parents=True)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", str(appdata))
        assert paths.legacy_windows_data_dir() == str(appdata / "Keyhac")

    def test_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("APPDATA", str(tmp_path))
        assert paths.legacy_windows_data_dir() is None

    def test_none_without_appdata(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.delenv("APPDATA", raising=False)
        assert paths.legacy_windows_data_dir() is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only migration offer")
class TestConfigMigration:
    """platform/win/migrate.py - the first-run offer to copy a 1.x config.
    The message box is stubbed; what is under test is when it is asked at all
    and what the answer does."""

    @pytest.fixture
    def migrate(self):
        from keyhac.platform.win import migrate
        return migrate

    def _stub_answer(self, migrate, monkeypatch, answer):
        asked = []

        class FakeUser32:
            def MessageBoxW(self, hwnd, text, caption, flags):
                asked.append(text)
                return answer

        monkeypatch.setattr(migrate, "user32", FakeUser32())
        return asked

    def test_copies_on_yes(self, tmp_path, monkeypatch, migrate):
        legacy = tmp_path / "appdata" / "Keyhac"
        legacy.mkdir(parents=True)
        (legacy / "config.py").write_text("# 1.x config\n")
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        asked = self._stub_answer(migrate, monkeypatch, migrate.IDYES)

        target = tmp_path / "home" / ".keyhac" / "config.py"
        assert migrate.offer_config_migration(str(target)) is True
        assert target.read_text() == "# 1.x config\n"
        assert len(asked) == 1
        # The prompt has to say the copy needs translating, or a user says yes
        # and gets a config that will not load without knowing why.
        assert "migration-from-keyhac-win.md" in asked[0]

    def test_declining_leaves_the_template(self, tmp_path, monkeypatch, migrate):
        legacy = tmp_path / "appdata" / "Keyhac"
        legacy.mkdir(parents=True)
        (legacy / "config.py").write_text("# 1.x config\n")
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        self._stub_answer(migrate, monkeypatch, 7)  # IDNO

        target = tmp_path / "home" / ".keyhac" / "config.py"
        assert migrate.offer_config_migration(str(target)) is False
        assert not target.exists()

    def test_not_offered_when_the_target_exists(self, tmp_path, monkeypatch, migrate):
        legacy = tmp_path / "appdata" / "Keyhac"
        legacy.mkdir(parents=True)
        (legacy / "config.py").write_text("# 1.x config\n")
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        asked = self._stub_answer(migrate, monkeypatch, migrate.IDYES)

        target = tmp_path / "config.py"
        target.write_text("# keyhac 2 config\n")
        assert migrate.offer_config_migration(str(target)) is False
        assert asked == []                            # not a first run
        assert target.read_text() == "# keyhac 2 config\n"

    def test_not_offered_without_a_legacy_config(self, tmp_path, monkeypatch, migrate):
        (tmp_path / "appdata" / "Keyhac").mkdir(parents=True)  # dir, no config.py
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        asked = self._stub_answer(migrate, monkeypatch, migrate.IDYES)
        assert migrate.offer_config_migration(str(tmp_path / "config.py")) is False
        assert asked == []

    def test_an_unwritable_target_is_survivable(self, tmp_path, monkeypatch, migrate):
        legacy = tmp_path / "appdata" / "Keyhac"
        legacy.mkdir(parents=True)
        (legacy / "config.py").write_text("# 1.x config\n")
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
        self._stub_answer(migrate, monkeypatch, migrate.IDYES)
        # Target's parent is a *file*: makedirs raises, and startup must go on.
        blocker = tmp_path / "blocker"
        blocker.write_text("")
        assert migrate.offer_config_migration(str(blocker / "config.py")) is False
