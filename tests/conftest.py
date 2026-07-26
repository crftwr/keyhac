import pytest

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

        self.hook.install(self.keymap.on_key_event, self.keymap.on_hook_restored)

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
