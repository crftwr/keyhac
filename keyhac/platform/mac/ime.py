"""macOS IME control - Text Input Sources (Carbon TIS) through ctypes.

macOS has no notion of an IME being "open" the way Windows does; what it has
is a selected *input source*, which for an input method carries an input mode
(kTISPropertyInputModeID).  On/off is read off that mode:

    com.apple.inputmethod.Japanese[.Katakana|.HalfWidthKana|.FullWidthRoman]
        -> on           (and likewise a non-Roman mode of any other IME)
    com.apple.inputmethod.Roman, or a plain keyboard layout with no mode
        -> off

Keying on the *mode* rather than the input source's bundle id is what makes
this IME-agnostic: Kotoeri, Google IME and ATOK all report the same mode ids.

TIS is not wrapped by PyObjC (neither Quartz nor ApplicationServices export
it), so it is reached by ctypes against Carbon.framework - the same route
MacInputHook.keyboard_layout() already takes.  CoreFoundation is called
through ctypes too rather than bridging to PyObjC objects, so that ownership
of the +1 references the Copy/Create functions return stays explicit.

STATUS: get_status() verified live (read com.apple.keylayout.US as off and
com.apple.inputmethod.Kotoeri.RomajiTyping.Japanese as on).  set_status()'s
Roman-mode fallback exists because selecting the Roman mode was measured to
fail with OSStatus -50 (paramErr) when that mode is disabled, which it is by
default when only "Japanese - Romaji" and a plain layout are enabled.
"""

import ctypes

from keyhac.core import log
from keyhac.platform.base import ImeProvider

logger = log.getLogger("MacIme")

#: Input mode of an input method sitting in its ASCII/alphanumeric state.
ROMAN_MODE = "com.apple.inputmethod.Roman"

_CF_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
_CARBON_PATH = "/System/Library/Frameworks/Carbon.framework/Carbon"

_kCFStringEncodingUTF8 = 0x08000100


class _TIS:
    """The Carbon/CoreFoundation entry points, loaded once and prototyped.

    Prototypes are declared in full for the same reason the Windows side does
    it: the default c_int restype truncates a 64-bit pointer.
    """

    def __init__(self):
        self.cf = ctypes.CDLL(_CF_PATH)
        self.carbon = ctypes.CDLL(_CARBON_PATH)

        void_p = ctypes.c_void_p
        for lib, name, restype, argtypes in [
            (self.cf, "CFRelease", None, [void_p]),
            (self.cf, "CFArrayGetCount", ctypes.c_long, [void_p]),
            (self.cf, "CFArrayGetValueAtIndex", void_p, [void_p, ctypes.c_long]),
            (self.cf, "CFStringGetCString", ctypes.c_bool,
             [void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]),
            (self.cf, "CFBooleanGetValue", ctypes.c_bool, [void_p]),
            (self.carbon, "TISCopyCurrentKeyboardInputSource", void_p, []),
            (self.carbon, "TISCopyCurrentASCIICapableKeyboardLayoutInputSource",
             void_p, []),
            (self.carbon, "TISCreateInputSourceList", void_p,
             [void_p, ctypes.c_bool]),
            (self.carbon, "TISGetInputSourceProperty", void_p, [void_p, void_p]),
            (self.carbon, "TISSelectInputSource", ctypes.c_int32, [void_p]),
        ]:
            fn = getattr(lib, name)
            fn.restype = restype
            fn.argtypes = argtypes
            setattr(self, name, fn)

        # CFStringRef globals, resolved once.
        self.key_mode_id = ctypes.c_void_p.in_dll(
            self.carbon, "kTISPropertyInputModeID")
        self.key_source_id = ctypes.c_void_p.in_dll(
            self.carbon, "kTISPropertyInputSourceID")
        self.key_enabled = ctypes.c_void_p.in_dll(
            self.carbon, "kTISPropertyInputSourceIsEnabled")
        self.key_select_capable = ctypes.c_void_p.in_dll(
            self.carbon, "kTISPropertyInputSourceIsSelectCapable")

    def cfstring(self, ref) -> str | None:
        """Copy a CFStringRef out as a Python str."""
        if not ref:
            return None
        buf = ctypes.create_string_buffer(512)
        if not self.CFStringGetCString(ref, buf, len(buf), _kCFStringEncodingUTF8):
            return None
        return buf.value.decode("utf-8", "replace")

    def property_string(self, source, key) -> str | None:
        """A source's string property (the ref is borrowed - do not release)."""
        return self.cfstring(self.TISGetInputSourceProperty(source, key))

    def property_bool(self, source, key) -> bool:
        ref = self.TISGetInputSourceProperty(source, key)
        return bool(ref) and bool(self.CFBooleanGetValue(ref))


