"""Windows clipboard provider - live against the real clipboard.

These mutate the system clipboard; the module fixture snapshots any text
present and restores it afterwards (non-text content cannot be preserved).
"""

import ctypes
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("Windows-only platform layer", allow_module_level=True)

# Takes focus, opens windows or sends real input: a VM's, not your desktop (conftest).
pytestmark = pytest.mark.desktop

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


class TestWinSnapshots:
    """Snapshots against the real clipboard: what is read back is what was
    put there, in the kept formats, and a cut comes back as a copy."""

    def _put(self, provider, formats):
        from keyhac.platform.win.clipboard import GMEM_MOVEABLE, kernel32, user32
        assert user32.OpenClipboard(None)
        try:
            user32.EmptyClipboard()
            for name, data in formats.items():
                handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                ptr = kernel32.GlobalLock(handle)
                ctypes.memmove(ptr, data, len(data))
                kernel32.GlobalUnlock(handle)
                user32.SetClipboardData(provider._format_number(name), handle)
        finally:
            user32.CloseClipboard()

    def test_rich_text_round_trip(self, provider):
        html = b"Version:0.9\r\nStartHTML:0\r\n<b>bold</b>"
        self._put(provider, {"CF_UNICODETEXT": "bold\0".encode("utf-16-le"),
                             "HTML Format": html,
                             "Some Private Format": b"x"})
        snapshot = provider.get_snapshot()
        assert snapshot.kind == "rich" and snapshot.text == "bold"
        assert snapshot.lossy            # the private format was not kept
        provider.set_text("other")
        provider.set_snapshot(snapshot)
        again = provider.get_snapshot()
        assert again.items[0]["HTML Format"] == html
        assert again.text == "bold"

    def test_image_size_and_round_trip(self, provider):
        import struct
        header = struct.pack("<IiiHHIIiiII", 40, 4, 2, 1, 32, 0, 32, 0, 0, 0, 0)
        dib = header + b"\xff" * 32
        self._put(provider, {"CF_DIB": dib})
        snapshot = provider.get_snapshot()
        assert snapshot.kind == "image" and snapshot.image_size == (4, 2)
        provider.set_text("other")
        provider.set_snapshot(snapshot)
        assert provider.get_snapshot().items[0]["CF_DIB"][:40] == header

    def test_a_cut_is_restored_as_a_copy(self, provider, tmp_path):
        import struct
        from keyhac.platform.win import clipboard as winclip
        path = str(tmp_path / "f.txt")
        open(path, "w").close()
        self._put(provider, {"CF_HDROP": winclip.build_hdrop([path]),
                             winclip.DROP_EFFECT: struct.pack("<I", winclip.DROPEFFECT_MOVE)})
        snapshot = provider.get_snapshot()
        assert snapshot.kind == "files" and snapshot.files == (path,)
        provider.set_snapshot(snapshot)
        effect = provider.get_snapshot().items[0][winclip.DROP_EFFECT]
        assert struct.unpack("<I", effect[:4])[0] == winclip.DROPEFFECT_COPY

    def test_files_from_disk_are_restored_from_their_paths(self, provider, tmp_path):
        from keyhac.platform.base import ClipboardSnapshot
        path = str(tmp_path / "g.txt")
        open(path, "w").close()
        provider.set_snapshot(ClipboardSnapshot(kind="files", files=(path,)))
        assert provider.get_snapshot().files == (path,)
