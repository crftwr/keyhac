"""The skill bundle carries what the skill tells the model to check.

`tools/build_skill_bundle.py` resolves `{VERSION}` on the way into the zip, and
SKILL.md's version check compares that with the `keyhac:` line of get_focus
(issue #191) - so the built SKILL.md has to state the packaged version in the
form the check reads, and nothing the repository leaves lying in the skill
folder may ride along.
"""

import pathlib
import re
import sys
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import keyhac  # noqa: E402
import build_skill_bundle  # noqa: E402


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    skill = tmp_path / "keyhac"
    source = ROOT / "keyhac" / "skills" / "keyhac"
    for path in source.rglob("*"):
        if path.is_file():
            target = skill / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
    # What an untracked leftover looks like: bytecode beside nothing.
    (skill / "evals" / "__pycache__").mkdir(parents=True, exist_ok=True)
    (skill / "evals" / "__pycache__" / "check.cpython-314.pyc").write_bytes(b"\0")
    monkeypatch.setattr(build_skill_bundle, "ROOT", tmp_path)
    written, problems, output = build_skill_bundle.build(skill)
    assert problems == []
    return zipfile.ZipFile(output), written


def test_the_skill_states_the_version_it_was_packaged_from(bundle):
    archive, _written = bundle
    text = archive.read("SKILL.md").decode("utf-8")
    assert "{VERSION}" not in text
    assert f"packaged from **Keyhac {keyhac.__version__}**" in text
    # The check is near the top, not in the trailing stamp.
    assert text.index("Check the version") < text.index("## Rules for every task")


def test_the_check_names_the_line_the_tool_answers_with():
    """The skill's wording and the tool's output are one contract."""
    skill = (ROOT / "keyhac" / "skills" / "keyhac" / "SKILL.md").read_text("utf-8")
    assert "`keyhac: X.Y.Z`" in skill and "`get_focus`" in skill


def test_no_bytecode_ships(bundle):
    archive, written = bundle
    assert not [n for n in archive.namelist()
                if "__pycache__" in n or n.endswith(".pyc")]
    assert not [n for n in written if re.search(r"__pycache__|\.pyc$", n)]