_tis = None
_tis_failed = False


def _tis_api() -> "_TIS | None":
    """The loaded TIS entry points, or None if the frameworks are unreachable
    (logged once, then remembered so a broken load is not retried per key)."""
    global _tis, _tis_failed
    if _tis is None and not _tis_failed:
        try:
            _tis = _TIS()
        except Exception as e:
            _tis_failed = True
            logger.error(f"Text Input Sources unavailable: {e}")
    return _tis


def _mode_is_on(mode_id: str | None) -> bool:
    """Whether an input mode counts as "IME on"."""
    # No mode at all means a plain keyboard layout is selected, not an IME.
    return bool(mode_id) and mode_id != ROMAN_MODE


class MacImeProvider(ImeProvider):
    """IME on/off via the current Text Input Source."""

    def get_status(self) -> bool | None:
        api = _tis_api()
        if api is None:
            return None
        source = api.TISCopyCurrentKeyboardInputSource()
        if not source:
            logger.debug("No current keyboard input source.")
            return None
        try:
            return _mode_is_on(api.property_string(source, api.key_mode_id))
        finally:
            api.CFRelease(source)

    def set_status(self, on: bool) -> bool:
        api = _tis_api()
        if api is None:
            return False
        # Already there: nothing to select.  Not just an optimization - the
        # selection below picks the *first* enabled mode, so re-asserting "on"
        # while the user sits in Katakana would drag them back to Hiragana.
        # (The Windows side needs no such guard: IMC_SETOPENSTATUS to the value
        # already held is a true no-op, and skipping it would only buy an extra
        # cross-process round trip.)
        if self.get_status() is on:
            return True
        if on:
            ok = self._select_input_mode(api, want_roman=False)
        else:
            # Preferred: the current method's Roman mode, which keeps the same
            # input source selected and only flips it to alphanumeric.  That
            # mode is disabled on a default Japanese setup, though, and
            # TISSelectInputSource refuses a disabled source (OSStatus -50), so
            # fall back to the ASCII-capable layout - which is what the Eisu
            # key reaches on such a setup anyway.
            ok = (self._select_input_mode(api, want_roman=True)
                  or self._select_ascii_layout(api))
        if not ok:
            return False
        # Report what was actually reached rather than that a call returned 0.
        return self.get_status() is on

    # ------------------------------------------------------------------

    def _select(self, api, source) -> bool:
        err = api.TISSelectInputSource(source)
        if err != 0:
            logger.debug(f"TISSelectInputSource failed with OSStatus {err}.")
            return False
        return True

    def _select_input_mode(self, api, want_roman: bool) -> bool:
        """Select the first enabled input mode on the wanted side of Roman.

        "First enabled" is as good as it gets with several IMEs installed: TIS
        exposes no most-recently-used order, and there is no way to ask what
        the Kana key itself would pick.  set_status() not disturbing an IME
        that is already on is what keeps that from mattering in practice.
        """
        sources = api.TISCreateInputSourceList(None, False)
        if not sources:
            return False
        try:
            for i in range(api.CFArrayGetCount(sources)):
                source = api.CFArrayGetValueAtIndex(sources, i)
                mode_id = api.property_string(source, api.key_mode_id)
                if mode_id is None:
                    continue
                if (mode_id == ROMAN_MODE) is not want_roman:
                    continue
                if not api.property_bool(source, api.key_select_capable):
                    continue
                if not api.property_bool(source, api.key_enabled):
                    continue
                if self._select(api, source):
                    return True
            return False
        finally:
            api.CFRelease(sources)

    def _select_ascii_layout(self, api) -> bool:
        source = api.TISCopyCurrentASCIICapableKeyboardLayoutInputSource()
        if not source:
            logger.debug("No ASCII-capable keyboard layout to fall back to.")
            return False
        try:
            return self._select(api, source)
        finally:
            api.CFRelease(source)
