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
import re
import shutil
import sys
import zipfile

SKILL = pathlib.Path(__file__).resolve().parents[1]
ROOT = SKILL.parents[2]
CASES = ROOT / "keyhac" / "skills" / "keyhac-action-authoring" / "evals" / "cases.md"

#: Rooms live in the checkout, gitignored, where the rest of the work is. Not a
#: temp directory - the first room built was in /var/folders/9k/kmx0.../T/ and
#: the session sent to look for it reported that it did not exist, which is a
#: fair conclusion from that path. QUESTIONS.md is the product of the exercise
#: and has no business in a folder the OS may sweep.
#:
#: Being inside the checkout was refused until 2026-09-02, for a good reason
#: that has since been paid for rather than avoided: Claude Code loads CLAUDE.md
#: from every parent of the working directory, so a room here starts with the
#: source layout, the design notes and the UINode contract already in context.
#: `--restricted` was measured to drop all of it - a session in a room under
#: this directory, asked what project it was in without being allowed to read
#: anything, answers UNKNOWN, where a plain one names Keyhac2 and its two
#: languages. It also hides the checkout's own skills and removes Bash.
#:
#: So the room may be here *only* because the runner passes those flags, which
#: makes the flags load-bearing rather than incidental: CANARY below is the
#: room's own CLAUDE.md, which only a session started without them can read.
ROOMS = ROOT / "cleanroom"


#: Both shipped skills go into the room, because that is the machine a user
#: actually has - both installed, and the model picking between them. It also
#: makes the last step of an action task answerable: every one of them ends by
#: handing the operator a `configure()` block to bind a key, and what a key
#: expression may say is the *other* skill's subject. A room holding one skill
#: measures a setup nobody runs.
BUNDLES = ("keyhac-action-authoring", "keyhac-key-table-configuration")


def bundles_for(version: str) -> list[pathlib.Path]:
    """This version's skill bundles, or a refusal.

    Refusing on a missing bundle rather than falling back to the newest one on
    disk: an older bundle tests an older skill and reports its gaps as though
    they were this one's, which is a wrong answer that looks like a right one.
    """
    wanted = [ROOT / "dist" / f"{name}-{version}-skill.zip" for name in BUNDLES]
    if missing := [p.name for p in wanted if not p.exists()]:
        found = sorted(p.name for p in (ROOT / "dist").glob("*skill.zip"))
        raise SystemExit(f"no bundle for {version} ({missing}) - run "
                         f"`make skill-bundle` first (dist/ has: {found})")
    return wanted


#: A room's CLAUDE.md, which a correctly-started session never sees.
#:
#: `--restricted` skips CLAUDE.md discovery, so under the runner this file is
#: dead weight. Under a plain `cd <room> && claude` it is the first thing
#: loaded - together with the checkout's own CLAUDE.md, which is the failure it
#: is here to announce. The contamination is otherwise silent, and silent is
#: the whole problem: the operator did start the session in the room and has
#: every reason to believe the run was clean.
CANARY = """\
# If this arrived on its own, stop

**Did you open this file yourself?** Then nothing is wrong - it is a check
addressed to a different reader, and you should carry on with TASK.md.

This text is only a problem if it reached you *without* being asked for, as
part of the context you started with. That would mean the session was started
without `--restricted`, and Claude Code will have loaded the CLAUDE.md of the
repository above this directory too - the source layout, the design notes, the
whole API contract. The measurement this room exists to take would already be
spoilt.

In that case, and only that case: **do not do the task.** Say that the room was
opened without `--restricted` and stop, because nothing written from there is
evidence about the skill bundle. `make cleanroom` never gets this wrong.
"""


def _refuse_a_room_that_is_neither(room: pathlib.Path) -> None:
    """A room belongs under `cleanroom/`, or outside the checkout entirely.

    Both are defensible - one is isolated by `--restricted`, the other by not
    being in the checkout at all. Anywhere else in the tree is a room that
    looks deliberate and is protected by neither, so it is refused rather than
    documented.
    """
    inside = room == ROOT or ROOT in room.parents
    if inside and ROOMS not in room.parents and room != ROOMS:
        raise SystemExit(
            f"{room} is inside the checkout but not under {ROOMS}/. A room "
            f"there is only clean while the session is started with "
            f"--restricted, and that is worth keeping in one place. Use "
            f"{ROOMS}/ or somewhere outside the checkout.")


