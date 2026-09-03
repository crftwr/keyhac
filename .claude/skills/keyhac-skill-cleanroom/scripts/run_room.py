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
import functools
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
import threading
import time
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

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
    2: ["systema_1.html", "systemb_1.html"],
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

#: Fixture files a task refers to but a screen cannot show. Case 5 opens
#: "Submit each row of this CSV", and the CSV lives with the fixtures - which
#: are served from a temp directory the room cannot read, so "this CSV" points
#: at nothing, the way case 2's "same as above" did. Staged on the Desktop
#: instead, which the room can reach and which is where an operator's input
#: file would actually be.
STAGED = {5: ["to_submit.csv"]}

DESKTOP = pathlib.Path.home() / "Desktop"
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
    for name in STAGED.get(case or 0, []):
        shutil.copy2(ROOT / "examples" / "actions" / "fixtures" / name, DESKTOP / name)
        print(f"  staged:  ~/Desktop/{name}")

    names = FIXTURES.get(case or 0, [])
    if not names:
        return
    # The whole set: the pages link to each other, and pagination is the task.
    served = pathlib.Path(tempfile.mkdtemp(prefix="keyhac-cleanroom-screen-"))
    shutil.copytree(ROOT / "examples" / "actions" / "fixtures", served,
                    dirs_exist_ok=True)
    origin = _serve(served)
    for name in names:
        if not (served / name).exists():
            raise SystemExit(f"fixture {name} is missing from {served}")
        _load(f"{origin}/{name}", _title_of(served / name), name)
        print(f"  screen:  {name} (Safari, {origin}/{name})")
    time.sleep(1.0)  # Safari needs a moment before the tree is readable.


def _title_of(page: pathlib.Path) -> str:
    """The window name the fixture will take once it has loaded."""
    found = re.search(r"<title>([^<]*)", page.read_text())
    return found.group(1).strip() if found else page.name


def _safari_windows() -> str:
    """Safari's window names, as one string to look for a title in."""
    asked = subprocess.run(
        ["osascript", "-e",
         'tell application "Safari" to get name of every window'],
        capture_output=True, text=True, check=False)
    return asked.stdout


