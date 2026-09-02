"""Build a clean room: the bundle, the rules, the task, and nothing else.

    python .claude/skills/keyhac-skill-cleanroom/scripts/open_room.py \
        --task "Save every tab of the front Chrome window as a PDF."

Prints the room's path and the sentence to hand the fresh session. Every step
is here rather than in prose because the one that used to be prose - "copy the
rules in verbatim" - is the only step a transcription error can quietly ruin.
"""

import argparse
import datetime
import pathlib
import shutil
import sys
import zipfile

SKILL = pathlib.Path(__file__).resolve().parents[1]
ROOT = SKILL.parents[2]

#: Rooms live here: outside every checkout, and somewhere a person can find
#: them. Not a temp directory - the first room built was in
#: /var/folders/9k/kmx0.../T/ and the session that went looking for it reported
#: that it did not exist. QUESTIONS.md is the product of the exercise and has
#: no business in a folder the OS may sweep.
ROOMS = pathlib.Path.home() / "keyhac-cleanroom"


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


def _refuse_a_room_inside_the_checkout(room: pathlib.Path) -> None:
    """A room under the checkout is a contaminated room, silently.

    Claude Code loads CLAUDE.md from every parent of the working directory, so
    a room at <checkout>/cleanroom/ starts its session with the source layout,
    the design notes and the UINode contract already in context - the exact
    material RULES.md forbids. That failure is worse than the obvious one,
    because the operator did start the session *in the room* and has every
    reason to believe the test was clean.
    """
    if room == ROOT or ROOT in room.parents:
        raise SystemExit(
            f"{room} is inside the checkout. Claude Code reads CLAUDE.md from "
            f"every parent directory, so a room there is contaminated before "
            f"the session begins - and it looks clean. Put it anywhere else; "
            f"the default is {ROOMS}/.")


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
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    room = (pathlib.Path(args.room).expanduser().resolve() if args.room
            else ROOMS / f"{keyhac.__version__}-{stamp}")
    _refuse_a_room_inside_the_checkout(room)
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
    print("Open the room, in a session of its own:")
    print()
    print(f"    cd {room} && claude")
    print()
    print("and say only:")
    print()
    print("    Read RULES.md, then do TASK.md.")
    print()
    print("Starting it anywhere else - the checkout especially - loads that")
    print("directory's CLAUDE.md and disqualifies the run. Answer nothing it")
    print("asks: a question you answer is a finding you have destroyed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
