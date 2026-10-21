"""Where the Accessibility-permission messages send the user
(keyhac/platform/mac/__init__.py).

macOS 13 renamed System Preferences to System Settings and turned the pane's
name round, so a message naming one is wrong on the other. Pure string logic
over platform.mac_ver(), so it runs on any OS.
"""

import platform

import pytest

from keyhac.platform.mac import accessibility_settings_path


@pytest.mark.parametrize("version, expected", [
    ("12.7.6", "System Preferences > Security & Privacy > Privacy > Accessibility"),
    ("11.0", "System Preferences > Security & Privacy > Privacy > Accessibility"),
    ("13.0", "System Settings > Privacy & Security > Accessibility"),
    ("27.0.1", "System Settings > Privacy & Security > Accessibility"),
    # Not on a Mac, or a version string it cannot read: the current spelling.
    ("", "System Settings > Privacy & Security > Accessibility"),
])
def test_accessibility_settings_path(monkeypatch, version, expected):
    monkeypatch.setattr(platform, "mac_ver", lambda: (version, ("", "", ""), ""))
    assert accessibility_settings_path() == expected
