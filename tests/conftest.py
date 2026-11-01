import os
import shutil
import sys
import tempfile

import pytest


# --- an empty home for the whole run ----------------------------------------
#
# Keyhac's defaults read ~/.keyhac: config.py, settings.json, clipboard.json,
# the extensions and packages folders. A test that forgets to pass its own
# path would read the runner's, and pass or fail with what that machine holds
# (XeFM's make tag stopped twice for this). Set in pytest_configure rather
# than a fixture so it is in place at collection time too; realpath because
# macOS hands out /var/..., a symlink to /private/var/.... A test that needs a
# home of its own still sets HOME itself.

_saved_home_env = {}
_fake_home = None


def pytest_configure(config):
    global _fake_home
    _fake_home = os.path.realpath(tempfile.mkdtemp(prefix="keyhac-test-home-"))
    for name in ("HOME", "USERPROFILE"):
        _saved_home_env[name] = os.environ.get(name)
        os.environ[name] = _fake_home


@pytest.fixture
def real_home_environ():
    """The environment with the runner's own HOME and USERPROFILE, for
    starting another application.

    The empty home is this suite's, not the desktop's: Windows' Notepad,
    started with USERPROFILE pointing at it, crashes within seconds (#293).
    """
    environ = dict(os.environ)
    for name, value in _saved_home_env.items():
        if value is None:
            environ.pop(name, None)
        else:
            environ[name] = value
    return environ


def pytest_unconfigure(config):
    for name, value in _saved_home_env.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    if _fake_home is not None:
        shutil.rmtree(_fake_home, ignore_errors=True)


# --- tests that touch the live desktop --------------------------------------
#
# Some platform tests take the foreground, move and resize windows, send real
# keys and mouse events, switch the input source or replace the clipboard.
# On the developer's own machine that gets in the way of whatever they are
# doing, and what they are doing gets in the way of the test. They are marked
# `desktop` and run only with --desktop, which is for a VM (#293).

def pytest_addoption(parser):
    parser.addoption("--desktop", action="store_true",
                     help="also run the tests marked desktop, which take focus, move "
                          "windows and send real input: for a VM, not your machine")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--desktop"):
        return
    skip = pytest.mark.skip(reason="touches the live desktop: run it in a VM "
                                   "(#293), or pass --desktop")
    for item in items:
        if "desktop" in item.keywords:
            item.add_marker(skip)

from keyhac.core.vk import init_key_names, get_key_names
from keyhac.core.keymap import Keymap
from keyhac.platform.fake import FakeInputHook, FakeFocusProvider


class EngineFixture:
    """A Keymap wired to fake platform objects, config loaded from a callable."""

    def __init__(self, platform: str, configure, tmp_path, layout: str = "ansi"):
        self.hook = FakeInputHook(layout)
        self.focus_provider = FakeFocusProvider()

        # Config file whose configure() delegates to the test's function
        config_file = tmp_path / "config.py"
        config_file.write_text("def configure(keymap):\n    _test_configure(keymap)\n")

        self.keymap = Keymap(
            self.hook, self.focus_provider, platform,
            config_path=str(config_file), template_path=str(config_file))
        self.keymap.configure()
        # Inject the test configure function and re-run it
        self.keymap.config.namespace["_test_configure"] = configure
        self.keymap.config.call("configure", self.keymap)

        self.hook.install(self.keymap.on_key_event, self.keymap.on_hook_restored,
                          self.keymap.on_mouse_event)

    def vk(self, name: str) -> int:
        return get_key_names().str_to_vk(name)

    # -- convenience: drive keys by name ---------------------------------

    def down(self, name: str) -> bool:
        return self.hook.key(self.vk(name), True)

    def up(self, name: str) -> bool:
        return self.hook.key(self.vk(name), False)

    def stroke(self, name: str) -> tuple[bool, bool]:
        return self.down(name), self.up(name)

    def sent_names(self) -> list[str]:
        names = get_key_names()
        return [("D-" if down else "U-") + names.vk_to_str(vk)
                for vk, down, _replay in self.hook.sent]


@pytest.fixture(autouse=True)
def restore_sys_path():
    """configure() puts <config dir>/extensions on sys.path, and every test
    gets a fresh tmp_path - so without this the suite finishes carrying one
    dead entry per test, and every import in between pays to scan them all.
    """
    before = list(sys.path)
    yield
    sys.path[:] = before


@pytest.fixture
def engine(tmp_path):
    """engine(configure, platform="mac") -> EngineFixture"""
    def make(configure, platform: str = "mac", layout: str = "ansi"):
        return EngineFixture(platform, configure, tmp_path, layout)
    return make


@pytest.fixture
def mac_names():
    return init_key_names("mac", "ansi")


@pytest.fixture
def win_names():
    return init_key_names("windows", "ansi")
