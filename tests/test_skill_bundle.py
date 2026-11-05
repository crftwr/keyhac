"""The skill bundle carries what the skill tells the model to check.

`tools/build_skill_bundle.py` resolves `{VERSION}` on the way into the zip, and
SKILL.md's version check compares that with the `keyhac:` line of get_focus
(issue #191) - so the built SKILL.md has to state the packaged version in the
form the check reads, and nothing the repository leaves lying in the skill
folder may ride along.
"""

import pathlib
import re
import subprocess
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


SHA = "0123456789abcdef0123456789abcdef01234567"


def _answers(head=SHA, tag=SHA, status=""):
    """A stand-in for build_skill_bundle._git, so the checkout's state is moot."""
    def git(*args):
        if args[0] == "status":
            return status
        return tag if "--verify" in args else head
    return git


def test_a_tree_at_its_tag_stamps_the_release():
    assert build_skill_bundle.identity(_answers()) == (
        keyhac.__version__, f"v{keyhac.__version__}")


def test_a_tree_off_its_tag_stamps_a_dev_version_and_links_the_commit():
    version, ref = build_skill_bundle.identity(_answers(tag="f" * 40))
    assert version == f"{keyhac.__version__}+dev.g{SHA[:9]}"
    assert ref == SHA


def test_a_tree_with_no_tag_yet_stamps_a_dev_version():
    version, _ref = build_skill_bundle.identity(_answers(tag=None))
    assert version == f"{keyhac.__version__}+dev.g{SHA[:9]}"


def test_a_dirty_tree_at_its_tag_says_so():
    version, ref = build_skill_bundle.identity(_answers(status=" M keyhac/x.py"))
    assert version == f"{keyhac.__version__}+dev.g{SHA[:9]}.dirty"
    assert ref == SHA


def test_with_no_git_the_stamp_is_the_plain_version():
    assert build_skill_bundle.identity(lambda *_: None) == (
        keyhac.__version__, f"v{keyhac.__version__}")


def test_a_dev_bundle_stamps_its_version_and_links_the_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(build_skill_bundle, "ROOT", tmp_path)
    dev = f"{keyhac.__version__}+dev.g{SHA[:9]}"
    monkeypatch.setattr(build_skill_bundle, "identity", lambda: (dev, SHA))

    _written, problems, output = build_skill_bundle.build(
        ROOT / "keyhac" / "skills" / "keyhac")

    assert problems == []
    assert output.name == f"keyhac-{dev}-skill.zip"
    archive = zipfile.ZipFile(output)
    skill = archive.read("SKILL.md").decode("utf-8")
    assert f"packaged from **Keyhac {dev}**" in skill
    assert f"Packaged from Keyhac {dev}." in skill
    actions = archive.read("references/actions.md").decode("utf-8")
    assert f"keyhac/tree/{SHA}/examples/actions" in actions
    assert "tree/v" not in actions and "+dev" not in actions


def test_the_real_git_answers_mean_what_identity_expects(tmp_path, monkeypatch):
    def git(*args):
        subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t",
                        "-c", "user.email=t@example.invalid", *args],
                       check=True, capture_output=True)

    monkeypatch.setattr(build_skill_bundle, "ROOT", tmp_path)
    git("init")
    (tmp_path / "file").write_text("one")
    git("add", "file")
    git("commit", "-m", "one")
    assert "+dev" in build_skill_bundle.identity()[0], "no tag yet"

    git("tag", "-a", f"v{keyhac.__version__}", "-m", "release")
    assert build_skill_bundle.identity()[0] == keyhac.__version__

    (tmp_path / "file").write_text("two")
    assert build_skill_bundle.identity()[0].endswith(".dirty")

    git("commit", "-am", "two")
    version, ref = build_skill_bundle.identity()
    assert "+dev" in version and not version.endswith(".dirty")
    assert re.fullmatch(r"[0-9a-f]{40}", ref)


@pytest.mark.parametrize("url, moving", [
    ("https://github.com/crftwr/keyhac/tree/main/examples", True),
    ("https://github.com/crftwr/keyhac/tree/v2.7.0/examples", False),
    (f"https://github.com/crftwr/keyhac/tree/{SHA}/examples", False),
    (f"https://raw.githubusercontent.com/crftwr/keyhac/{SHA}/x.py", False),
    ("https://raw.githubusercontent.com/crftwr/keyhac/main/x.py", True),
])
def test_a_link_may_name_a_tag_or_a_commit_but_not_a_branch(url, moving):
    assert bool(build_skill_bundle.MOVING_REF.search(url)) is moving


def test_no_bytecode_ships(bundle):
    archive, written = bundle
    assert not [n for n in archive.namelist()
                if "__pycache__" in n or n.endswith(".pyc")]
    assert not [n for n in written if re.search(r"__pycache__|\.pyc$", n)]