def _load(url: str, title: str, name: str,
          attempts: int = 3, patience: float = 8.0) -> None:
    """Put `url` in a Safari window of its own, and prove that it is there.

    Every part of this was paid for by a run. `make new document with
    properties {URL:...}` returns a document and does not load it. A plain
    two-step sets the URL before the new document is ready. And a `file:///`
    under the temp directory is refused outright - "outside the sandbox" -
    which is why the fixtures are served over a local HTTP origin instead,
    the shape a real target application has anyway.

    Case 4 ran without any of this: Safari sat on its Start Page, the room
    hunted for its task across every application the operator had open -
    reading Gmail and Claude on the way - and wrote an action it could never
    exercise. So the load is waited for by the title the page will take, and a
    screen that never arrives refuses the run instead of buying it.
    """
    for _ in range(attempts):
        subprocess.run(["osascript", "-e", f'''tell application "Safari"
            activate
            set fixture to make new document
            delay 0.5
            set URL of fixture to "{url}"
        end tell'''], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + patience
        while time.monotonic() < deadline:
            if title in _safari_windows():
                return
            time.sleep(0.5)
        print(f"  ! {name} did not load as \"{title}\" - retrying")
    raise SystemExit(
        f"{name} never appeared in Safari as \"{title}\".\n\n"
        f"Safari has: {_safari_windows().strip() or '(no windows)'}\n\n"
        f"A modal sheet left over a window blocks this and shows up nowhere "
        f"else. Clear Safari and run again - refusing rather than opening a "
        f"room that would spend its run looking for a screen that is not "
        f"there, which is what case 4 did.")


def _serve(directory: pathlib.Path) -> str:
    """Serve the fixtures over HTTP, and hand back their origin.

    Not `file://`: Safari refuses one under the temp directory outright, and
    the copy is in the temp directory precisely so that the address bar does
    not read out the checkout's path to the room. An HTTP origin says nothing
    but a port number, and it is what the applications these actions really
    drive look like.

    The server is a daemon thread, so it lives exactly as long as the run.
    """
    quiet = type("Quiet", (SimpleHTTPRequestHandler,),
                 {"log_message": lambda *args, **kwargs: None})
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(quiet, directory=str(directory)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}"


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

    # Written outside the room and moved in at the end. Left in the room it is
    # a file the room can read, and the first case-2 run did exactly that -
    # grepping its own log for `"type":"text"` to reconstruct the task its
    # prompt referred to. The harness's own record is not part of the room.
    transcript = pathlib.Path(tempfile.gettempdir()) / f"cleanroom-{room.name}.jsonl"
    print(f"  running:  {room.name} (transcript -> {transcript})")
    print()
    global _started, _last_event
    _started = _last_event = time.monotonic()
    with subprocess.Popen(command, cwd=room, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          bufsize=1) as session, transcript.open("w") as log:
        heartbeat = threading.Thread(target=_heartbeat, args=(session,), daemon=True)
        heartbeat.start()
        for line in session.stdout:
            log.write(line)
            _last_event = time.monotonic()
            _report(line, room)
        code = session.wait()
    shutil.move(transcript, room / "transcript.jsonl")
    return code


#: When the run started, and when it last said anything - the two numbers the
#: heartbeat needs and the digest prints.
_started = _last_event = 0.0

#: Set when the heartbeat finds Keyhac gone. A run that loses the endpoint is
#: inconclusive rather than clean: it measured an outage.
_endpoint_died = False


def _clock() -> str:
    seconds = int(time.monotonic() - _started)
    return f"{seconds // 60:>2}:{seconds % 60:02d}"


def _endpoint_alive() -> bool:
    """Whether Keyhac is still answering, without raising about it."""
    try:
        mcp_endpoint()
        return True
    except SystemExit:
        return False


def _heartbeat(session, every: float = 20.0) -> None:
    """Say the run is alive while it is thinking, and stop it when Keyhac is not.

    A model between tool calls writes nothing at all, so a long turn and a hung
    process look identical - which is the question an operator watching this
    actually has. A line every 20 quiet seconds answers it, and says how quiet
    it has been so a real hang is still visible as a number that keeps growing.

    The endpoint is checked on the same beat because case 5 spent twenty-three
    minutes and three dollars after Keyhac had already died - it segfaulted
    three minutes in, and the room went on writing an action it could no longer
    run, reporting the outage as its finding. Two failed checks in a row rather
    than one, so a blip does not end a good run; a room that cannot reach
    Keyhac cannot measure the skill, and stopping is cheaper than finishing.
    """
    missed = 0
    while session.poll() is None:
        time.sleep(every / 4)
        quiet = time.monotonic() - _last_event
        if quiet >= every:
            print(f"  [{_clock()}] … thinking ({int(quiet)}s since the last step)")
            globals()["_last_event"] = time.monotonic()
            missed = 0 if _endpoint_alive() else missed + 1
            if missed >= 2:
                print(f"  [{_clock()}] !! Keyhac stopped answering - ending the "
                      f"run, which cannot measure anything from here")
                globals()["_endpoint_died"] = True
                session.terminate()
                return


def _hint(tool: str, given: dict, room: pathlib.Path) -> str:
    """The part of a tool call worth one line.

    Paths get their tail, not their head. Six consecutive reads inside the room
    printed as an identical 60-character prefix of the room's own path, which
    is six lines saying nothing - the operator watching could not tell them
    apart or tell whether anything was moving.
    """
    for key in ("file_path", "path", "notebook_path"):
        if value := given.get(key):
            shown = str(value)
            return shown[len(str(room)) + 1:] if shown.startswith(str(room)) else shown
    for key in ("pattern", "command", "app", "action", "title", "role", "text"):
        if value := given.get(key):
            return str(value)[:70]
    return ""


def _report(line: str, room: pathlib.Path = pathlib.Path()) -> None:
    """One line per thing the room did, so a long run is watchable."""
    try:
        event = json.loads(line)
    except ValueError:
        return
    if event.get("type") == "assistant":
        for part in event.get("message", {}).get("content", []):
            if part.get("type") == "tool_use":
                print(f"  [{_clock()}] · {part['name']} "
                      f"{_hint(part['name'], part.get('input', {}), room)}".rstrip())
            elif part.get("type") == "text" and part.get("text", "").strip():
                first = part["text"].strip().splitlines()[0]
                print(f"  [{_clock()}]   {first[:110]}")
    elif event.get("type") == "user":
        # A refusal is the one tool *result* worth a line: it is what a wrong
        # permission list or a reach outside the room looks like from here, and
        # three went past unseen in the first case-2 run. Keyed on `is_error`
        # rather than on the wording - the real one reads "… is outside …;
        # --restricted confines the file tools to the working directories",
        # which matches none of the words you would think to grep for.
        for part in event.get("message", {}).get("content", []):
            if part.get("type") == "tool_result" and part.get("is_error"):
                said = str(part.get("content", "")).replace("\n", " ")
                print(f"  [{_clock()}] ! refused - {said[:120]}")
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


def audit(room: pathlib.Path, foreign: set[str]) -> tuple[list[str], list[str]]:
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

    **A refusal is not a breach.** The first case-2 run was disqualified for a
    `Glob` of a sibling room that the confinement turned down flat, so nothing
    was read and nothing was learned - the measurement was intact and the
    verdict said otherwise. Reaching and being refused is reported, because it
    is the confinement doing its job in public, but only a call that came back
    with something disqualifies a run.

    Returns:
        What disqualifies the run, and what was refused.
    """
    room = room.resolve()  # every comparison below is against an absolute path
    transcript = room / "transcript.jsonl"
    if not transcript.exists():
        return ["no transcript - the room was never driven from here"], []

    refused_ids = {part["tool_use_id"]
                   for event in _events(transcript) if event.get("type") == "user"
                   for part in event.get("message", {}).get("content", [])
                   if part.get("type") == "tool_result" and part.get("is_error")}
    breaches = list(_isolation_held(transcript))
    refused = []
    forbidden = {
        "site-packages/keyhac": "the installed package",
        "/Applications/Keyhac.app": "the installed app",
    }
    for event in _events(transcript):
        if event.get("type") != "assistant":
            continue
        for part in event.get("message", {}).get("content", []):
            if part.get("type") != "tool_use":
                continue
            landed = breaches if part.get("id") not in refused_ids else refused
            for aimed in _paths_in(part.get("input", {})):
                for path, what in forbidden.items():
                    if path in aimed:
                        landed.append(f"{part['name']} reached into {what}: {aimed}")
                if aimed.startswith(str(ROOT)) and not aimed.startswith(str(room)):
                    landed.append(f"{part['name']} reached into the checkout: {aimed}")
                if pathlib.Path(aimed).name in foreign:
                    landed.append(
                        f"{part['name']} read another author's action: {aimed}")
    return sorted(set(breaches)), sorted(set(refused))


def _events(transcript: pathlib.Path):
    """Every event in the transcript that parses."""
    for line in transcript.read_text().splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def sweep_desktop(room: pathlib.Path, before: set[pathlib.Path]) -> list[str]:
    """Take what this run left on the Desktop into the room.

    The Desktop is a granted directory, so every room can read what every
    previous room wrote there: case 5 opened `records.csv` and `out.csv`, which
    are cases 2 and 1's finished output, on its way to its own task. Rooms
    leaking to each other is the one contamination this whole arrangement is
    built to prevent, and it arrived through the back door of the output
    folder.

    Moved rather than deleted, and named in the log: what a run produced is its
    evidence, and a file this misjudges is recoverable from the room.
    """
    moved = []
    for produced in sorted(set(DESKTOP.iterdir()) - before):
        if produced.is_file() and not produced.name.startswith("."):
            shutil.move(produced, room / produced.name)
            moved.append(produced.name)
    return moved


def collect(room: pathlib.Path, before: set[pathlib.Path],
            keep: bool, case: int | None = None) -> list[pathlib.Path]:
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
    for name in STAGED.get(case or 0, []):
        if (staged := DESKTOP / name).exists():
            # What the room wrote into it is the run's output, so it comes back
            # as evidence rather than being left on the operator's Desktop.
            shutil.move(staged, room / name)
    return written


def main() -> int:
    # Line buffering, because the progress display is the point of it. Piped to
    # a file - a log, a CI job, anything not a terminal - Python block-buffers
    # stdout, and the first thing that disappears is the heartbeat that exists
    # to say the run is alive. A watched run showed nothing for four minutes
    # while its transcript grew to 224KB.
    sys.stdout.reconfigure(line_buffering=True)

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

    desktop_before = set(DESKTOP.iterdir()) if DESKTOP.exists() else set()
    open_fixture(args.case)
    before = set(EXTENSIONS.glob("*.py")) if EXTENSIONS.exists() else set()
    exit_code = run_session(room, bridge, args.model, args.max_turns)
    if exit_code != 0:
        print(f"  ! the room session exited {exit_code}")

    written = collect(room, before, args.keep, args.case)
    print()
    for action in written:
        print(f"  action:  {action.name} -> {room / action.name}")
    for produced in sweep_desktop(room, desktop_before):
        print(f"  output:  ~/Desktop/{produced} -> {room / produced}")

    print()
    if _endpoint_died or _lost_the_endpoint(room):
        print("== INCONCLUSIVE ==")
        print("  Keyhac stopped answering during the run, so what the room")
        print("  reported past that point is about the outage. Restart Keyhac,")
        print("  tick AI Integration > MCP Server, and run this case again.")
        print()

    print("== rules ==")
    breaches, refused = audit(room, {p.name for p in before})
    for turned_down in refused:
        print(f"  refused (the confinement held): {turned_down}")
    if breaches:
        for breach in breaches:
            print(f"  DISQUALIFIED: {breach}")
        print("  What came out measured a session that had the answers.")
    else:
        print("  clean: nothing outside the room was read")
    print()
    return score(room) or (2 if _endpoint_died or _lost_the_endpoint(room) else 0)


def _lost_the_endpoint(room: pathlib.Path) -> bool:
    """Whether the transcript shows Keyhac going away mid-run."""
    transcript = room / "transcript.jsonl"
    if not transcript.exists():
        return False
    return any("could not reach Keyhac" in str(part.get("content", ""))
               for event in _events(transcript) if event.get("type") == "user"
               for part in event.get("message", {}).get("content", [])
               if part.get("type") == "tool_result" and part.get("is_error"))


if __name__ == "__main__":
    raise SystemExit(main())
