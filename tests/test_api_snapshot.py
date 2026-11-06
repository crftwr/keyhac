"""The public API, held still (#278).

`tests/data/api_snapshot.txt` lists every public name and signature with its status.
Any difference from what the code says now fails here. Regenerating the file is the
deliberate step, and it refuses a breaking change within a major version.
"""

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))

import api_snapshot  # noqa: E402


def _method(*params):
    return json.dumps([list(p) for p in params])


def test_the_snapshot_is_current():
    stored = api_snapshot.parse(api_snapshot.SNAPSHOT.read_text(encoding="utf-8"))
    current = api_snapshot.build()
    changes = api_snapshot.classify(stored, current)
    if not changes and api_snapshot.major(stored) == api_snapshot.major(current):
        return
    lines = "\n".join(f"  {category}: {name}: {why}" for category, name, why in changes)
    pytest.fail(f"The public API differs from tests/data/api_snapshot.txt:\n{lines}\n"
                "A breaking change has to wait for the next major "
                "(doc/dev/next-major.md). Otherwise run `make api-snapshot` and commit it.")


def test_the_snapshot_round_trips():
    items = api_snapshot.build()
    assert api_snapshot.parse(api_snapshot.render(items)) == items


def test_it_holds_the_documented_surface_and_not_the_wiring():
    items = api_snapshot.build()
    assert items["Keymap.define_keytable"][1] == "method"
    assert items["mcp.describe_screen"][1] == "tool"
    assert "Keymap.char_for_key" not in items        # lazydocs: ignore
    assert "Keymap._on_key_event" not in items       # underscored
    assert items["Keymap.on_key_event"][0] == "deprecated"
    assert "WaitTimeout.with_traceback" not in items    # Python's, not ours


@pytest.mark.parametrize("old, new, category", [
    (None, ("stable", "method", "[]"), "addition"),
    (None, ("preview", "method", "[]"), "preview"),
    (("stable", "method", "[]"), None, "breaking"),
    (("deprecated", "method", "[]"), None, "breaking"),
    (("preview", "method", "[]"), None, "preview"),
    (("stable", "method", "[]"), ("deprecated", "method", "[]"), "deprecation"),
    (("stable", "method", "[]"), ("preview", "method", "[]"), "breaking"),
    (("preview", "method", "[]"), ("stable", "method", "[]"), "addition"),
    (("stable", "property", "rw"), ("stable", "property", "ro"), "breaking"),
    (("stable", "property", "ro"), ("stable", "property", "rw"), "addition"),
    (("stable", "method", "[]"), ("stable", "property", "ro"), "breaking"),
])
def test_what_a_change_counts_as(old, new, category):
    before = {"X.y": old} if old else {}
    after = {"X.y": new} if new else {}
    assert api_snapshot.classify(before, after)[0][0] == category


@pytest.mark.parametrize("old, new, reason", [
    (_method(("a", "pk", None)), _method(), "removed"),
    (_method(("a", "pk", None), ("b", "pk", None)),
     _method(("b", "pk", None), ("a", "pk", None)), "moved"),
    (_method(("a", "pk", "1")), _method(("a", "pk", None)), "lost its default"),
    (_method(("a", "pk", "1")), _method(("a", "pk", "2")), "default changed"),
    (_method(), _method(("a", "pk", None)), "required parameter"),
    (_method(("a", "pk", None)), _method(("a", "kw", None)), "positional"),
])
def test_a_signature_change_that_breaks_callers(old, new, reason):
    change = api_snapshot.classify({"X.y": ("stable", "method", old)},
                                   {"X.y": ("stable", "method", new)})[0]
    assert change[0] == "breaking" and reason in change[2]


def test_a_new_optional_parameter_is_an_addition():
    change = api_snapshot.classify(
        {"X.y": ("stable", "method", _method(("a", "pk", None)))},
        {"X.y": ("stable", "method", _method(("a", "pk", None), ("b", "pk", "1")))})[0]
    assert change[0] == "addition"


@pytest.mark.parametrize("old, new, category", [
    ([["a", True]], [["a", True], ["b", False]], "addition"),
    ([["a", True]], [["a", True], ["b", True]], "breaking"),
    ([["a", False]], [["a", True]], "breaking"),
    ([["a", True]], [], "breaking"),
])
def test_a_tool_parameter_change(old, new, category):
    change = api_snapshot.classify({"mcp.t": ("stable", "tool", json.dumps(old))},
                                   {"mcp.t": ("stable", "tool", json.dumps(new))})[0]
    assert change[0] == category


class TestWrite:

    def _prepared(self, tmp_path, monkeypatch, stored):
        snapshot = tmp_path / "api_snapshot.txt"
        snapshot.write_text(api_snapshot.render(stored), encoding="utf-8")
        monkeypatch.setattr(api_snapshot, "SNAPSHOT", snapshot)
        monkeypatch.setattr(api_snapshot, "ROOT", tmp_path)
        return snapshot

    def test_it_refuses_a_break_within_a_major(self, tmp_path, monkeypatch):
        items = api_snapshot.build()
        stored = {**items, "Gone.member": ("stable", "method", "[]")}
        snapshot = self._prepared(tmp_path, monkeypatch, stored)
        before = snapshot.read_text(encoding="utf-8")
        assert api_snapshot.main(["--write"]) == 1
        assert snapshot.read_text(encoding="utf-8") == before

    def test_it_takes_a_break_across_a_major(self, tmp_path, monkeypatch):
        items = api_snapshot.build()
        old_major = str(api_snapshot.major(items) - 1)
        stored = {**items, "Gone.member": ("stable", "method", "[]"),
                  api_snapshot.MAJOR_ROW: ("stable", "version", old_major)}
        snapshot = self._prepared(tmp_path, monkeypatch, stored)
        assert api_snapshot.main(["--write"]) == 0
        assert "Gone.member" not in snapshot.read_text(encoding="utf-8")

    def test_check_fails_on_any_difference(self, tmp_path, monkeypatch):
        items = api_snapshot.build()
        stored = dict(items)
        stored.pop("Keymap.define_keytable")
        self._prepared(tmp_path, monkeypatch, stored)
        assert api_snapshot.main(["--check"]) == 1
