"""Theme and font size (#11): the choices, what they resolve to, and the tray
items that make them. Against PuiKit's MemoryBackend, which has no OS to read."""

import logging

import pytest
from puikit import THEME_GUI, Panel
from puikit.backends.memory_backend import MemoryBackend

from keyhac.core import log
from keyhac.core.settings import Settings
from keyhac.ui import appearance, console as console_module, tray
from keyhac.ui.appearance import Appearance
from keyhac.ui.console import ConsoleWindow


class _Backend(MemoryBackend):
    """A MemoryBackend with an OS appearance and a base font to observe."""

    def __init__(self, **kwargs):
        super().__init__(width=100, height=30)
        self.system = "dark"
        self.pinned = "unset"
        self.base_fonts = []

    def set_appearance(self, value):
        self.pinned = value

    def effective_appearance(self):
        return self.pinned if self.pinned in ("light", "dark") else self.system

    def set_base_font(self, font):
        self.base_fonts.append(font)


class _Hook:
    installed = True

    def check_health(self):
        pass


class _Keymap:
    _mcp_server_running = False


@pytest.fixture
def settings(tmp_path):
    return Settings(str(tmp_path / "settings.json"))


@pytest.fixture
def console(settings, monkeypatch):
    log.Console._instance = None
    monkeypatch.setattr(console_module, "create_backend",
                        lambda _kind, **kwargs: _Backend())
    window = ConsoleWindow(_Keymap(), _Hook(), settings=settings)
    window.open()
    return window


def test_defaults_are_auto_and_the_size_keyhac_always_had(settings):
    chosen = Appearance(settings)
    assert chosen.theme_choice == "auto"
    assert chosen.font_size_choice == "medium" and chosen.font_points == 12


def test_there_are_five_sizes_in_order():
    points = [row[2] for row in appearance.FONT_SIZES]
    assert len(points) == 5 and points == sorted(set(points))


def test_a_choice_survives_a_restart(settings, tmp_path):
    chosen = Appearance(settings)
    chosen.theme_choice, chosen.font_size_choice = "light", "large"
    again = Appearance(Settings(str(tmp_path / "settings.json")))
    assert again.theme_choice == "light" and again.font_points == 14


def test_a_settings_file_holding_nonsense_is_the_default(settings):
    settings.set("theme", "purple")
    settings.set("font_size", 99)
    chosen = Appearance(settings)
    assert chosen.theme_choice == "auto" and chosen.font_points == 12


@pytest.mark.parametrize("choice, system, light", [
    ("auto", "light", True), ("auto", "dark", False),
    ("auto", None, False),  # an OS that will not say keeps the dark look
    ("light", "dark", True), ("dark", "light", False),
])
def test_what_a_choice_resolves_to(choice, system, light):
    backend = _Backend()
    backend.system = system
    chosen = Appearance()
    chosen.theme_choice = choice
    chosen.apply_to_backend(backend)
    assert chosen.is_light is light
    assert backend.pinned == (None if choice == "auto" else choice)
    panel = Panel(backend)
    chosen.style_panel(panel)
    assert panel.auto_ink is light
    window_color = panel.theme.surface_bg("window")
    assert (sum(window_color) > 3 * 128) is light


def test_dark_is_exactly_what_keyhac_looked_like_before_it_had_themes():
    backend = _Backend()
    panel = Panel(backend)
    default = THEME_GUI
    chosen = Appearance()
    chosen.theme_choice = "dark"
    chosen.apply_to_backend(backend)
    chosen.style_panel(panel)
    assert panel.auto_ink is False
    assert panel.theme.surfaces == {**default.surfaces, "window": (24, 24, 24)}
    assert panel.theme.accent == default.accent and panel.theme.text == default.text


def test_a_window_opened_later_wears_the_consoles_theme(monkeypatch):
    panel = Panel(_Backend())
    before = panel.theme
    monkeypatch.setattr(appearance.runtime, "appearance", None)
    appearance.style_new_panel(panel)
    assert panel.theme is before  # nothing registered: PuiKit's default
    chosen = Appearance()
    chosen.theme_choice = "light"
    monkeypatch.setattr(appearance.runtime, "appearance", chosen)
    appearance.style_new_panel(panel)
    assert panel.auto_ink is True


def test_auto_follows_the_os_and_a_pinned_choice_does_not(console):
    backend = console.backend
    assert console.appearance.system_changed(backend) is False
    backend.system = "light"
    assert console.appearance.system_changed(backend) is True
    console._last_health_tick = console._last_appearance_poll = -10.0
    console._on_pump_tick()
    assert console.panel.auto_ink is True
    assert console.appearance.system_changed(backend) is False

    console.set_theme("dark")
    backend.system = "dark"
    assert console.appearance.system_changed(backend) is False
    assert console.panel.auto_ink is False


def test_the_font_size_reaches_the_base_font_and_the_lines_already_shown(console):
    log.Console.get_instance().write("old line", logging.INFO)
    console._on_pump_tick()

    console.set_font_size("extra_large")

    assert console.backend.base_fonts[-1].size == 16
    assert console._log_view.lines, "the log survives"
    assert {style.font.size for _text, style in console._log_view.lines} == {15}
    assert console.appearance.font_size_choice == "extra_large"
    # Nothing shown twice: the rewrite took the pending lines with it.
    count = len(console._log_view.lines)
    console._on_pump_tick()
    assert len(console._log_view.lines) == count


def test_the_tray_menu_chooses_and_ticks(console, monkeypatch):
    class _Reports:
        running = tracking = False
        write = toggle_tracking = start_history = lambda self: None

    class _Profiling:
        can_start, can_stop = True, False
        start = stop = lambda self: None

    class _Sync:
        running = False
        start = lambda self: None

    monkeypatch.setattr(tray, "MemoryReports", lambda keymap: _Reports())
    monkeypatch.setattr(tray, "Profiling", lambda keymap: _Profiling())
    monkeypatch.setattr(tray, "PackageSync", lambda keymap: _Sync())
    keymap = console._keymap
    keymap.edit_config = keymap.configure = lambda: None
    backend = console.backend
    backend.show_main_window = backend.quit = lambda: None

    tray.install_tray(console, keymap, console._hook)
    _title, menu, _tooltip, _image = backend.tray_calls[-1]
    top = [getattr(item, "label", "-") for item in menu.items]
    assert top == ["Open Console", "Edit Config", "Reload Config", "-",
                   "Keyboard Hook", "AI Integration", "-",
                   "Appearance", "Advanced", "-", "Quit Keyhac"]
    entries = {getattr(item, "label", "-"): item for item in menu.items}

    rows = entries["Appearance"].submenu.items
    labels = [getattr(i, "label", "-") for i in rows]
    assert labels == ["Theme", "Auto", "Light", "Dark", "-", "Font Size", "Extra Small",
                      "Small", "Medium", "Large", "Extra Large"]
    by_label = {i.label: i for i in rows if hasattr(i, "label")}
    assert not by_label["Theme"].is_enabled() and not by_label["Font Size"].is_enabled()

    themes = {name: by_label[name] for name in ("Auto", "Light", "Dark")}
    assert themes["Auto"].is_checked() and not themes["Light"].is_checked()
    themes["Light"].activate()
    assert themes["Light"].is_checked() and not themes["Auto"].is_checked()
    assert console.panel.auto_ink is True

    sizes = by_label
    assert sizes["Medium"].is_checked()
    sizes["Large"].activate()
    assert sizes["Large"].is_checked() and backend.base_fonts[-1].size == 14
