"""keyhac.core.permissions - the data directory stays owner-only.

Mode bits are POSIX; the whole module is a no-op on Windows, so is this file.
Every test forces umask 022 - the stock desktop value, and the one that makes
a plain open() produce 0644 - so what is asserted is the module's doing and
not the runner's environment.
"""

import os
import stat

import pytest

from keyhac.core import paths, permissions

pytestmark = pytest.mark.skipif(os.name == "nt",
                                reason="POSIX mode bits; Windows uses ACLs")


@pytest.fixture(autouse=True)
def default_umask():
    previous = os.umask(0o022)
    yield
    os.umask(previous)


def mode(path) -> int:
    return stat.S_IMODE(os.lstat(str(path)).st_mode)


def make_data_dir(tmp_path, monkeypatch, *, is_default=True):
    """A ~/.keyhac as an upgrading user has it: 0755 with 0644 inside."""
    data_dir = tmp_path / ".keyhac"
    data_dir.mkdir(mode=0o755)
    for name in ("config.py", "clipboard.json", "settings.json"):
        f = data_dir / name
        f.write_text("x")
        f.chmod(0o644)
    if is_default:
        monkeypatch.setattr(paths, "default_data_dir", lambda: str(data_dir))
    else:
        monkeypatch.setattr(paths, "default_data_dir",
                            lambda: str(tmp_path / "elsewhere"))
    return data_dir


class TestCreation:

    def test_open_private_creates_owner_only(self, tmp_path):
        path = tmp_path / "clipboard.json"
        with permissions.open_private(str(path)) as f:
            f.write("{}")
        assert mode(path) == 0o600
        # The comparison that matters: the same write, done plainly.
        plain = tmp_path / "plain.json"
        with open(plain, "w") as f:
            f.write("{}")
        assert mode(plain) == 0o644

    def test_open_private_keeps_an_existing_mode(self, tmp_path):
        path = tmp_path / "f"
        path.write_text("old")
        path.chmod(0o640)
        with permissions.open_private(str(path)) as f:
            f.write("new")
        # Creation mode only - O_CREAT does not re-permission what is there.
        # The start-up sweep is what fixes an already-loose file.
        assert mode(path) == 0o640
        assert path.read_text() == "new"

    def test_open_private_append_does_not_truncate(self, tmp_path):
        path = tmp_path / "instance.lock"
        with permissions.open_private(str(path), "a") as f:
            f.write("1")
        with permissions.open_private(str(path), "a") as f:
            f.write("2")
        assert path.read_text() == "12"
        assert mode(path) == 0o600

    def test_copy_private_destination_is_owner_only(self, tmp_path):
        src = tmp_path / "template.py"
        src.write_text("def configure(keymap):\n    pass\n")
        src.chmod(0o644)
        dst = tmp_path / "config.py"
        permissions.copy_private(str(src), str(dst))
        assert dst.read_text() == src.read_text()
        assert mode(dst) == 0o600
        assert mode(src) == 0o644  # the source is left alone

    def test_ensure_private_dir(self, tmp_path):
        path = tmp_path / "a" / "b"
        permissions.ensure_private_dir(str(path))
        assert mode(path) == 0o700

    def test_ensure_private_dir_leaves_an_existing_one(self, tmp_path):
        path = tmp_path / "a"
        path.mkdir(mode=0o755)
        permissions.ensure_private_dir(str(path))
        assert mode(path) == 0o755  # the sweep's job, not this one's

    def test_ensure_private_dir_accepts_an_empty_path(self):
        permissions.ensure_private_dir("")  # dirname of a bare filename


