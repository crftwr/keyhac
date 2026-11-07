"""AppControl.move_to_trash - live against the real Trash / Recycle Bin.

What delete_extension(keep_backup=False) stands on (#250): a file goes where
the user can put it back, and where that is impossible it is refused, never
deleted outright.

These put files in the user's Trash, so they run in a VM (conftest).
"""

import os
import sys
import uuid

import pytest

# Changes the user's Trash: a VM's, not your desktop (conftest).
pytestmark = pytest.mark.desktop


@pytest.fixture
def app_control():
    if sys.platform == "darwin":
        from keyhac.platform.mac.apps import MacAppControl
        return MacAppControl()
    if sys.platform == "win32":
        from keyhac.platform.win.apps import WinAppControl
        return WinAppControl()
    pytest.skip("no Trash on this platform")


def _trash_folder():
    # The account's real home: the suite gives tests a HOME of their own, and
    # the Trash the Finder uses is not under it.
    import pwd
    return os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".Trash")


def test_a_file_goes_to_the_trash_and_can_be_found_there(app_control, tmp_path):
    """The file leaves its folder and lands in the Trash, not nowhere."""
    name = f"keyhac-trash-test-{uuid.uuid4().hex[:8]}.py"
    path = tmp_path / name
    path.write_text("x = 1\n")

    app_control.move_to_trash(str(path))

    assert not path.exists()
    if sys.platform == "darwin":
        trashed = [entry for entry in os.listdir(_trash_folder())
                   if entry.startswith(name[:-3])]
        assert trashed, "not in ~/.Trash"
        for entry in trashed:
            os.remove(os.path.join(_trash_folder(), entry))
    else:
        # The Recycle Bin as the shell lists it (namespace 10): gone from the
        # folder is not enough, since deleting outright would pass that too.
        import subprocess
        listed = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(New-Object -ComObject Shell.Application).NameSpace(10).Items()"
             " | ForEach-Object { $_.Name }"],
            capture_output=True, text=True, timeout=60).stdout
        assert any(line.strip() in (name, name[:-3]) for line in listed.splitlines()), \
            f"not in the Recycle Bin: {listed!r}"


def test_a_missing_file_is_refused_with_a_reason(app_control, tmp_path):
    with pytest.raises(OSError) as raised:
        app_control.move_to_trash(str(tmp_path / "not-there.py"))
    assert str(raised.value)


@pytest.mark.skipif(sys.platform != "win32", reason="drive types are Windows'")
def test_a_drive_with_no_recycle_bin_is_refused_not_deleted(app_control, monkeypatch,
                                                            tmp_path):
    """On a network or removable drive SHFileOperation would delete outright;
    the fixed-drive check stops it first."""
    from keyhac.platform.win import apps
    monkeypatch.setattr(apps.kernel32, "GetDriveTypeW", lambda root: 4)  # remote
    path = tmp_path / "keep-me.py"
    path.write_text("x = 1\n")
    with pytest.raises(OSError, match="Recycle Bin"):
        app_control.move_to_trash(str(path))
    assert path.exists()
