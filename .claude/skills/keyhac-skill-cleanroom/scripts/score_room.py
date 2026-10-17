"""Score a clean room: what can be checked, and what is left to read.

    python .claude/skills/keyhac-skill-cleanroom/scripts/score_room.py <room> [case]

`make cleanroom` prints this for every room it drives. Run it by hand for a
room you drove yourself; the case's own outcome check needs the evidence the
runner collects, so a hand-driven room gets the mechanical half only.
"""

import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import outcome  # noqa: E402

CHECK = pathlib.Path(__file__).resolve().with_name("check.py")


def score(room: pathlib.Path, case=None) -> int:
    """Print the mechanical half and the questions; return how many questions."""
    # What the room wrote: its own copies in the room, and what the runner
    # collected from extensions/. Not the bundle, and not the evidence's other
    # folders, which hold the operator's-side files.
    def written_by_the_room(path: pathlib.Path) -> bool:
        parts = path.relative_to(room).parts
        if parts[0] == outcome.EVIDENCE:
            return parts[1:2] == ("extensions",)
        return parts[0] != "skills"

    actions = sorted(p for p in room.rglob("*.py") if written_by_the_room(p))
    # Advisory, and deliberately not the verdict. check.py is a set of
    # heuristics, and one of them - a list appended to in a function that also
    # raises - fired on a per-page header mapping whose loss costs nothing,
    # because the rule it stands for is about work already done being thrown
    # away. What decides the verdict is the case's outcome check, which reads
    # what the run produced; this is printed for a person to weigh.
    if actions:
        print("== mechanical (scripts/check.py), advisory ==")
        for action in actions:
            subprocess.call([sys.executable, str(CHECK), str(action)])
        print()

    questions = room / "QUESTIONS.md"
    text = questions.read_text(encoding="utf-8") if questions.exists() else ""
    entries = text.count("\n## ")
    print("== the actual result ==")
    print(f"{questions}: {entries} question(s)")
    if entries == 0:
        print("  Zero is not a pass. A run with nothing to guess at either")
        print("  measured nothing or broke a rule - read the log before")
        print("  believing it.")
    else:
        print(text)

    if case is not None and getattr(case, "MUST", ""):
        print("== left to read: the MUST list, against the transcript and the reply ==")
        for line in case.MUST.strip().splitlines():
            print(f"    {line}")
        print()
        print("  A MUST missed is a skill defect, not a model defect: fix the")
        print("  skill, rebuild the bundle, and re-run every case if the change")
        print("  was to a rule.")
    print()
    return entries


def main(argv: list[str]) -> int:
    if len(argv) not in (1, 2):
        raise SystemExit(__doc__)
    room = pathlib.Path(argv[0])
    case = outcome.load_cases([argv[1]])[0] if len(argv) == 2 else None
    failures = []
    if case is not None and (room / outcome.EVIDENCE / "run.json").exists():
        failures = outcome.failures(case, outcome.Outcome.from_room(room))
        for failure in failures:
            print(f"  FAIL: {failure}")
    # A room driven by hand has a transcript only if it was saved as one;
    # friction.py says so when there is none.
    import friction
    print(friction.report(room, failures))
    score(room, case)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
