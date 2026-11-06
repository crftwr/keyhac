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


def _snapshot(*rows):
    lines = ["stable\tversion\tkeyhac.major\t2"]
    lines += ["\t".join(row) for row in rows]
    return "\n".join(lines) + "\n"


_METHOD = ("stable", "method", "Keymap.configure", '[["self", "pk", null]]')


class TestSnapshotChanges:

    def _changes(self, monkeypatch, base_rows, head_rows, base_doc=None, head_doc=None):
        texts = {("base", release_review.API_SNAPSHOT): _snapshot(*base_rows),
                 ("head", release_review.API_SNAPSHOT): _snapshot(*head_rows),
                 ("base", "doc/config-api.md"): base_doc,
                 ("head", "doc/config-api.md"): head_doc}
        monkeypatch.setattr(release_review, "show",
                            lambda ref, path: texts.get((ref, path)))
        return release_review.api_changes("base", "head")

    def test_unchanged_api_has_nothing(self, monkeypatch):
        assert self._changes(monkeypatch, [_METHOD], [_METHOD]) == ([], [])

    def test_a_name_that_only_left_the_reference_is_not_a_finding(self, monkeypatch):
        # Keymap.mcp_server_running on 2.8.0: still a deprecated alias, listed in
        # the snapshot, gone from the reference on purpose.
        row = ("deprecated", "attribute", "Keymap.mcp_server_running")
        base_doc = "#### <kbd>property</kbd> Keymap.mcp_server_running\n"
        assert self._changes(monkeypatch, [row], [row], base_doc, "") == ([], [])

    def test_a_class_that_is_new_only_to_the_reference_is_not_a_note(self, monkeypatch):
        # CandidatePreview on 2.8.0: exported since before the reference listed it.
        row = ("stable", "class", "CandidatePreview")
        head_doc = "## <kbd>class</kbd> `CandidatePreview`\n"
        assert self._changes(monkeypatch, [row], [row], "", head_doc) == ([], [])

    def test_removal_is_a_finding(self, monkeypatch):
        findings, _ = self._changes(monkeypatch, [_METHOD], [])
        assert [f.where for f in findings] == ["Keymap.configure"]

    def test_removing_a_deprecated_name_is_a_finding(self, monkeypatch):
        row = ("deprecated", "attribute", "Keymap.on_key_event")
        findings, _ = self._changes(monkeypatch, [row], [])
        assert [f.where for f in findings] == ["Keymap.on_key_event"]

    def test_incompatible_signature_is_a_finding(self, monkeypatch):
        head = _METHOD[:3] + ('[["self", "pk", null], ["quiet", "pk", null]]',)
        findings, _ = self._changes(monkeypatch, [_METHOD], [head])
        assert "quiet" in findings[0].message

    def test_a_break_across_a_major_is_a_note(self, monkeypatch):
        texts = {("base", release_review.API_SNAPSHOT): _snapshot(_METHOD),
                 ("head", release_review.API_SNAPSHOT):
                     "stable\tversion\tkeyhac.major\t3\n"}
        monkeypatch.setattr(release_review, "show",
                            lambda ref, path: texts.get((ref, path)))
        findings, notes = release_review.api_changes("base", "head")
        assert findings == [] and "major version moved" in notes[0].message

    def test_new_deprecation_is_listed_for_the_note(self, monkeypatch):
        head = ("deprecated",) + _METHOD[1:]
        findings, notes = self._changes(monkeypatch, [_METHOD], [head])
        assert findings == [] and "deprecated" in notes[0].message

    def test_a_name_first_listed_as_deprecated_is_not_new_api(self, monkeypatch):
        # Keymap.on_key_event on 2.8.0: public only in the naming sense until its
        # wiring moved to an underscored name, then listed as a deprecated alias.
        row = ("deprecated", "attribute", "Keymap.on_key_event")
        findings, notes = self._changes(monkeypatch, [], [row])
        assert findings == [] and "deprecated" in notes[0].message \
            and "minor bump" not in notes[0].message

    def test_addition_is_a_note_and_asks_for_a_minor_bump(self, monkeypatch):
        row = ("stable", "class", "NewThing")
        findings, notes = self._changes(monkeypatch, [], [row])
        assert findings == [] and "new class" in notes[0].message \
            and "minor bump" in notes[0].message

    def test_preview_addition_is_a_note(self, monkeypatch):
        row = ("preview", "method", "Keymap.try_this", "[]")
        findings, notes = self._changes(monkeypatch, [], [row])
        assert findings == [] and "new Preview method" in notes[0].message

    def test_removing_a_preview_is_a_note(self, monkeypatch):
        row = ("preview", "method", "Keymap.try_this", "[]")
        findings, notes = self._changes(monkeypatch, [row], [])
        assert findings == [] and "removed" in notes[0].message

    def test_camel_case_is_a_finding_for_any_new_name(self, monkeypatch):
        stable = ("stable", "method", "Keymap.getFocus", "[]")
        preview = ("preview", "method", "Keymap.tryThis", "[]")
        findings, _ = self._changes(monkeypatch, [], [stable, preview])
        assert [f.where for f in findings] == ["Keymap.getFocus", "Keymap.tryThis"]

    def test_docstring_check_stays_on_the_reference(self, monkeypatch):
        findings, notes = self._changes(monkeypatch, [_METHOD], [_METHOD], "",
                                        "The keyhac-win name for this.\n")
        assert findings == [] and _rules(notes) == ["docstring"]


class TestPlacement:

    def test_new_root_file_and_stray_test(self):
        assert release_review.check_placement("NOTES.md", {"README.md"})
        assert release_review.check_placement("README.md", {"README.md"}) == []
        assert release_review.check_placement("tools/test_x.py", set())
        assert release_review.check_placement("tests/test_x.py", set()) == []
