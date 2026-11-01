"""tools/release_review.py - the mechanical half of the pre-tag review. Each
rule is judged on the lines a release added, so these feed it a source and
the set of added line numbers, with no git involved."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import release_review  # noqa: E402


def _rules(items):
    return [item.rule for item in items]


class TestImports:

    def test_os_module_in_core_is_a_finding(self):
        source = "import os\nimport ctypes\nfrom AppKit import NSApp\n"
        found = release_review.check_imports("keyhac/core/x.py", source, {1, 2, 3})
        assert [f.where for f in found] == ["keyhac/core/x.py:2", "keyhac/core/x.py:3"]

    def test_only_added_lines_are_judged(self):
        source = "import ctypes\n"
        assert release_review.check_imports("keyhac/core/x.py", source, set()) == []

    def test_ui_may_not_reach_appkit_either(self):
        found = release_review.check_imports("keyhac/ui/x.py", "import objc\n", {1})
        assert _rules(found) == ["ui"]

    def test_platform_layer_may(self):
        assert release_review.check_imports("keyhac/platform/win/x.py",
                                            "import ctypes\n", {1}) == []

    def test_lazy_import_inside_a_function_counts(self):
        source = "def f():\n    import Quartz\n"
        assert _rules(release_review.check_imports("keyhac/core/x.py", source, {2})) \
            == ["core"]


class TestPrototypes:

    SOURCE = (
        "user32 = ctypes.WinDLL('user32', use_last_error=True)\n"
        "user32.GetForegroundWindow.restype = wintypes.HWND\n"
        "user32.GetForegroundWindow.argtypes = []\n"
        "hwnd = user32.GetForegroundWindow()\n"
        "user32.SetWindowsHookExW(13, proc, None, 0)\n"
    )

    def test_call_without_restype_is_a_finding(self):
        # SetWindowsHookExW's handle once came back truncated to c_int.
        found = release_review.check_prototypes("keyhac/platform/win/hook.py",
                                                self.SOURCE, {4, 5})
        assert [f.where for f in found] == ["keyhac/platform/win/hook.py:5"]

    def test_declared_call_passes(self):
        assert release_review.check_prototypes("keyhac/platform/win/x.py",
                                               self.SOURCE, {4}) == []

    def test_file_without_dll_handles_is_skipped(self):
        assert release_review.check_prototypes("keyhac/core/x.py",
                                               "x = user32.Foo()\n", {1}) == []


_API_BASE = """# API reference

## <kbd>class</kbd> `Keymap`

### <kbd>method</kbd> `Keymap.define_keytable`

```python
define_keytable(name, focus_path_pattern=None) → KeyTable
```

### <kbd>method</kbd> `Keymap.reload_config`

```python
reload_config() → bool
```

#### <kbd>property</kbd> Keymap.focus
"""


class TestApiEntries:

    def test_headings_and_signatures(self):
        entries = release_review.api_entries(_API_BASE)
        assert entries["Keymap"] == ("class", None)
        assert entries["Keymap.define_keytable"] == (
            "method", "define_keytable(name, focus_path_pattern=None) → KeyTable")
        assert entries["Keymap.focus"] == ("property", None)

    def test_parameters(self):
        assert release_review.parameters("f(a, b=1, *args, c: int = 2, **kw) → None") \
            == {"a": False, "b": True, "args": True, "c": True, "kw": True}
        assert release_review.parameters("f(x=(1, 2), y=None)") == {"x": True, "y": True}
        assert release_review.parameters("f() → None") == {}


class TestApiChanges:

    def _changes(self, monkeypatch, head_text):
        texts = {("base", "doc/config-api.md"): _API_BASE,
                 ("head", "doc/config-api.md"): head_text}
        monkeypatch.setattr(release_review, "show",
                            lambda ref, path: texts.get((ref, path)))
        return release_review.api_changes("base", "head")

    def test_unchanged_api_has_nothing(self, monkeypatch):
        assert self._changes(monkeypatch, _API_BASE) == ([], [])

    def test_removed_entry_is_a_finding(self, monkeypatch):
        head = _API_BASE.replace("#### <kbd>property</kbd> Keymap.focus\n", "")
        findings, _ = self._changes(monkeypatch, head)
        assert [f.where for f in findings] == ["Keymap.focus"]

    def test_removed_parameter_is_a_finding(self, monkeypatch):
        head = _API_BASE.replace("define_keytable(name, focus_path_pattern=None)",
                                 "define_keytable(name)")
        findings, _ = self._changes(monkeypatch, head)
        assert "focus_path_pattern" in findings[0].message

    def test_parameter_without_default_is_a_finding(self, monkeypatch):
        head = _API_BASE.replace("reload_config()", "reload_config(quiet)")
        findings, _ = self._changes(monkeypatch, head)
        assert "no default" in findings[0].message

    def test_parameter_with_default_is_a_note(self, monkeypatch):
        head = _API_BASE.replace("reload_config()", "reload_config(quiet=False)")
        findings, notes = self._changes(monkeypatch, head)
        assert findings == [] and "quiet" in notes[0].message

    def test_new_entry_is_a_note_and_camel_case_a_finding(self, monkeypatch):
        head = _API_BASE + "\n### <kbd>method</kbd> `Keymap.getFocus`\n" \
                           "\n### <kbd>method</kbd> `Keymap.get_focus`\n"
        findings, notes = self._changes(monkeypatch, head)
        assert [f.where for f in findings] == ["Keymap.getFocus"]
        assert {n.where for n in notes} == {"Keymap.getFocus", "Keymap.get_focus"}

    def test_porting_history_in_new_text_is_a_note(self, monkeypatch):
        head = _API_BASE + "\nThe keyhac-win name for this.\n"
        findings, notes = self._changes(monkeypatch, head)
        assert findings == [] and _rules(notes) == ["docstring"]


class TestPlacement:

    def test_new_root_file_and_stray_test(self):
        assert release_review.check_placement("NOTES.md", {"README.md"})
        assert release_review.check_placement("README.md", {"README.md"}) == []
        assert release_review.check_placement("tools/test_x.py", set())
        assert release_review.check_placement("tests/test_x.py", set()) == []
