"""keyhac.core.settings - the write-through JSON app-state store."""

import json

from keyhac.core.settings import Settings


class TestSettings:

    def test_missing_file_is_empty(self, tmp_path):
        s = Settings(str(tmp_path / "settings.json"))
        assert s.get("console_visible") is None
        assert s.get("console_visible", True) is True

    def test_set_is_write_through(self, tmp_path):
        path = tmp_path / "settings.json"
        Settings(str(path)).set("console_visible", False)
        # A fresh instance (a new process, effectively) sees the value.
        assert Settings(str(path)).get("console_visible") is False
        assert json.loads(path.read_text()) == {"console_visible": False}

    def test_set_creates_parent_dir(self, tmp_path):
        path = tmp_path / "state" / "settings.json"
        Settings(str(path)).set("k", 1)
        assert Settings(str(path)).get("k") == 1

    def test_unchanged_set_does_not_rewrite(self, tmp_path):
        path = tmp_path / "settings.json"
        s = Settings(str(path))
        s.set("k", 1)
        path.write_text("sentinel not rewritten")
        s.set("k", 1)  # unchanged: must not touch the file
        assert path.read_text() == "sentinel not rewritten"
        s.set("k", 2)  # changed: rewrites
        assert json.loads(path.read_text()) == {"k": 2}

    def test_none_is_a_storable_value(self, tmp_path):
        path = tmp_path / "settings.json"
        s = Settings(str(path))
        s.set("k", None)
        assert json.loads(path.read_text()) == {"k": None}

    def test_corrupt_file_is_ignored(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("{not json")
        s = Settings(str(path))
        assert s.get("k", "default") == "default"
        s.set("k", 1)  # and the store still works (overwrites the corpse)
        assert json.loads(path.read_text()) == {"k": 1}

    def test_non_object_json_is_ignored(self, tmp_path):
        path = tmp_path / "settings.json"
        path.write_text("[1, 2, 3]")
        assert Settings(str(path)).get("k") is None
