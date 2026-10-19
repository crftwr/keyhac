"""Keyhac's evidence tools, for the operator: what a room's run actually did.

    python qa/evidence.py replay   <room> --app Code --keys F1,Fn-F1 [--title T] [--before] [--platform mac|windows]
    python qa/evidence.py bindings <room> [--platform P]
    python qa/evidence.py preserved <room>
    python qa/evidence.py calls    <room> [--tool reload_config]
    python qa/evidence.py writes   <room>
    python qa/evidence.py events   <room>
    python qa/evidence.py records  <room> <file> --expect systema[,systemb]
    python qa/evidence.py expected systema|systemb|to_submit|settings

Every answer is JSON. Each one reads; nothing here changes a file. The
operator runs them through quality-loop's ``evidence`` tool, with no shell.

**replay** is the one that matters most: the room's config.py is loaded by
Keyhac's own Keymap on the fake platform the engine tests use, the focus is
moved to the application named, and the keys are *pressed* through the fake
hook. What it answers is what the application would have received - "Cmd-S",
or the key itself when Keyhac let it through, or nothing - so "F1 does
nothing in VS Code" is checked by pressing F1 with VS Code focused.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import configs  # noqa: E402

#: Loads a config.py in Keyhac's own Keymap on the fake platform and presses
#: keys through the fake hook. In a subprocess: a config runs arbitrary code
#: at import, puts its extensions/ on sys.path and initialises the key-name
#: tables for one platform, and none of that may leak into the caller.
_REPLAY = r"""
import json, logging, pathlib, re, sys
root, home, platform, probes = sys.argv[1], sys.argv[2], sys.argv[3], json.loads(sys.argv[4])
sys.path.insert(0, root)
from keyhac.core.clipboard_history import ClipboardHistory
from keyhac.core.keymap import Keymap, _condition_text, _key_text
from keyhac.core.vk import get_key_names
from keyhac.platform.base import Focus
from keyhac.platform.fake import FakeInputHook, FakeFocusProvider

