"""Windows clipboard provider - live against the real clipboard.

These mutate the system clipboard; the module fixture snapshots any text
present and restores it afterwards (non-text content cannot be preserved).
"""

import ctypes
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

from keyhac.platform.win.clipboard import WinClipboardProvider  # noqa: E402


@pytest.fixture(scope="module")
def provider():
    provider = WinClipboardProvider()
    saved = provider.get_text()
    yield provider
    if saved is not None:
        provider.set_text(saved)


class TestWinClipboardProvider:

    @pytest.mark.parametrize("text", [
        "plain ascii",
        "日本語のテキスト（全角）",
        "emoji 🎹🗒️ and surrogates 𠮷野家",
        "line one\r\nline two",
    ])
    def test_set_get_round_trip(self, provider, text):
        provider.set_text(text)
        assert provider.get_text() == text

    def test_poll_reports_a_change_once(self, provider):
        provider.poll()  # settle: swallow any pending change
        provider.set_text("poll-probe")
        assert provider.poll() is True
        assert provider.poll() is False

    def test_poll_ignores_no_change(self, provider):
        provider.poll()
        assert provider.poll() is False

    def test_empty_clipboard_reads_none(self, provider):
        import ctypes
        user32 = ctypes.WinDLL("user32")
        assert user32.OpenClipboard(None)
        user32.EmptyClipboard()
        user32.CloseClipboard()
        assert provider.get_text() is None

    @pytest.mark.parametrize("name, dword, concealed", [
        ("ExcludeClipboardContentFromMonitorProcessing", None, True),
        ("CanIncludeInClipboardHistory", 0, True),
        ("CanIncludeInClipboardHistory", 1, False),
    ])
    def test_monitor_markers(self, provider, name, dword, concealed):
        from keyhac.platform.win.clipboard import kernel32, user32
        provider.set_text("secret")
        assert provider.is_concealed() is False
        # Add the marker beside the text, the way a password manager does.
        assert user32.OpenClipboard(None)
        try:
            data = (dword or 0).to_bytes(4, "little")
            handle = kernel32.GlobalAlloc(0x0002, len(data))
            ptr = kernel32.GlobalLock(handle)
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(handle)
            user32.SetClipboardData(user32.RegisterClipboardFormatW(name), handle)
        finally:
            user32.CloseClipboard()
        assert provider.is_concealed() is concealed
