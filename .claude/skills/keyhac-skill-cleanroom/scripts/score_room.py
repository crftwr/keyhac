"""Score a clean room: the mechanical half, and what to read for the rest.

    python .claude/skills/keyhac-skill-cleanroom/scripts/score_room.py <room>
"""

import pathlib
import subprocess
import sys

SKILL = pathlib.Path(__file__).resolve().parents[1]
ROOT = SKILL.parents[2]
CHECK = ROOT / "keyhac" / "skills" / "keyhac-action-authoring" / "evals" / "check.py"


def score(room: pathlib.Path) -> int:
    """The mechanical half, printed; the judgement half, pointed at."""
    actions = sorted(p for p in room.rglob("*.py") if "skills" not in p.parts)
    if not actions:
        print(f"no action left in {room} - the room was meant to leave one")

    print("== mechanical (evals/check.py) ==")
    failures = 0
    for action in actions:
        failures += subprocess.call([sys.executable, str(CHECK), str(action)])

    questions = room / "QUESTIONS.md"
    text = questions.read_text() if questions.exists() else ""
    entries = text.count("\n## ")
    print()
    print("== the actual result ==")
    print(f"{questions}: {entries} question(s)")
    if entries == 0:
        print("  Zero is not a pass. A run with nothing to guess at either")
        print("  measured nothing or broke a rule - read the log before")
        print("  believing it.")
    else:
        print(text)

    print("== left to read ==")
    print("  The case's 'must' list, against what came out. A 'must' missed is")
    print("  a skill defect, not a model defect: fix the skill, rebuild the")
    print("  bundle, and re-run every case if the change was to a rule.")
    print()
    print(f"  Then: rm -rf {room}. A room driven by hand also leaves its")
    print("  action in ~/.keyhac/extensions/ - remove it, since ActionsSource")
    print("  imports every module there. `make cleanroom` takes it back out.")
    return failures


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit(__doc__)
    return score(pathlib.Path(argv[0]))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
