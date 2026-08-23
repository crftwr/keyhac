"""macOS IME provider - live against the real Text Input Sources.

These change the process-wide input source, so the module fixture snapshots
the selected one and puts it back afterwards.  Skipped when the machine has no
input method installed (a plain-keyboard-layout Mac has no IME to turn on),
which is what CI sees.
"""

import sys

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS-only platform layer", allow_module_level=True)

from keyhac.platform.mac.ime import MacImeProvider, _tis_api  # noqa: E402


def _current_source_id(api):
    source = api.TISCopyCurrentKeyboardInputSource()
    if not source:
        return None
    try:
        return api.property_string(source, api.key_source_id)
    finally:
        api.CFRelease(source)


def _select_source_id(api, source_id) -> bool:
    sources = api.TISCreateInputSourceList(None, False)
    try:
        for i in range(api.CFArrayGetCount(sources)):
            source = api.CFArrayGetValueAtIndex(sources, i)
            if api.property_string(source, api.key_source_id) == source_id:
                return api.TISSelectInputSource(source) == 0
        return False
    finally:
        api.CFRelease(sources)


def _has_input_method(api) -> bool:
    sources = api.TISCreateInputSourceList(None, False)
    try:
        return any(
            api.property_string(api.CFArrayGetValueAtIndex(sources, i),
                                api.key_mode_id) is not None
            for i in range(api.CFArrayGetCount(sources)))
    finally:
        api.CFRelease(sources)


@pytest.fixture(scope="module")
def provider():
    api = _tis_api()
    if api is None:
        pytest.skip("Text Input Sources unavailable")
    if not _has_input_method(api):
        pytest.skip("no input method installed to switch on")
    saved = _current_source_id(api)
    yield MacImeProvider()
    if saved:
        _select_source_id(api, saved)


def test_status_is_a_bool_not_none(provider):
    """A Mac with an input method can always answer; None is for the case
    where TIS itself is unreachable."""
    assert provider.get_status() in (True, False)


def test_turning_on_and_off_round_trips(provider):
    assert provider.set_status(True) is True
    assert provider.get_status() is True
    assert provider.set_status(False) is True
    assert provider.get_status() is False
    assert provider.set_status(True) is True
    assert provider.get_status() is True


def test_setting_the_state_it_is_already_in_succeeds(provider):
    provider.set_status(False)
    assert provider.set_status(False) is True
    assert provider.get_status() is False


def test_off_never_lands_on_an_input_method(provider):
    """Off must reach a plain layout or a Roman mode - the fallback exists
    because the Roman mode is disabled on a default Japanese setup, and
    TISSelectInputSource refuses a disabled source (OSStatus -50)."""
    api = _tis_api()
    provider.set_status(True)
    assert provider.set_status(False) is True
    source = api.TISCopyCurrentKeyboardInputSource()
    try:
        mode = api.property_string(source, api.key_mode_id)
    finally:
        api.CFRelease(source)
    assert mode is None or mode == "com.apple.inputmethod.Roman"


def test_turning_on_while_already_on_keeps_the_selected_mode(provider):
    """"On" must not mean "the first enabled mode": re-asserting it while the
    user sits in, say, Katakana would drag them back to Hiragana."""
    api = _tis_api()
    provider.set_status(True)
    before = _current_source_id(api)
    assert provider.set_status(True) is True
    assert _current_source_id(api) == before
