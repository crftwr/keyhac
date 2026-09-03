"""Run a clean room end to end: build it, drive it, audit it, score it.

    python .claude/skills/keyhac-skill-cleanroom/scripts/run_room.py --case 1
    python .claude/skills/keyhac-skill-cleanroom/scripts/run_room.py --task "..."

or, the way it is meant to be typed, `make cleanroom CASE=1`.

**The room session is non-interactive on purpose.** The rule it kept breaking
was the operator's, not the room's: "answer nothing it asks" is a discipline,
and a discipline fails at the first plausible-sounding question. A `-p` session
has nobody to ask, so the rule holds by construction rather than by willpower -
the only questions it can raise are the ones it writes into QUESTIONS.md, which
is the artefact the exercise exists to produce.

What the operator still has to supply is the screen: the run drives whatever is
in front of it. For the cases with a fixture this script opens it first; for a
task of your own, put the screen up before you start.
"""

import argparse
import json
import os
import pathlib
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from open_room import ROOT, build_room, case_prompt  # noqa: E402
from score_room import score  # noqa: E402

#: The sentence the room is opened with, and the whole of what it is told.
#: Anything else said here is something the bundle then did not have to carry.
PROMPT = "Read RULES.md, then do TASK.md."

#: Fixtures that stand in for a real screen, by case number. Cases 6-10 are
#: about somebody's actual build output, deploy or Slack, and have none - for
#: those the operator puts the screen up and this script does not pretend to.
FIXTURES = {
    1: ["systema_1.html"],
    2: ["systema_1.html"],
    4: ["dialog.html"],
    5: ["submit.html"],
}

#: What the room may do. A non-interactive session is granted its permissions
#: up front or it is granted nothing: the first run of this script wrote no
#: action at all, because every tool call in a `-p` session with no grant is
#: denied and the room dutifully reported that it could not write a file.
#:
#: The list is the room's job description - read the bundle, write an action,
#: drive Keyhac, keep notes - and deliberately not `bypassPermissions`.
#:
#: There is no Bash here because `--restricted` removes it outright, and that
#: is the point rather than a limitation: a shell can `cat` its way out of the
#: working directories the file tools are confined to, so the confinement is
#: only real while there is no shell. What the room cannot be stopped from
#: doing by construction - reading another author's action in a folder it must
#: be able to write to - `audit()` catches afterwards.
ALLOW = ["Read", "Glob", "Grep", "Write", "Edit", "TodoWrite", "mcp__keyhac"]

EXTENSIONS = pathlib.Path.home() / ".keyhac" / "extensions"

MCP_IS_OFF = """\
Keyhac's MCP endpoint is not answering.

Tick **AI Integration > MCP Server** in the console or the tray menu. It is off
by default and turns itself off 60 minutes after being ticked, so this is
usually the switch rather than anything broken.

Checked before launching because the alternative is a room that writes an
action, cannot run it, and reports that as a finding about the skill."""


def find_claude() -> str:
    """The `claude` binary, which is routinely not on PATH.

    On this author's machine it lives inside the VS Code extension and PATH has
    never had it, so a script that just calls "claude" fails at the last step
    of a run that has already opened a room.
    """
    if override := os.environ.get("CLAUDE_BIN"):
        return override
    if found := shutil.which("claude"):
        return found
    candidates = list(pathlib.Path.home().glob(
        ".vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude"))
    local = pathlib.Path.home() / ".claude" / "local" / "claude"
    if local.exists():
        candidates.append(local)
    if not candidates:
        raise SystemExit(
            "no `claude` binary - put it on PATH or set CLAUDE_BIN=/path/to/claude")
    # Newest install wins; version strings sort wrong (2.1.99 > 2.1.251).
    return str(max(candidates, key=lambda p: p.stat().st_mtime))


