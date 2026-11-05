"""macOS clipboard provider - against a private pasteboard of its own, so the
system clipboard is never touched."""

import sys

import pytest

if sys.platform != "darwin":
    pytest.skip("macOS-only platform layer", allow_module_level=True)

from AppKit import NSPasteboard, NSPasteboardItem  # noqa: E402
from Foundation import NSData  # noqa: E402

from keyhac.platform.mac.clipboard import MacClipboardProvider  # noqa: E402

#: A 1×1 PNG.
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201e5b0b3"
    "6f0000000049454e44ae426082")


@pytest.fixture
def provider():
    pasteboard = NSPasteboard.pasteboardWithUniqueName()
    provider = MacClipboardProvider()
    provider._pasteboard = pasteboard
    yield provider
    pasteboard.releaseGlobally()


def put(provider, formats: dict) -> None:
    item = NSPasteboardItem.alloc().init()
    for t, data in formats.items():
        item.setData_forType_(NSData.dataWithBytes_length_(data, len(data)), t)
    provider._pasteboard.clearContents()
    provider._pasteboard.writeObjects_([item])


class TestKind:

    def test_an_image_without_text_is_an_image(self, provider):
        put(provider, {"public.png": PNG})
        snapshot = provider.get_snapshot()
        assert snapshot.kind == "image" and snapshot.image_size == (1, 1)

    def test_text_with_a_picture_of_itself_is_text(self, provider):
        # What Excel offers for a range of cells, among much else.
        put(provider, {"public.utf8-plain-text": "a\tb\r\n".encode(),
                       "public.html": b"<table><tr><td>a<td>b</table>",
                       "public.png": PNG})
        snapshot = provider.get_snapshot()
        assert snapshot.kind == "rich" and snapshot.text == "a\tb\r\n"
        assert provider.image_data(snapshot) == PNG

    def test_text_with_only_an_image_still_keeps_the_image(self, provider):
        put(provider, {"public.utf8-plain-text": b"caption", "public.png": PNG})
        snapshot = provider.get_snapshot()
        # "rich": choosing it puts more back than the text.
        assert snapshot.kind == "rich"
        assert "public.png" in snapshot.items[0]
