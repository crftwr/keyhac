"""The authoring skill's mechanical rules (keyhac/skills/keyhac-action-authoring).

Two directions, because a checker that only ever says "ok" is a rubber stamp:
the hand-written examples must pass, and a fixture that breaks every rule must
be caught. Together they pin the skill's rules to code that demonstrably
follows them.
"""

import importlib.util
import pathlib

import pytest

SKILL = pathlib.Path(__file__).resolve().parents[1] / "keyhac/skills/keyhac-action-authoring"
EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples/actions"


@pytest.fixture(scope="module")
def check():
    spec = importlib.util.spec_from_file_location(
        "action_evals_check", SKILL / "evals/check.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


#: Discovered rather than listed. The list this replaces named four files and
#: was written when there were four; snapshot_settings.py was added and never
#: joined it, and neither did the Windows one - so two examples sat unchecked
#: while the suite reported the rules as enforced. A glob cannot drift.
ACTIONS = sorted(p for p in EXAMPLES.rglob("*.py") if not p.name.startswith("_"))


def test_the_examples_were_found_at_all():
    """Guards the glob: a layout change that empties it must fail loudly rather
    than silently checking nothing, which is the failure mode it exists to fix."""
    assert len(ACTIONS) >= 6
    assert {p.parent.name for p in ACTIONS} == {"mac", "win"}


@pytest.mark.parametrize("action", ACTIONS, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_the_hand_written_actions_follow_the_rules(check, action):
    """These are what the rules were derived from; a rule they fail is a rule
    that is wrong, or an example that needs fixing - both worth knowing."""
    assert check(action) == []


def test_every_rule_is_actually_enforced(check):
    problems = "\n".join(check(SKILL / "evals/fixtures/violates_every_rule.py"))
    for rule in ("rule 1", "rule 2", "rule 3", "rule 4", "rule 6", "rule 7"):
        assert rule in problems, f"{rule} was not caught"


def test_a_read_only_action_is_not_required_to_wait(check, tmp_path):
    """Scraping a screen has nothing to wait for; demanding a wait there would
    train the skill to add pointless ones."""
    path = tmp_path / "read_only.py"
    path.write_text(
        "def run(window, ui):\n"
        "    return [n.all_text for n in ui.find_elements(window, role='AXRow')]\n")
    assert check(path) == []


def test_the_skill_and_its_references_exist(check):
    """A skill whose references have drifted away is worse than none."""
    body = (SKILL / "SKILL.md").read_text()
    assert body.startswith("---"), "SKILL.md needs frontmatter"
    for reference in ("references/practice.md", "references/quirks.md"):
        assert (SKILL / reference).exists()
        assert reference.split("/")[-1] in body or reference in body