#: Cases written as a continuation of another, as data rather than inferred
#: from the wording. Case 2 opens "Same as above" and case 9 "Do the same
#: against Slack": read alone in a room, both point at a task that is not in
#: it. The first real case-2 room said so in its second sentence - "TASK.md
#: references 'same as above' - I need to find the prior task" - and went
#: looking for the previous room, which the confinement refused. It would have
#: measured what the room could guess about the missing half rather than what
#: the case is about.
CONTINUES = {2: 1, 3: 5, 9: 8}


def case_prompt(number: int) -> str:
    """Case `number`'s prompt from cases.md - the quote, and nothing after it.

    Typing the task by hand is where the scoring key leaks: the "must" list
    sits directly under the prompt, and a copy-paste that takes one line too
    many hands the room the answers and produces a run that looks excellent.
    Lifting only the block quote makes that mistake unavailable.
    """
    section = re.search(rf"^### {number}\..*?\n((?:>.*\n)+)", CASES.read_text(),
                        re.MULTILINE)
    if not section:
        raise SystemExit(f"no case {number} in {CASES}")
    quoted = " ".join(line.lstrip("> ").strip()
                      for line in section.group(1).splitlines())
    quoted = quoted.strip().strip('"')
    if previous := CONTINUES.get(number):
        # Put the task it continues in front of it, which is where "above" was
        # pointing all along - as its own paragraph, so that "above" is
        # literally true. Joined into one paragraph it still read as a quote
        # from somewhere else, and case 2's room said so: "as if quoting an
        # earlier instruction that is not in the room". It cost nothing and it
        # is one line to stop costing anything.
        return f"{case_prompt(previous)}\n\n{quoted}"
    return quoted


def build_room(task: str, room: str | None = None) -> pathlib.Path:
    """The bundle, the rules, the task, and nothing else."""
    sys.path.insert(0, str(ROOT))
    import keyhac

    bundles = bundles_for(keyhac.__version__)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    built = (pathlib.Path(room).expanduser().resolve() if room
             else ROOMS / f"{keyhac.__version__}-{stamp}")
    _refuse_a_room_that_is_neither(built)
    built.mkdir(parents=True, exist_ok=True)

    for bundle, name in zip(bundles, BUNDLES):
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(built / "skills" / name)
    (built / "CLAUDE.md").write_text(CANARY)
    shutil.copy2(SKILL / "room" / "RULES.md", built / "RULES.md")
    (built / "TASK.md").write_text(f"# The task\n\n{task.strip()}\n")
    (built / "QUESTIONS.md").write_text(
        "# What the skill did not tell me\n\n"
        "One section per thing you had to guess. Empty means the run measured "
        "nothing - see RULES.md.\n")
    return built


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", type=int,
                        help="an eval case number; its prompt is lifted from "
                             "cases.md and its 'must' list is not")
    source.add_argument("--task",
                        help="the prompt, intent only - no API names, and "
                             "never the case's 'must' list, which is the "
                             "scoring key")
    parser.add_argument("--room",
                        help=f"where to build it; under {ROOMS}/ by default")
    args = parser.parse_args()

    task = case_prompt(args.case) if args.case else args.task
    room = build_room(task, args.room)

    print(f"room:    {room}")
    print(f"task:    {task}")
    print(f"skills:  {', '.join(BUNDLES)} "
          f"({len(list((room / 'skills').rglob('*')))} files)")
    print()
    # Imported here rather than at the top: run_room imports this module, and
    # the flags belong with the code that runs them.
    from run_room import mcp_endpoint, printable_command

    print("Open the room, in a session of its own:")
    print()
    print(f"    {printable_command(room, mcp_endpoint()['bridge'])}")
    print()
    print("and say only:")
    print()
    print("    Read RULES.md, then do TASK.md.")
    print()
    print("Every flag there is load-bearing. The room is inside the checkout,")
    print("so a plain `claude` in it loads the repository's CLAUDE.md and the")
    print("run is spoilt before your first sentence - which is what the room's")
    print("own CLAUDE.md is there to say to a session that can read it.")
    print("Answer nothing it asks: a question you answer is a finding you have")
    print("destroyed.")
    print()
    print("`make cleanroom CASE=n` does all of this and the driving too, and")
    print("cannot be asked a question in the first place. This entry point is")
    print("for the run you want to watch by hand.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