errors = []
class Catch(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.ERROR:
            errors.append(record.getMessage())
logging.getLogger("keyhac").addHandler(Catch())

class Clipboard:
    def get_text(self): return ""
    def set_text(self, text): pass

hook, focus = FakeInputHook("ansi"), FakeFocusProvider()
keymap = Keymap(hook, focus, platform, config_path=str(pathlib.Path(home) / "config.py"))
keymap._clipboard_history = ClipboardHistory(Clipboard(), str(pathlib.Path(home) / "clipboard.json"))
keymap.configure()
hook.install(keymap.on_key_event, keymap.on_hook_restored, keymap.on_mouse_event)

def describe(action):
    if isinstance(action, (str, tuple, list)) or action is None:
        return repr(action)
    kind = action if callable(action) and hasattr(action, "__qualname__") else type(action)
    return f"{getattr(kind, '__module__', '?')}.{getattr(kind, '__qualname__', kind)}"

bindings = []
conditioned = {id(table): condition for condition, table in keymap._keytable_list}
for table in keymap._all_keytables:
    condition = conditioned.get(id(table))
    where = _condition_text(condition) if condition is not None else f"multi-stroke {table.name}"
    for key, action in table.table.items():
        bindings.append([where, _key_text(key), describe(action)])

MODIFIERS = {"ctrl": "LCtrl", "shift": "LShift", "alt": "LAlt", "opt": "LAlt",
             "cmd": "LCmd", "win": "LWin", "fn": "Fn"}
ORDER = ["Ctrl", "Alt", "Shift", "Cmd", "Win", "Fn"]
names = get_key_names()

def plain(name):
    return re.sub(r"^[LR](?=[A-Z])", "", name)

def spelled(mods, key):
    held = [m for m in ORDER if m in {plain(x) for x in mods}]
    return "-".join(held + [key])

def press(text):
    # Down with its modifiers held around it, then up. What the application
    # receives: the key itself when Keyhac let it through, otherwise every
    # key-down Keyhac sent, with the modifiers down at that moment - the ones
    # held physically, as Keyhac's own presses and releases left them.
    *mods, key = text.split("-")
    held = [MODIFIERS[m.lower()] for m in mods]
    for name in held:
        hook.key(names.str_to_vk(name), True)
    start = len(hook.sent)
    consumed = hook.key(names.str_to_vk(key), True)
    hook.key(names.str_to_vk(key), False)
    sent = hook.sent[start:]
    for name in reversed(held):
        hook.key(names.str_to_vk(name), False)
    edges = [("D-" if down else "U-") + names.vk_to_str(vk) for vk, down, _r in sent]
    if not consumed:
        return {"key": text, "consumed": False, "received": [spelled(held, key)], "sent": edges}
    down, received = set(held), []
    modifier_names = {plain(m) for m in MODIFIERS.values()} | {"Ctrl", "Alt", "Shift", "Cmd", "Win"}
    for edge in edges:
        name = edge[2:]
        if plain(name) in modifier_names:
            (down.add if edge.startswith("D-") else down.discard)(name)
        elif edge.startswith("D-"):
            received.append(spelled(down, name))
    return {"key": text, "consumed": True, "received": received, "sent": edges}

results = []
for number, probe in enumerate(probes):
    focus.focus = Focus(app_name=probe["app"], pid=100 + number,
                        window_title=probe.get("title", "Untitled"),
                        path=f"/{probe['app']}/{probe.get('title', 'Untitled')}")
    hook.sent.clear()
    results.append({"app": probe["app"], "presses": [press(k) for k in probe["keys"]]})
print(json.dumps({"errors": errors, "bindings": bindings, "probes": results}))
"""


def this_platform() -> str:
    return "mac" if sys.platform == "darwin" else "windows"


def replay(config: str, platform: str, probes: list[dict],
           extensions: dict[str, str] | None = None) -> dict:
    """Load ``config`` as Keyhac would on ``platform`` and press ``probes``."""
    with tempfile.TemporaryDirectory(prefix="keyhac-qa-replay-") as home:
        home_path = pathlib.Path(home)
        (home_path / "config.py").write_text(config, encoding="utf-8")
        (home_path / "extensions").mkdir()
        for name, source in (extensions or {}).items():
            (home_path / "extensions" / name).write_text(source, encoding="utf-8")
        # Must not reach the real ~/.keyhac: Keymap resolves extensions/ beside
        # config_path, and HOME/USERPROFILE point here in case anything asks.
        env = dict(os.environ, HOME=home, USERPROFILE=home, PYTHONIOENCODING="utf-8")
        done = subprocess.run(
            [sys.executable, "-c", _REPLAY, str(ROOT), home, platform, json.dumps(probes)],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            stdin=subprocess.DEVNULL, env=env, cwd=home)
    if done.returncode != 0 or not done.stdout.strip():
        return {"errors": [f"the replay itself failed: {done.stderr.strip()[-400:]}"],
                "bindings": [], "probes": []}
    return json.loads(done.stdout.strip().splitlines()[-1])


# --- reading a room ------------------------------------------------------------

def _evidence(room: str) -> pathlib.Path:
    path = pathlib.Path(room) / "evidence"
    if not path.is_dir():
        raise SystemExit(f"{room} has no evidence/ - is it a room the runner finished?")
    return path


def _text(path: pathlib.Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None


def _extensions(evidence: pathlib.Path) -> dict[str, str]:
    directory = evidence / "extensions"
    return {p.name: p.read_text(encoding="utf-8") for p in directory.glob("*.py")} \
        if directory.is_dir() else {}


def _configs(evidence: pathlib.Path) -> tuple[str, str | None]:
    before = _text(evidence / "before" / "config.py")
    if before is None:
        raise SystemExit("evidence/before/config.py is missing - this room stood no config in")
    return before, _text(evidence / "after" / "config.py")


def cmd_replay(args) -> dict:
    evidence = _evidence(args.room)
    before, after = _configs(evidence)
    config = before if args.before else after
    if config is None:
        return {"error": "config.py was gone after the run"}
    probe = {"app": args.app, "keys": [k.strip() for k in args.keys.split(",") if k.strip()]}
    if args.title:
        probe["title"] = args.title
    loaded = replay(config, args.platform, [probe], _extensions(evidence))
    return {"config": "before" if args.before else "after", "platform": args.platform,
            "load_errors": loaded["errors"], "app": args.app,
            "presses": loaded["probes"][0]["presses"] if loaded["probes"] else []}


def cmd_bindings(args) -> dict:
    evidence = _evidence(args.room)
    before, after = _configs(evidence)
    old = replay(before, args.platform, [])
    new = replay(after or "", args.platform, [], _extensions(evidence))
    was = {(w, k): a for w, k, a in old["bindings"]}
    now = {(w, k): a for w, k, a in new["bindings"]}
    return {
        "platform": args.platform,
        "load_errors_after": new["errors"],
        "added": [{"table": w, "key": k, "action": a} for (w, k), a in now.items()
                  if (w, k) not in was],
        "changed_or_lost": [{"table": w, "key": k, "was": a, "now": now.get((w, k))}
                            for (w, k), a in was.items() if now.get((w, k)) != a],
    }


def cmd_preserved(args) -> dict:
    evidence = _evidence(args.room)
    before, after = _configs(evidence)
    if after is None:
        return {"config_after": "gone"}
    after_lines = set(after.splitlines())
    lines = before.splitlines()
    return {
        "changed": after != before,
        "keep_lines_lost": [l.strip() for l in lines if configs.KEEP in l and l not in after_lines],
        "original_lines": len(lines),
        "original_lines_still_there": sum(1 for l in lines if l in after_lines),
        "lines_after": len(after.splitlines()),
    }


def cmd_rules(args) -> dict:
    """The skill's mechanical rules (qa/action_rules.py) on every action the
    room left: what it installed in extensions/, and the copies in the room."""
    import action_rules
    room = pathlib.Path(args.room)
    found = {}
    for path in sorted([*(_evidence(args.room) / "extensions").glob("*.py"),
                        *room.glob("*.py")]):
        found[str(path.relative_to(room))] = action_rules.check(path)
    return {"actions": found,
            "note": "none found" if not found else
                    "an empty list is a file that broke none of the rules"}


def cmd_calls(args) -> dict:
    from quality_loop import transcript
    found = []
    for call in transcript.tool_calls(pathlib.Path(args.room) / "transcript.jsonl"):
        if not call.name.startswith("mcp__keyhac__"):
            continue
        short = call.name.removeprefix("mcp__keyhac__")
        if args.tool and short != args.tool:
            continue
        found.append({"tool": short, "input": {k: (v if len(str(v)) < 300 else
                                                   str(v)[:300] + "...")
                                               for k, v in call.input.items()},
                      "is_error": call.is_error, "result": call.result[:1500]})
    return {"calls": found}


#: The room's own file tools - a file one of these wrote was not an action's output.
FILE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


def cmd_writes(args) -> dict:
    """Files the room wrote with its own file tools rather than through an
    action. `records` reads the Desktop as the run left it, and a room with
    Write can put a correct CSV there by hand."""
    from quality_loop import transcript
    room = pathlib.Path(args.room).resolve()
    desktop, elsewhere = [], []
    for call in transcript.tool_calls(room / "transcript.jsonl"):
        if call.name not in FILE_TOOLS or call.is_error:
            continue
        given = str(call.input.get("file_path") or call.input.get("notebook_path") or "")
        # Either separator: a Windows room's paths reach a Mac reading its evidence.
        segments = given.replace("\\", "/").split("/")
        if len(segments) >= 2 and segments[-2] == "Desktop":
            desktop.append({"file": segments[-1], "tool": call.name})
        elif not pathlib.Path(given).resolve().is_relative_to(room):
            elsewhere.append({"path": given, "tool": call.name})
    return {"desktop": desktop, "outside_the_room": elsewhere,
            "note": "a Desktop file here was written by the room, not by its action"
                    if desktop else "the room wrote nothing on the Desktop itself"}


def cmd_events(args) -> dict:
    path = _evidence(args.room) / "screen-events.jsonl"
    events = [json.loads(line) for line in (_text(path) or "").splitlines() if line.strip()]
    return {"events": events}


# --- what the fixture screens hold -----------------------------------------------
#
# Written out by hand rather than parsed out of the HTML: a check that read its
# expectations from the page it is checking would agree with any page,
# including a broken one.

SYSTEM_A = {f"REC-{n:03d}": (f"item {n}", str(37 * n)) for n in range(1, 10)}
SYSTEM_B = {f"REF-{100 + n}": (f"item {100 + n}", str(3700 + 37 * n)) for n in range(8)}
TO_SUBMIT = {
    "REF-201": ("first item", "120", True),
    "REF-202": ("second item", "340", False),
    "REF-203": ("bad amount", "not-a-number", False),
    "REF-204": ("fourth item", "75", True),
}
REFUSED = {"REF-203": "Amount must be a number"}
SETTINGS = {
    "General": {"Display name": "Acme workstation", "Theme": "dark", "Open at login": True},
    "Network": {"Proxy": "proxy.acme.test:8080", "Timeout (s)": "30", "Work offline": False},
    "Advanced": {"Cache size (MB)": "512", "Log level": "warning", "Beta features": False},
}
EXPECTED = {
    "systema": {k: {"name": n, "amount": a} for k, (n, a) in SYSTEM_A.items()},
    "systemb": {k: {"name": n, "amount": a} for k, (n, a) in SYSTEM_B.items()},
    "to_submit": {k: {"title": t, "amount": a, "urgent": u,
                      "form_refuses_with": REFUSED.get(k)}
                  for k, (t, a, u) in TO_SUBMIT.items()},
    "settings": SETTINGS,
}


def cmd_expected(args) -> dict:
    return {args.what: EXPECTED[args.what]}


def cmd_records(args) -> dict:
    """Which expected records a CSV the room left holds, and which it does not."""
    evidence = _evidence(args.room)
    text = _text(evidence / "desktop" / args.file)
    if text is None:
        return {"file": args.file, "found": False}
    rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            for row in csv.DictReader(io.StringIO(text.lstrip("﻿")))]
    expected = {}
    for name in args.expect.split(","):
        expected.update({k: v["amount"] for k, v in EXPECTED[name.strip()].items()})
    seen: dict[str, list] = {}
    for row in rows:
        ids = [cell for cell in row.values() if cell in expected]
        if ids:
            seen.setdefault(ids[0], []).append(row)
    return {
        "file": args.file, "found": True, "rows": len(rows),
        "headers": list(rows[0]) if rows else [],
        "missing": sorted(set(expected) - set(seen)),
        "more_than_once": sorted(k for k, v in seen.items() if len(v) > 1),
        "rows_that_are_no_record": len(rows) - sum(len(v) for v in seen.values()),
        "wrong_amount": sorted(k for k, found in seen.items()
                               if not any(expected[k] in row.values() for row in found)),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="evidence.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("replay", help="press keys through the room's config.py, "
                              "with an application focused, and say what it received")
    one.add_argument("room")
    one.add_argument("--app", required=True, help="the focused application, as "
                     "Keyhac names it: Code, Terminal, Safari, notepad, WindowsTerminal...")
    one.add_argument("--keys", required=True, help="comma-separated, e.g. F1,Fn-F1 or "
                     "Ctrl-X,Ctrl-S (pressed in turn, each with its modifiers held)")
    one.add_argument("--title", help="the focused window's title")
    one.add_argument("--before", action="store_true", help="the config the room started from")
    one.add_argument("--platform", default=this_platform(), choices=["mac", "windows"])
    one.set_defaults(func=cmd_replay)
    two = commands.add_parser("bindings", help="bindings added, changed or lost, as Keyhac "
                              "loads the config before and after")
    two.add_argument("room")
    two.add_argument("--platform", default=this_platform(), choices=["mac", "windows"])
    two.set_defaults(func=cmd_bindings)
    three = commands.add_parser("preserved", help="the user's # keep: lines, and how much "
                                "of the original file is still there")
    three.add_argument("room")
    three.set_defaults(func=cmd_preserved)
    four = commands.add_parser("calls", help="the room's calls of Keyhac's MCP tools, "
                               "with what each answered")
    four.add_argument("room")
    four.add_argument("--tool")
    four.set_defaults(func=cmd_calls)
    nine = commands.add_parser("writes", help="files the room wrote with its own file "
                               "tools, not through an action - the Desktop ones first")
    nine.add_argument("room")
    nine.set_defaults(func=cmd_writes)
    five = commands.add_parser("events",help="what the fixture page recorded being done on it")
    five.add_argument("room")
    five.set_defaults(func=cmd_events)
    six = commands.add_parser("records", help="a CSV the room left on the Desktop, "
                              "against the records the screens hold")
    six.add_argument("room")
    six.add_argument("file")
    six.add_argument("--expect", required=True, help="systema, systemb, or both, comma-separated")
    six.set_defaults(func=cmd_records)
    eight = commands.add_parser("rules", help="the skill's mechanical rules (waits not "
                                "sleeps, no coordinates, bounded loops...) on each action "
                                "the room left")
    eight.add_argument("room")
    eight.set_defaults(func=cmd_rules)
    seven = commands.add_parser("expected", help="what a fixture screen holds")
    seven.add_argument("what", choices=sorted(EXPECTED))
    seven.set_defaults(func=cmd_expected)
    args = parser.parse_args(argv)
    print(json.dumps(args.func(args), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