def mcp_endpoint() -> dict:
    """Keyhac's live MCP endpoint, or a refusal naming the switch.

    `~/.keyhac/mcp.json` is written per launch and names the bridge, so reading
    the bridge path from it rather than hard-coding one follows the operator
    from a source checkout to the installed app without being told.
    """
    published = pathlib.Path.home() / ".keyhac" / "mcp.json"
    if not published.exists():
        raise SystemExit(MCP_IS_OFF)
    try:
        info = json.loads(published.read_text())
        os.kill(info["pid"], 0)
    except (ValueError, KeyError, OSError):
        raise SystemExit(MCP_IS_OFF)
    with socket.socket() as probe:
        probe.settimeout(1.0)
        if probe.connect_ex(("127.0.0.1", info["port"])) != 0:
            raise SystemExit(MCP_IS_OFF)
    return info


def open_fixture(case: int | None) -> None:
    """Put the screen the task talks about in front of the room.

    Served from a copy outside the checkout, because the address bar is part of
    the screen: opened from `examples/actions/fixtures/` the room is shown a
    `file:///…/projects/keyhac/…` URL, which tells it exactly where the
    repository it may not read is, and it never had to go looking. It also made
    the first real run look like a rule breach - the room quoted that URL into
    its notes as the screen it wrote against, honestly, and the audit called it
    reaching into the checkout.

    The copy is not put in the room either. A real target application does not
    hand over its own source, and a room that can read the fixture's HTML can
    skip the accessibility tree the skill is about.
    """
    names = FIXTURES.get(case or 0, [])
    if not names:
        return
    # The whole set: the pages link to each other, and pagination is the task.
    served = pathlib.Path(tempfile.mkdtemp(prefix="keyhac-cleanroom-screen-"))
    shutil.copytree(ROOT / "examples" / "actions" / "fixtures", served,
                    dirs_exist_ok=True)
    for name in names:
        if not (served / name).exists():
            print(f"  ! fixture {name} is missing; put the screen up yourself")
            continue
        subprocess.run(["open", "-a", "Safari", str(served / name)], check=False)
        print(f"  screen:  {name} (Safari, served from {served})")
    time.sleep(1.5)  # Safari needs a moment before the tree is readable.


def session_command(bridge: str, interactive: bool = False) -> list[str]:
    """The flags that make a room inside the checkout a clean room.

    `--restricted` is the load-bearing one and was measured, not assumed: a
    session started in a room under `cleanroom/` and asked what project it is
    in - reading nothing - answers UNKNOWN with it and names Keyhac2, its two
    languages and its launcher without it. It also hides the checkout's own
    skills (`keyhac-skill-cleanroom` among them, which describes this whole
    exercise and points at the scoring key) and removes Bash, which is how the
    file tools' confinement to the working directories stays true.

    That confinement is why the two `--add-dir`s are here: the room writes its
    action to ~/.keyhac/extensions and reads back what it produced on the
    Desktop, and neither is reachable otherwise. Reading *other* actions in
    that folder is still forbidden - a rule the audit checks, since a directory
    grant cannot express "this file but not that one".
    """
    return [
        find_claude(),
        "--restricted",
        "--add-dir", str(EXTENSIONS), "--add-dir", str(pathlib.Path.home() / "Desktop"),
        # Exactly one server, built from the running Keyhac. Whatever else the
        # operator has connected is not part of the product's public interface.
        "--mcp-config", json.dumps({"mcpServers": {"keyhac": {"command": bridge}}}),
        "--strict-mcp-config",
        # `--restricted` ignores the settings files, this one included, so the
        # room's permissions have to be handed to it directly.
        "--settings", json.dumps({"permissions": {"allow": ALLOW}}),
    ] + ([] if interactive else ["-p", PROMPT])


def printable_command(room: pathlib.Path, bridge: str) -> str:
    """The same session, for a person to paste - flags and all.

    Printing a bare `cd <room> && claude` is how a room in the checkout gets
    opened without `--restricted`, which is the failure this arrangement trades
    on not happening.
    """
    parts = [shlex.quote(part) for part in session_command(bridge, interactive=True)]
    lines, index = [], 0
    while index < len(parts):  # keep each flag on one line with its value
        flag, value = parts[index], parts[index + 1:index + 2]
        if flag.startswith("--") and value and not value[0].startswith("--"):
            lines.append(f"{flag} {value[0]}")
            index += 2
        else:
            lines.append(flag)
            index += 1
    return f"cd {shlex.quote(str(room))} && \\\n        " + " \\\n        ".join(lines)