class TestSweep:

    def test_tightens_the_directory_and_its_files(self, tmp_path, monkeypatch):
        data_dir = make_data_dir(tmp_path, monkeypatch)
        permissions.harden_data_dir(str(data_dir))
        assert mode(data_dir) == 0o700
        for name in ("config.py", "clipboard.json", "settings.json"):
            assert mode(data_dir / name) == 0o600, name

    def test_tightens_write_tool_backups(self, tmp_path, monkeypatch):
        data_dir = make_data_dir(tmp_path, monkeypatch)
        backup = data_dir / "config.py.bak-20260904-213500"
        backup.write_text("x")
        backup.chmod(0o644)
        permissions.harden_data_dir(str(data_dir))
        assert mode(backup) == 0o600

    def test_tightens_the_extensions_tree(self, tmp_path, monkeypatch):
        data_dir = make_data_dir(tmp_path, monkeypatch)
        package = data_dir / "extensions" / "pkg"
        package.mkdir(parents=True)
        module = package / "action.py"
        module.write_text("x")
        (data_dir / "extensions").chmod(0o755)
        package.chmod(0o755)
        module.chmod(0o644)
        permissions.harden_data_dir(str(data_dir))
        assert mode(data_dir / "extensions") == 0o700
        assert mode(package) == 0o700
        assert mode(module) == 0o600

    def test_is_idempotent_and_never_loosens(self, tmp_path, monkeypatch):
        data_dir = make_data_dir(tmp_path, monkeypatch)
        (data_dir / "clipboard.json").chmod(0o400)
        permissions.harden_data_dir(str(data_dir))
        permissions.harden_data_dir(str(data_dir))
        assert mode(data_dir / "clipboard.json") == 0o400

    def test_owner_execute_bit_survives(self, tmp_path, monkeypatch):
        data_dir = make_data_dir(tmp_path, monkeypatch)
        (data_dir / "config.py").chmod(0o755)
        permissions.harden_data_dir(str(data_dir))
        assert mode(data_dir / "config.py") == 0o700

    def test_a_symlinked_config_is_left_alone(self, tmp_path, monkeypatch):
        data_dir = make_data_dir(tmp_path, monkeypatch)
        # config.py symlinked into a dotfiles repo: the target is the
        # operator's file at their permissions, and chmod would follow.
        dotfiles = tmp_path / "dotfiles"
        dotfiles.mkdir()
        real = dotfiles / "keyhac-config.py"
        real.write_text("x")
        real.chmod(0o644)
        (data_dir / "config.py").unlink()
        (data_dir / "config.py").symlink_to(real)
        permissions.harden_data_dir(str(data_dir))
        assert mode(real) == 0o644

    def test_a_config_arg_directory_is_not_re_permissioned(self, tmp_path,
                                                           monkeypatch):
        # --config pointed at a source tree: its files are ours to tighten,
        # the directory holding them is not.
        data_dir = make_data_dir(tmp_path, monkeypatch, is_default=False)
        permissions.harden_data_dir(str(data_dir))
        assert mode(data_dir) == 0o755
        assert mode(data_dir / "clipboard.json") == 0o600

    def test_missing_files_and_directory_are_not_an_error(self, tmp_path,
                                                          monkeypatch):
        monkeypatch.setattr(paths, "default_data_dir", lambda: str(tmp_path))
        permissions.harden_data_dir(str(tmp_path / "does-not-exist"))

    def test_windows_does_nothing(self, tmp_path, monkeypatch):
        data_dir = make_data_dir(tmp_path, monkeypatch)
        monkeypatch.setattr(permissions, "_POSIX_MODES", False)
        permissions.harden_data_dir(str(data_dir))
        assert mode(data_dir) == 0o755
        assert mode(data_dir / "clipboard.json") == 0o644


class TestCallers:
    """The state files as their own writers produce them."""

    def test_settings_file_is_owner_only(self, tmp_path):
        from keyhac.core.settings import Settings
        path = tmp_path / "state" / "settings.json"
        Settings(str(path)).set("console_visible", False)
        assert mode(path) == 0o600
        assert mode(path.parent) == 0o700

    def test_clipboard_history_file_is_owner_only(self, tmp_path):
        from keyhac.core.clipboard_history import ClipboardHistory
        history = ClipboardHistory(None, str(tmp_path / "clipboard.json"))
        history.add_item("a password, as it happens")
        history._save()
        assert mode(tmp_path / "clipboard.json") == 0o600

    def test_first_run_config_is_owner_only(self, tmp_path):
        from keyhac.core.config import Config
        template = tmp_path / "template.py"
        template.write_text("def configure(keymap):\n    pass\n")
        config_path = tmp_path / ".keyhac" / "config.py"
        Config(str(config_path), str(template))
        assert mode(config_path) == 0o600
        assert mode(config_path.parent) == 0o700
