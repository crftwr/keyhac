"""Build a clean room: the bundle, the rules, the task, and nothing else.

    python .claude/skills/keyhac-skill-cleanroom/scripts/open_room.py \
        --task "Save every tab of the front Chrome window as a PDF."

Prints the room's path and the sentence to hand the fresh session. Every step
is here rather than in prose because the one that used to be prose - "copy the
rules in verbatim" - is the only step a transcription error can quietly ruin.
"""

import argparse
import pathlib
import shutil
import sys
import tempfile
import zipfile

SKILL = pathlib.Path(__file__).resolve().parents[1]
ROOT = SKILL.parents[2]


def bundle_for(version: str) -> pathlib.Path:
    """This version's action-authoring bundle, or a refusal.

    Refusing on a missing bundle rather than falling back to the newest one on
    disk: an older bundle tests an older skill and reports its gaps as though
    they were this one's, which is a wrong answer that looks like a right one.
    """
    wanted = ROOT / "dist" / f"keyhac-action-authoring-{version}-skill.zip"
    if not wanted.exists():
        found = sorted(p.name for p in (ROOT / "dist").glob("*skill.zip"))
        raise SystemExit(f"no bundle for {version} - run `make skill-bundle` "
                         f"first (dist/ has: {found})")
    return wanted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True,
                        help="the prompt, intent only - no API names, and "
                             "never the case's 'must' list, which is the "
                             "scoring key")
    parser.add_argument("--room", help="where to build it; a temp dir by default")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    import keyhac

    bundle = bundle_for(keyhac.__version__)
    room = pathlib.Path(args.room) if args.room else pathlib.Path(
        tempfile.mkdtemp(prefix="keyhac-cleanroom."))
    room.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(bundle) as archive:
        archive.extractall(room / "skill")
    shutil.copy2(SKILL / "room" / "RULES.md", room / "RULES.md")
    (room / "TASK.md").write_text(f"# The task\n\n{args.task.strip()}\n")
    (room / "QUESTIONS.md").write_text(
        "# What the skill did not tell me\n\n"
        "One section per thing you had to guess. Empty means the run measured "
        "nothing - see RULES.md.\n")

    print(f"room:    {room}")
    print(f"bundle:  {bundle.name}")
    print(f"skill:   {len(list((room / 'skill').rglob('*')))} files unpacked")
    print()
    print("Start a session whose working directory is the room, and say only:")
    print()
    print("    Read RULES.md, then do TASK.md.")
    print()
    print("Answer nothing it asks. A question you answer is a finding you have")
    print("destroyed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
