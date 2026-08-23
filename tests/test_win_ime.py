"""Windows IME provider - live against the real IMM32 state.

Needs an IME installed for the current input language (Microsoft IME); the
whole module skips otherwise, since a machine with no IME can only ever answer
None.  The state read and written is the foreground window's - i.e. whatever
console pytest is running in - and the fixture puts it back afterwards.

STATUS: written to spec; run this on the Windows pass (doc/dev/testing.md).
"""

import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from keyhac.platform.win.ime import WinImeProvider  # noqa: E402


@pytest.fixture(scope="module")
def provider():
    provider = WinImeProvider()
    saved = provider.get_status()
    if saved is None:
        pytest.skip("no IME answers IMM32 for the foreground window")
    yield provider
    provider.set_status(saved)


def test_status_is_a_bool_when_an_ime_answers(provider):
    assert provider.get_status() in (True, False)


def test_turning_on_and_off_round_trips(provider):
    assert provider.set_status(True) is True
    assert provider.get_status() is True
    assert provider.set_status(False) is True
    assert provider.get_status() is False


def test_setting_the_state_it_is_already_in_succeeds(provider):
    provider.set_status(False)
    assert provider.set_status(False) is True
    assert provider.get_status() is False


def test_the_send_is_capped_well_under_the_hook_timeout():
    """The cap is load-bearing: this runs inside the WH_KEYBOARD_LL callback,
    and exceeding LowLevelHooksTimeout (300 ms) gets the hook silently
    unhooked."""
    from keyhac.platform.win import ime
    assert ime.SEND_TIMEOUT_MS <= 150


def test_a_query_returns_promptly(provider):
    """A responsive IME answers far inside the cap; this is the regression
    guard for accidentally reintroducing a blocking SendMessage."""
    import time
    start = time.perf_counter()
    for _ in range(10):
        provider.get_status()
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 10 * 50
