"""Theme and font size: chosen from the tray menu, remembered in settings.json.

Not configurable from config.py. These are the user's preference for this
machine rather than part of a keymap, and a config that sets them would fight
the menu every time it reloads.

**Dark is what Keyhac has always looked like** and stays exactly that: PuiKit's
own GUI theme with the backends' clear color named as a surface. Light turns on
`Panel.auto_ink`, which is what lets the colors written for a dark background -
the log levels, the gray captions - stay as they are in the code and still read
on a light one.
"""

from dataclasses import replace

from puikit import THEME_GUI, Font, derive_theme

from keyhac.ui import runtime

#: The surface a window's root pane is tagged with (`hints=WINDOW_SURFACE`).
#: Without one a window shows the backend's clear color, which is always dark.
WINDOW_SURFACE = {"surface": "window"}

_DARK = replace(THEME_GUI, surfaces={**THEME_GUI.surfaces, "window": (24, 24, 24)})
_LIGHT = derive_theme(
    background=(255, 255, 255), foreground=(30, 30, 30), muted=(110, 110, 110),
    accent=(0, 122, 204), surface=(235, 235, 238), selection=(120, 180, 240),
    surfaces={"window": (240, 240, 242)})

#: The Theme menu: (setting, label).
THEMES = (("auto", "Auto"), ("light", "Light"), ("dark", "Dark"))
_DEFAULT_THEME = "auto"

#: The Font Size menu: (setting, label, points).
FONT_SIZES = (
    ("extra_small", "Extra Small", 10),
    ("small", "Small", 11),
    ("medium", "Medium", 12),
    ("large", "Large", 14),
    ("extra_large", "Extra Large", 16),
)
_DEFAULT_FONT_SIZE = "medium"


class Appearance:
    """The two choices, and what they currently resolve to.

    Without a settings store (tests) a choice lasts as long as the object.
    """

    def __init__(self, settings=None):
        self._settings = settings
        self._choices = {}
        self._system = None

    def _choice(self, key: str, known, default: str) -> str:
        """A settings file can hold anything, so an unknown value is the default."""
        value = self._settings.get(key) if self._settings else self._choices.get(key)
        return value if value in known else default

    def _choose(self, key: str, value: str) -> None:
        self._choices[key] = value
        if self._settings:
            self._settings.set(key, value)

    @property
    def theme_choice(self) -> str:
        return self._choice("theme", [name for name, _ in THEMES], _DEFAULT_THEME)

    @theme_choice.setter
    def theme_choice(self, value: str) -> None:
        self._choose("theme", value)

    @property
    def font_size_choice(self) -> str:
        return self._choice("font_size", [row[0] for row in FONT_SIZES],
                            _DEFAULT_FONT_SIZE)

    @font_size_choice.setter
    def font_size_choice(self, value: str) -> None:
        self._choose("font_size", value)

    @property
    def font_points(self) -> int:
        return next(points for name, _, points in FONT_SIZES
                    if name == self.font_size_choice)

    def base_font(self) -> Font:
        return Font(size=self.font_points, monospace=True)

    @property
    def is_light(self) -> bool:
        """Dark where the OS will not say (`effective_appearance` -> None)."""
        choice = self.theme_choice
        return (self._system == "light") if choice == "auto" else choice == "light"

    def apply_to_backend(self, backend) -> None:
        """Hand the OS-drawn parts the choice, and read what Auto resolves to."""
        choice = self.theme_choice
        backend.set_appearance(None if choice == "auto" else choice)
        self._system = backend.effective_appearance()

    def system_changed(self, backend) -> bool:
        """Under Auto, whether the OS setting moved since it was last read."""
        return (self.theme_choice == "auto"
                and backend.effective_appearance() != self._system)

    def style_panel(self, panel) -> None:
        panel.theme = _LIGHT if self.is_light else _DARK
        panel.auto_ink = self.is_light


def style_new_panel(panel) -> None:
    """A window opened later wears what the console wears. No registered
    Appearance (tests, headless) leaves PuiKit's default on it."""
    if runtime.appearance is not None:
        runtime.appearance.style_panel(panel)