def run_session(room: pathlib.Path, bridge: str, model: str | None,
                max_turns: int) -> int:
    """Drive the room, teeing the transcript to disk and a digest to stdout."""
    command = session_command(bridge) + [
        "--max-turns", str(max_turns),
        "--output-format", "stream-json", "--verbose",
    ]
    if model:
        command += ["--model", model]

    transcript = room / "transcript.jsonl"
    print(f"  running:  {room.name} (transcript -> {transcript.name})")
    print()
    with subprocess.Popen(command, cwd=room, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          bufsize=1) as session, transcript.open("w") as log:
        for line in session.stdout:
            log.write(line)
            _report(line)
        return session.wait()


def _report(line: str) -> None:
    """One line per thing the room did, so a long run is watchable."""
    try:
        event = json.loads(line)
    except ValueError:
        return
    if event.get("type") == "assistant":
        for part in event.get("message", {}).get("content", []):
            if part.get("type") == "tool_use":
                target = part.get("input", {})
                hint = (target.get("file_path") or target.get("pattern")
                        or target.get("app") or target.get("action")
                        or target.get("command") or "")
                print(f"    · {part['name']} {str(hint)[:60]}".rstrip())
            elif part.get("type") == "text" and part.get("text", "").strip():
                first = part["text"].strip().splitlines()[0]
                print(f"    {first[:100]}")
    elif event.get("type") == "result":
        print()
        print(f"  turns:   {event.get('num_turns', '?')}, "
              f"{event.get('duration_ms', 0) / 1000:.0f}s, "
              f"${event.get('total_cost_usd', 0):.2f}")


#: Input fields that say where a tool was aimed. Everything else a tool carries
#: - a file's contents, a note, a message - is what it said, not where it went.
AIMED_AT = ("file_path", "path", "notebook_path", "pattern", "command", "glob")


def _paths_in(tool_input: dict) -> list[str]:
    """The paths a tool call was pointed at, absolute ones only."""
    aimed = []
    for key in AIMED_AT:
        value = tool_input.get(key)
        if isinstance(value, str):
            aimed += re.findall(r"/[^\s\"']+", value)
    return aimed


def _isolation_held(transcript: pathlib.Path):
    """Check from the session's own first line that the flags did their job.

    A room inside the checkout is clean because of `--restricted`, and a
    guarantee that rests on a flag should be read back rather than believed.
    The init event says which tools and skills the session actually got, so all
    three of these are answerable from the record: no Bash means restricted
    mode took effect, no `keyhac` slash command means the checkout's own skills
    stayed hidden, and a connected server means the room could really drive
    Keyhac rather than quietly reporting that it could not.
    """
    first = transcript.read_text().splitlines()[:1]
    if not first:
        yield "the session produced no output at all"
        return
    try:
        init = json.loads(first[0])
    except ValueError:
        yield "the session's first line was not an init event"
        return
    if "Bash" in init.get("tools", []):
        yield ("the session had Bash: it was not started with --restricted, so "
               "the checkout's CLAUDE.md was in context from the first token")
    if leaked := [c for c in init.get("slash_commands", []) if "keyhac" in str(c)]:
        yield f"the checkout's own skills were visible to the room: {leaked}"
    servers = {s.get("name"): s.get("status") for s in init.get("mcp_servers", [])}
    if servers.get("keyhac") != "connected":
        yield (f"Keyhac's MCP server was {servers.get('keyhac', 'absent')}, so "
               f"whatever the room reported about running an action is about "
               f"the endpoint being off, not about the skill")


