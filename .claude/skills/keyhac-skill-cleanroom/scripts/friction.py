"""Where a room stumbled, read off its transcript - including what it never wrote down.

    python .claude/skills/keyhac-skill-cleanroom/scripts/friction.py <room>

QUESTIONS.md is what the room *noticed* it had to guess. It is not everything
that went wrong: a tool call that failed and was quietly retried, a search of
the skill that found nothing, a config written three times before it loaded,
an answer from Keyhac that said "unknown" and was worked around - none of those
is a question the room thought to ask, and each is a place where the skill, or
Keyhac, cost it something. The case-11 to 13 runs of 2026-10-17 are the
example: every one of them met `describe_keymap` answering "focus now:
unknown", and only one mentioned it as more than an aside.

So this reads the record, not the room's account of it, and lists every such
moment as an incident with the line that shows it. The runner writes them to
the room's FRICTION.md and into the run's FINDINGS.md beside the questions;
triage both the same way (SKILL.md, Score it). An incident with no matching
question is the more valuable kind - it is the gap nobody noticed.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import outcome  # noqa: E402


@dataclasses.dataclass
class Incident:
    kind: str           # one of KINDS
    step: int           # the tool call it is about, counting from 1 (0: none)
    detail: str         # what happened, in one line
    evidence: str = ""  # the line of the record that shows it

    def line(self) -> str:
        where = f"step {self.step}: " if self.step else ""
        shown = f"\n    > {self.evidence}" if self.evidence else ""
        return f"- **{self.kind}** - {where}{self.detail}{shown}"


#: What each kind means, for the reader of FRICTION.md.
KINDS = {
    "tool error": "a tool call failed or was refused",
    "keyhac trouble": "Keyhac answered, and the answer was a failure or a blank",
    "searched, found nothing": "the room looked in the skill for something that is not there",
    "rework": "the same file written, or the same action run, more than once",
    "session": "the session itself did not end cleanly",
    "said so": "the room said, as it worked, that it was unsure or had it wrong",
}

#: In an answer from Keyhac: it went wrong, or there was nothing to go on.
#: Matched per line, so one ERROR among a reload's DEBUG lines is found and
#: the DEBUG lines are not.
TROUBLE = re.compile(
    r"Traceback|^ERROR\b|\bERROR \[|\bfailed\b|\braised\b|StaleElement|WaitTimeout|"
    r"AppNotResponding|timed out|\bnot found\b|no (such )?element|nothing focused|"
    r"focus now: unknown|could not reach Keyhac", re.IGNORECASE | re.MULTILINE)

#: The file tools' own "nothing here" answers.
NOTHING_FOUND = re.compile(r"^(No matches found|No files found)", re.IGNORECASE)

#: In the room's own words, while it works. Deliberately the phrases of
#: doubt and correction, not of caution: "not verified" is the skill's own
#: honest-reporting rule being followed, not a stumble.
UNSURE = re.compile(
    r"not documented|undocumented|doesn't say|does not say|isn't (stated|said|clear)|"
    r"unclear|not sure|no way to|can't tell|cannot tell|couldn't find|could not find|"
    r"\bguess|assum(e|ing)\b|didn't work|did not work|that failed|wrong|mistake|"
    r"let me (try|retry) again|try again|instead\b", re.IGNORECASE)

#: Keyhac tools whose answer is *content* - a file, the screen's text - rather
#: than a report. Their content can say "failed" or "not found" about anything
#: (the fixture config's own comments do), so only the first line, where an
#: error or an empty answer would be, is judged.
CONTENT = {"mcp__keyhac__read_config", "mcp__keyhac__read_extension",
           "mcp__keyhac__read_text", "mcp__keyhac__describe_screen",
           "mcp__keyhac__find_elements", "mcp__keyhac__list_windows",
           "mcp__keyhac__list_extensions", "mcp__keyhac__list_actions"}

#: Tools that write the operator-side files an action or a key binding is.
WRITES = {"mcp__keyhac__write_config": "config.py",
          "mcp__keyhac__write_extension": None}


#: Where a sentence ends: a stop followed by space, or a line break. Not a
#: bare ".", which is inside every file name the room mentions.
_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n")


def _sentence(text: str, match: re.Match, width: int = 220) -> str:
    """The sentence around a match, on one line."""
    starts = [m.end() for m in _BOUNDARY.finditer(text, 0, match.start())]
    start = starts[-1] if starts else 0
    after = _BOUNDARY.search(text, match.end())
    end = after.start() if after else len(text)
    sentence = " ".join(text[start:end].split())
    return sentence if len(sentence) <= width else sentence[:width - 1] + "…"


def _first_line(text: str, pattern: re.Pattern | None = None) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if pattern is not None:
        lines = [line for line in lines if pattern.search(line)] or lines
    return (lines[0] if lines else "")[:220]


def _written(call: outcome.ToolCall) -> str | None:
    """The operator-side file a call wrote, if it wrote one."""
    if call.name in WRITES:
        return WRITES[call.name] or f"extensions/{call.input.get('name', '?')}.py"
    if call.name in ("Write", "Edit"):
        path = str(call.input.get("file_path", "")).replace("\\", "/")
        if "/.keyhac/extensions/" in path:
            return "extensions/" + path.rsplit("/", 1)[-1]
    return None


def incidents(transcript: pathlib.Path) -> list[Incident]:
    """Every stumble in one room's transcript, in the order they happened."""
    if not transcript.exists():
        return [Incident("session", 0, "no transcript - the room never ran")]
    calls = outcome.tool_calls(transcript)
    found: list[Incident] = []
    writes: dict[str, list[int]] = {}
    runs: dict[str, list[int]] = {}

    for step, call in enumerate(calls, 1):
        if call.is_error:
            found.append(Incident("tool error", step, f"{call.name} failed",
                                  _first_line(call.result)))
        elif call.keyhac and TROUBLE.search(
                call.result.strip().split("\n", 1)[0] if call.name in CONTENT
                else call.result):
            found.append(Incident("keyhac trouble", step,
                                  f"{call.name.removeprefix('mcp__keyhac__')} answered",
                                  _first_line(call.result, TROUBLE)))
        elif call.name in ("Grep", "Glob") and NOTHING_FOUND.match(call.result.strip()):
            looked = call.input.get("pattern", "")
            found.append(Incident("searched, found nothing", step,
                                  f"{call.name} {looked!r} came back empty"))
        if target := _written(call):
            writes.setdefault(target, []).append(step)
        if call.name == "mcp__keyhac__start_action":
            runs.setdefault(str(call.input.get("name", "?")), []).append(step)

    for target, steps in writes.items():
        if len(steps) > 1:
            found.append(Incident("rework", steps[-1],
                                  f"{target} written {len(steps)} times "
                                  f"(steps {', '.join(map(str, steps))})"))
    for action, steps in runs.items():
        if len(steps) > 1:
            found.append(Incident("rework", steps[-1],
                                  f"{action} started {len(steps)} times "
                                  f"(steps {', '.join(map(str, steps))})"))

    events = list(outcome.transcript_events(transcript))
    # The last thing the room says is its reply, read on its own below - here
    # it would be counted twice.
    final = next((e.get("result", "") or "" for e in reversed(events)
                  if e.get("type") == "result"), "")
    step = 0
    for event in events:
        if event.get("type") == "assistant":
            for part in event.get("message", {}).get("content", []) or []:
                if part.get("type") == "tool_use":
                    step += 1
                elif part.get("type") == "text":
                    text = part.get("text", "")
                    if final and text.strip() == final.strip():
                        continue
                    if match := UNSURE.search(text):
                        found.append(Incident("said so", step, "while working",
                                              _sentence(text, match)))
        elif event.get("type") == "result":
            if event.get("subtype") not in (None, "success") or event.get("is_error"):
                found.append(Incident("session", step,
                                      f"ended as {event.get('subtype')!r}"
                                      + (" - it ran out of turns"
                                         if "max_turns" in str(event.get("subtype")) else "")))
            reply = event.get("result", "") or ""
            for match in re.finditer(r"mistake|by accident|I overwrote|wrongly", reply,
                                     re.IGNORECASE):
                found.append(Incident("said so", step, "in its final reply",
                                      _sentence(reply, match)))

    # Two doubtful words in one sentence are one incident.
    unique = {(i.kind, i.step, i.detail, i.evidence): i for i in found}
    return sorted(unique.values(), key=lambda incident: incident.step)


def report(room: pathlib.Path, failures: list[str] | None = None) -> str:
    """FRICTION.md for one room: the outcome check's failures, then the
    incidents, grouped by kind."""
    found = incidents(room / "transcript.jsonl")
    lines = [f"# Where {room.name} stumbled", "",
             "Read off the transcript by scripts/friction.py, after the run. "
             "QUESTIONS.md is what the room noticed; this is what the record "
             "shows, noticed or not. Triage both the same way: skill gap, out "
             "of scope, or tooling.", ""]
    if failures:
        lines += ["## The outcome check failed", ""] + [f"- {f}" for f in failures] + [""]
    if not found:
        lines += ["Nothing: no failed call, no blank answer, no search that came "
                  "back empty, no rework, no doubt said out loud.", ""]
    for kind, meaning in KINDS.items():
        mine = [incident for incident in found if incident.kind == kind]
        if mine:
            lines += [f"## {kind} ({len(mine)}) - {meaning}", ""]
            lines += [incident.line() for incident in mine] + [""]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit(__doc__)
    print(report(pathlib.Path(argv[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
