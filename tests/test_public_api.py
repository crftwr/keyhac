"""One list of public names (#278): `keyhac.__all__`, and what is derived from it.

The generator's lists, the skill's import lists and `__all__` used to be written
separately, and they disagreed: a name could be importable and undocumented, or
documented in a guide and not importable.
"""

import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import keyhac  # noqa: E402
import keyhac.core.ui as ui_module  # noqa: E402
import public_api  # noqa: E402

REFERENCES = ROOT / "keyhac" / "skills" / "keyhac" / "references"


def test_every_exported_name_exists():
    for name in keyhac.__all__:
        assert hasattr(keyhac, name), name


def test_every_exported_name_is_documented_in_one_reference():
    documented = set(public_api.API_NAMES) | set(public_api.ACTION_API_NAMES)
    undocumented = set(keyhac.__all__) - documented - set(public_api.NOT_DOCUMENTED)
    assert undocumented == set()


def test_a_documented_name_is_exported_unless_it_is_listed_as_reached():
    documented = set(public_api.API_NAMES) | set(public_api.ACTION_API_NAMES)
    reached = set(public_api.REACHED_BY_CONFIG) | set(public_api.REACHED_BY_ACTION)
    assert documented - set(keyhac.__all__) == reached


def test_a_reached_name_is_not_importable_and_is_in_the_ui_module():
    """These are reached precisely so that the config namespace stays small."""
    for name in public_api.REACHED_BY_ACTION:
        assert name not in keyhac.__all__
        assert hasattr(ui_module, name), name


@pytest.mark.parametrize("reference", ["practice.md", "actions.md"])
def test_the_skill_teaches_exactly_the_action_import_list(reference):
    text = (REFERENCES / reference).read_text(encoding="utf-8")
    found = re.findall(r"from keyhac import \(([^)]*)\)", text)
    assert len(found) == 1, "one import list per reference"
    taught = {name.strip() for name in found[0].split(",")}
    assert taught == set(public_api.ACTION_IMPORTS)


def test_the_guide_can_use_candidate_preview_through_the_star_import():
    """doc/configuration.md's preview example has no import of its own."""
    namespace = {}
    exec("from keyhac import *", namespace)
    assert "CandidatePreview" in namespace