def audit(room: pathlib.Path, foreign: set[str]) -> list[str]:
    """Read the transcript for the rules the room was asked to keep.

    RULES.md forbids the checkout, the installed package and other people's
    actions, and until there was a transcript the only evidence it had been
    obeyed was that it said so. A rule you cannot check is a rule you are
    assuming, and this run's whole output is a claim about what a reader of the
    bundle alone would do.

    What is scanned is where a tool was *pointed* - the path-bearing fields of
    its input - and not the rest of what it typed. Scanning the whole input
    disqualified the first real run: the room had read the screen, Safari's
    address bar showed the fixture's `file:///…` URL because the runner opened
    it out of the checkout, and the room quoted that URL into its own notes as
    the screen it wrote against. Reading a path off the screen and repeating it
    is not reaching for the checkout; it is doing the task.

    Results are not scanned at all, for the same reason one step earlier: a
    window title or an editor's tab is full of paths the room never asked for.

    The room now lives *in* the checkout, so "mentions the checkout path" no
    longer means anything on its own: every legitimate read of the bundle says
    it too. What is checked is a path under the checkout and outside the room.
    """
    transcript = room / "transcript.jsonl"
    if not transcript.exists():
        return ["no transcript - the room was never driven from here"]

    breaches = list(_isolation_held(transcript))
    forbidden = {
        "site-packages/keyhac": "the installed package",
        "/Applications/Keyhac.app": "the installed app",
    }
    for line in transcript.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") != "assistant":
            continue
        for part in event.get("message", {}).get("content", []):
            if part.get("type") != "tool_use":
                continue
            for aimed in _paths_in(part.get("input", {})):
                for path, what in forbidden.items():
                    if path in aimed:
                        breaches.append(
                            f"{part['name']} reached into {what}: {aimed}")
                if aimed.startswith(str(ROOT)) and not aimed.startswith(str(room)):
                    breaches.append(
                        f"{part['name']} reached into the checkout: {aimed}")
                if pathlib.Path(aimed).name in foreign:
                    breaches.append(
                        f"{part['name']} read another author's action: {aimed}")
    return sorted(set(breaches))


def collect(room: pathlib.Path, before: set[pathlib.Path],
            keep: bool) -> list[pathlib.Path]:
    """Take the action out of ~/.keyhac/extensions and leave it in the room.

    `ActionsSource` imports every module in that folder, so a test action left
    behind shows up in the chooser and can break its listing. Cleaning up is
    the step an operator skips, so it happens here.
    """
    written = sorted(set(EXTENSIONS.glob("*.py")) - before)
    for action in written:
        shutil.copy2(action, room / action.name)
        if not keep:
            action.unlink()
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case", type=int,
                        help="an eval case number: its prompt is lifted from "
                             "cases.md, and its 'must' list is not")
    source.add_argument("--task", help="a prompt of your own, intent only")
    parser.add_argument("--room", help="where to build it")
    parser.add_argument("--model", help="model for the room session")
    parser.add_argument("--max-turns", type=int, default=150)
    parser.add_argument("--keep", action="store_true",
                        help="leave the action in ~/.keyhac/extensions")
    parser.add_argument("--dry-run", action="store_true",
                        help="build the room and stop, for a session you want "
                             "to drive by hand")
    args = parser.parse_args()

    task = case_prompt(args.case) if args.case else args.task
    print(f"task:    {task}")
    print()

    bridge = mcp_endpoint()["bridge"]
    room = build_room(task, args.room)
    print(f"  room:    {room}")
    if args.dry_run:
        print()
        print(f"    {printable_command(room, bridge)}")
        print()
        print("  and say only:  Read RULES.md, then do TASK.md.")
        print("  Every flag there is load-bearing: a plain `claude` in that")
        print("  directory reads the checkout's CLAUDE.md, and the room's own")
        print("  CLAUDE.md is there to tell such a session to stop.")
        return 0

    open_fixture(args.case)
    before = set(EXTENSIONS.glob("*.py")) if EXTENSIONS.exists() else set()
    exit_code = run_session(room, bridge, args.model, args.max_turns)
    if exit_code != 0:
        print(f"  ! the room session exited {exit_code}")

    written = collect(room, before, args.keep)
    print()
    for action in written:
        print(f"  action:  {action.name} -> {room / action.name}")

    print()
    print("== rules ==")
    if breaches := audit(room, {p.name for p in before}):
        for breach in breaches:
            print(f"  DISQUALIFIED: {breach}")
        print("  What came out measured a session that had the answers.")
    else:
        print("  clean: nothing in the transcript reached outside the room")
    print()
    return score(room)


if __name__ == "__main__":
    raise SystemExit(main())
