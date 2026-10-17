"""What a clean-room run left behind, and the checks every case shares.

A case (``../cases/NN_name.py``) receives an :class:`Outcome` and returns the
list of things that went wrong - empty means it passed. The helpers here answer
the questions a case asks the way Keyhac itself would, not the way the room
says it went:

- a ``config.py`` is loaded by Keyhac's own ``Keymap`` on the fake platform,
  in a subprocess, and keys are *pressed* through the fake hook - so "F1 does
  nothing in VS Code" is checked by pressing F1 with VS Code focused;
- a fixture page reports what was done on it (approve, submit) to the server
  the runner serves it from, so "nothing was submitted twice" is the page's
  record rather than the room's account;
- what came back from Keyhac's MCP tools is read out of the transcript, which
  is the tool's answer and not the model's paraphrase of it.

Also imported by ``tests/test_skill_cleanroom.py``, which runs every case's
check against the untouched starting state and against its reference outcome,
so a check that cannot fail - or cannot pass - is caught without spending a
run (issue #193).
"""

from __future__ import annotations

import csv
import dataclasses
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

SKILL = pathlib.Path(__file__).resolve().parents[1]
ROOT = SKILL.parents[2]
CASES = SKILL / "cases"
FIXTURES = ROOT / "examples" / "actions" / "fixtures"
TEMPLATE = ROOT / "keyhac" / "_config.py"

#: Where a run's evidence goes inside its room, written by the runner after the
#: session has ended - the session never sees it.
EVIDENCE = "evidence"

#: Marks the lines of the fixture config that stand for the user's own edits.
#: Every one must survive whatever the room writes: "change as little as the
#: request needs" is the key-table reference's first rule, and these are what
#: it protects.
KEEP = "# keep:"

#: The user's own edits, laid over the shipped template to make the config a
#: room finds: the file of someone who has used Keyhac for a while.
#: ``(anchor, replacement)``, each anchor a whole line of ``keyhac/_config.py``
#: that must occur exactly once.
_USER_EDITS = (
    ("    keymap.clipboard_history.max_items = 500",
     "    keymap.clipboard_history.max_items = 2000  # keep: I paste a lot"),
    ('    kt[f"{MOD1}-Semicolon"] = InputText("me@example.com")',
     '    kt[f"{MOD1}-Semicolon"] = InputText("dana@acme.test")  # keep: my address'),
    ('    kt[f"{MOD1}-2"] = ActivateWindow(app="chrome|Google Chrome")',
     '    kt[f"{MOD1}-2"] = ActivateWindow(app="chrome|Google Chrome")\n'
     '    kt[f"{MOD1}-3"] = ActivateWindow(app="Slack")  # keep: chat'),
    ('    kt_browser[f"{MOD1}-R"] = f"{MOD2}-R"          # reload',
     '    kt_browser[f"{MOD1}-R"] = f"{MOD2}-R"          # reload\n'
     '    kt_browser[f"{MOD1}-W"] = f"{MOD2}-W"  # keep: close the tab'),
)


def fixture_config() -> str:
    """What the room finds in ``~/.keyhac/config.py``: the shipped template
    with a user's edits on it, each marked with :data:`KEEP`."""
    text = TEMPLATE.read_text(encoding="utf-8")
    lines = text.split("\n")
    for anchor, replacement in _USER_EDITS:
        hits = [i for i, line in enumerate(lines) if line == anchor]
        if len(hits) != 1:
            raise RuntimeError(f"fixture anchor not found once in {TEMPLATE}: "
                               f"{anchor!r}")
        lines[hits[0]] = replacement
    return "\n".join(lines).rstrip("\n") + "\n# keep: a note of mine at the very end\n"


def this_platform() -> str:
    """Keyhac's name for the platform this runs on."""
    return "mac" if sys.platform == "darwin" else "windows"


# --- loading cases -------------------------------------------------------------

def load_cases(names: list[str] | None = None) -> list:
    """Every case module, in file order, or the ones ``names`` picks - by
    number (``3``), by file stem, or by the part after the number."""
    # The cases share what the fixture screens hold through cases/_screens.py.
    if str(CASES) not in sys.path:
        sys.path.insert(0, str(CASES))
    found = []
    for path in sorted(CASES.glob("[0-9][0-9]_*.py")):
        spec = importlib.util.spec_from_file_location(f"case_{path.stem}", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.NAME = path.stem
        module.NUMBER = int(path.stem[:2])
        for attribute, default in (("SCREEN", None), ("STAGED", ()),
                                   ("NEEDS", None), ("EDITS_CONFIG", False)):
            if not hasattr(module, attribute):
                setattr(module, attribute, default)
        found.append(module)
    if not names:
        return found
    wanted = []
    for name in names:
        name = str(name)
        match = [m for m in found
                 if name in (m.NAME, m.NAME.split("_", 1)[1])
                 or (name.isdigit() and int(name) == m.NUMBER)]
        if not match:
            raise SystemExit(f"no case {name!r}; have: "
                             + ", ".join(m.NAME for m in found))
        wanted += match
    return wanted


# --- the outcome ---------------------------------------------------------------

@dataclasses.dataclass
class ToolCall:
    """One tool call of the room's, with what came back."""
    name: str
    input: dict
    result: str = ""
    is_error: bool = False

    @property
    def keyhac(self) -> bool:
        """Whether Keyhac answered it, rather than a file tool."""
        return self.name.startswith("mcp__keyhac__")


@dataclasses.dataclass
class Outcome:
    """What one run left behind."""
    platform: str                         # "mac" | "windows": where it ran
    config_before: str                    # ~/.keyhac/config.py as the room found it
    config_after: str | None              # ... and afterwards (None: gone)
    reply: str = ""                       # the room's final message
    outputs: dict = dataclasses.field(default_factory=dict)     # Desktop files, by name
    extensions: dict = dataclasses.field(default_factory=dict)  # new in extensions/, by name
    events: list = dataclasses.field(default_factory=list)      # what the screen recorded
    calls: list = dataclasses.field(default_factory=list)       # ToolCall, in order
    room: pathlib.Path | None = None

    @property
    def config_changed(self) -> bool:
        return self.config_after != self.config_before

    def output(self, name: str) -> str | None:
        """A file the run left on the Desktop, as text."""
        return self.outputs.get(name)

    def csv_rows(self, name: str) -> list[dict] | None:
        """A CSV the run left on the Desktop, as rows keyed by lowercased
        header, or None when there is no such file or it does not parse."""
        text = self.output(name)
        if text is None:
            return None
        try:
            reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
            return [{(k or "").strip().lower(): (v or "").strip()
                     for k, v in row.items()} for row in reader]
        except csv.Error:
            return None

    def actions(self) -> dict:
        """The Python modules the run wrote into extensions/, by stem."""
        return {pathlib.Path(name).stem: text
                for name, text in self.extensions.items() if name.endswith(".py")}

    def keyhac_said(self, pattern: str, tool: str | None = None) -> bool:
        """Whether any answer Keyhac gave matches ``pattern`` - the tool's own
        output, so a match is evidence rather than the room's report."""
        found = re.compile(pattern, re.IGNORECASE | re.DOTALL)
        return any(found.search(call.result) for call in self.calls
                   if call.keyhac and (tool is None or call.name.endswith(tool)))

    def called(self, tool: str) -> list[ToolCall]:
        """The calls of one Keyhac tool, by its short name."""
        return [call for call in self.calls if call.name == f"mcp__keyhac__{tool}"]

    def screen(self, what: str) -> list[dict]:
        """The screen's own record of one kind of thing done on it."""
        return [event for event in self.events if event.get("what") == what]

    @classmethod
    def untouched(cls, case, platform: str | None = None) -> "Outcome":
        """What a run that did nothing at all would leave: the starting state."""
        config = fixture_config()
        return cls(platform=platform or this_platform(), config_before=config,
                   config_after=config,
                   outputs={name: (FIXTURES / fixture).read_text(encoding="utf-8")
                            for fixture, name in case.STAGED})

    @classmethod
    def from_room(cls, room: pathlib.Path) -> "Outcome":
        """Read back what the runner recorded in ``room/evidence``."""
        evidence = room / EVIDENCE
        meta = json.loads((evidence / "run.json").read_text(encoding="utf-8"))
        after = evidence / "config.after.py"
        return cls(
            platform=meta["platform"],
            config_before=(evidence / "config.before.py").read_text(encoding="utf-8"),
            config_after=after.read_text(encoding="utf-8") if after.exists() else None,
            reply=final_reply(room / "transcript.jsonl"),
            outputs=_texts(evidence / "desktop"),
            extensions=_texts(evidence / "extensions"),
            events=_jsonl(evidence / "screen-events.jsonl"),
            calls=tool_calls(room / "transcript.jsonl"),
            room=room)


def _texts(directory: pathlib.Path) -> dict:
    """Every file directly in ``directory``, as text where it decodes."""
    found = {}
    if directory.is_dir():
        for path in sorted(directory.iterdir()):
            if path.is_file():
                try:
                    found[path.name] = path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    found[path.name] = ""
    return found


def _jsonl(path: pathlib.Path) -> list:
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def transcript_events(transcript: pathlib.Path):
    """Every event in a stream-json transcript that parses."""
    yield from _jsonl(transcript)


def final_reply(transcript: pathlib.Path) -> str:
    replies = [e.get("result", "") for e in transcript_events(transcript)
               if e.get("type") == "result"]
    return replies[-1] if replies else ""


def tool_calls(transcript: pathlib.Path) -> list[ToolCall]:
    """Every tool call in the transcript, paired with its result."""
    calls, by_id = [], {}
    for event in transcript_events(transcript):
        for part in event.get("message", {}).get("content", []) or []:
            if not isinstance(part, dict):
                continue
            if event.get("type") == "assistant" and part.get("type") == "tool_use":
                call = ToolCall(part.get("name", ""), part.get("input", {}) or {})
                calls.append(call)
                by_id[part.get("id")] = call
            elif event.get("type") == "user" and part.get("type") == "tool_result":
                if call := by_id.get(part.get("tool_use_id")):
                    call.result = _result_text(part.get("content"))
                    call.is_error = bool(part.get("is_error"))
    return calls


def _result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text", "")) for p in content if isinstance(p, dict))
    return str(content or "")


# --- the config, as Keyhac loads it --------------------------------------------

#: Loads a config.py in Keyhac's own Keymap, wired to the fake platform the
#: engine tests use, and presses keys through the fake hook. In a subprocess:
#: a config runs arbitrary code at import, puts its extensions/ on sys.path and
#: initialises the key-name tables for one platform, and none of that may leak
#: into the caller or into the next replay.
_REPLAY = r"""
import json, logging, pathlib, sys
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
# Keyhac's loggers hang off "keyhac", which does not propagate to the root.
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
for position, table in enumerate(keymap._all_keytables):
    condition = conditioned.get(id(table))
    where = _condition_text(condition) if condition is not None else f"multi-stroke {table.name}"
    for key, action in table.table.items():
        bindings.append([where, _key_text(key), describe(action)])

MODIFIERS = {"ctrl": "LCtrl", "shift": "LShift", "alt": "LAlt", "opt": "LAlt",
             "cmd": "LCmd", "win": "LWin", "fn": "Fn"}
names = get_key_names()

def press(spelled):
    *mods, key = spelled.split("-")
    held = [names.str_to_vk(MODIFIERS[m.lower()]) for m in mods]
    for vk in held:
        hook.key(vk, True)
    consumed = hook.key(names.str_to_vk(key), True)
    hook.key(names.str_to_vk(key), False)
    for vk in reversed(held):
        hook.key(vk, False)
    return consumed

results = []
for number, probe in enumerate(probes):
    focus.focus = Focus(app_name=probe["app"], pid=100 + number,
                        window_title=probe.get("title", "Untitled"),
                        path=f"/{probe['app']}/{probe.get('title', 'Untitled')}")
    hook.sent.clear()
    consumed = [press(k) for k in probe["keys"]]
    results.append({"consumed": consumed,
                    "sent": [("D-" if down else "U-") + names.vk_to_str(vk)
                             for vk, down, _replay in hook.sent]})
print(json.dumps({"errors": errors, "bindings": bindings, "probes": results}))
"""


@dataclasses.dataclass
class Replay:
    """A config as Keyhac loaded it, and what pressing keys in it did."""
    errors: list
    bindings: list            # [condition text, key, action description]
    probes: list              # per probe: {"consumed": [...], "sent": [...]}


def replay(config: str, platform: str, probes: list[dict] | None = None,
           extensions: dict | None = None) -> Replay:
    """Load ``config`` as Keyhac would on ``platform`` and press ``probes``.

    Each probe is ``{"app": name, "keys": ["Ctrl-F", ...]}``: the focus moves
    to that application and the keys are pressed in turn, each one down and up
    with its modifiers held around it. ``extensions`` (name -> source) are put
    beside the config, so a config that imports the room's action loads.
    """
    # Remembered: a case's checks replay the same fixture several times over,
    # and each replay is a fresh interpreter importing Keyhac.
    key = json.dumps([config, platform, probes or [], sorted((extensions or {}).items())])
    if key not in _replayed:
        _replayed[key] = _replay(config, platform, probes, extensions)
    return _replayed[key]


_replayed: dict[str, Replay] = {}


def _replay(config: str, platform: str, probes: list[dict] | None,
            extensions: dict | None) -> Replay:
    with tempfile.TemporaryDirectory(prefix="keyhac-cleanroom-replay-") as home:
        home_path = pathlib.Path(home)
        (home_path / "config.py").write_text(config, encoding="utf-8")
        (home_path / "extensions").mkdir()
        for name, source in (extensions or {}).items():
            (home_path / "extensions" / name).write_text(source, encoding="utf-8")
        # The replay must not reach the real ~/.keyhac: Keymap resolves the
        # extensions directory beside config_path, and HOME/USERPROFILE point
        # here as well in case anything else asks for the user's home.
        env = dict(os.environ, HOME=home, USERPROFILE=home, PYTHONIOENCODING="utf-8")
        done = subprocess.run(
            [sys.executable, "-c", _REPLAY, str(ROOT), home, platform,
             json.dumps(probes or [])],
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            stdin=subprocess.DEVNULL, env=env, cwd=home)
    if done.returncode != 0 or not done.stdout.strip():
        return Replay([f"the replay itself failed: {done.stderr.strip()[-400:]}"], [], [])
    loaded = json.loads(done.stdout.strip().splitlines()[-1])
    return Replay(loaded["errors"], loaded["bindings"], loaded["probes"])


#: What each platform's applications report as their name, for the probes a
#: key-table case presses keys in. Windows reports the executable without
#: ".exe"; macOS the bundle's display name.
APPS = {
    "mac": {"vscode": "Code", "editor": "TextEdit", "terminal": "Terminal",
            "browser": "Safari"},
    "windows": {"vscode": "Code", "editor": "notepad", "terminal": "WindowsTerminal",
                "browser": "msedge"},
}

#: The modifier a platform's applications use for their own shortcuts.
SHORTCUT = {"mac": "Cmd", "windows": "Ctrl"}


def sends(probe: dict, key: str) -> bool:
    """Whether a probe's keys made the engine emit ``key``."""
    return f"D-{key}" in probe["sent"]


def modifiers_at(probe: dict, key: str, held: tuple = ()) -> set:
    """The modifiers down when the engine sent ``key``: the ones the probe
    holds physically (``held``, e.g. ``("LCtrl",)``) as the engine's own
    releases and presses left them. Spelled without the side: {"Ctrl"}.

    The engine sends only the difference from what is physically down - with
    Ctrl held, Ctrl-S comes out as a bare S - so what was *sent* alone does
    not say which shortcut the application received."""
    down = set(held)
    for name in probe["sent"]:
        edge, vk = name[:2], name[2:]
        if vk == key and edge == "D-":
            break
        (down.add if edge == "D-" else down.discard)(vk)
    else:
        return set()
    return {re.sub(r"^[LR](?=[A-Z])", "", name) for name in down}


def passed_through(probe: dict, index: int = 0) -> bool:
    """Whether the probe's ``index``-th key reached the application as itself:
    not consumed, or consumed and sent on unchanged."""
    return not probe["consumed"][index] or bool(probe["sent"])


# --- what every run must get right ---------------------------------------------

def common_failures(o: Outcome, case) -> list[str]:
    """What every run must get right, whatever it was asked."""
    failures = []
    if o.config_after is None:
        return ["~/.keyhac/config.py is gone"]
    if o.config_changed and not case.EDITS_CONFIG:
        failures.append("config.py was edited, which the task did not ask for")
    if o.config_changed:
        failures += preserved_failures(o)
        if case.EDITS_CONFIG and not _reloaded_after_last_write(o):
            failures.append("config.py was changed and never reloaded afterwards, "
                            "so nothing checked that it still loads")
    return failures


def _reloaded_after_last_write(o: Outcome) -> bool:
    """Whether reload_config ran after the last write to config.py. A write
    the room made with its own file tools rather than write_config counts as
    one before everything, which a later reload still covers."""
    names = [call.name for call in o.calls]
    last_write = max((i for i, name in enumerate(names)
                      if name == "mcp__keyhac__write_config"), default=-1)
    return any(name == "mcp__keyhac__reload_config"
               for name in names[last_write + 1:])


def preserved_failures(o: Outcome) -> list[str]:
    """The user's own edits and the rest of the file, still there and still
    loading."""
    failures = []
    loaded = replay(o.config_after, o.platform, extensions=o.extensions)
    if loaded.errors:
        failures.append(f"Keyhac could not load the resulting config.py: "
                        f"{loaded.errors[:2]}")
    after_lines = set(o.config_after.splitlines())
    for line in o.config_before.splitlines():
        if KEEP in line and line not in after_lines:
            failures.append(f"lost the user's line: {line.strip()}")
    before_lines = o.config_before.splitlines()
    kept = sum(1 for line in before_lines if line in after_lines)
    # An edit changes a line or two of the original; a rewrite changes many.
    # Two lines or 3%, whichever is more, so a short file is not held to a
    # ratio that one edited line already breaks.
    if kept < len(before_lines) - max(2, 0.03 * len(before_lines)):
        failures.append(f"rewrote the file: only {kept} of {len(before_lines)} "
                        f"original lines are still there")
    return failures


def binding_changes(o: Outcome) -> tuple[list, list]:
    """``(added, lost)``: bindings the run's config.py has that the fixture did
    not, and fixture bindings it no longer has as they were. Compared as
    Keyhac loaded each file, so moving a line or respelling a key is no
    change and silently overriding one is."""
    before = replay(o.config_before, o.platform)
    after = replay(o.config_after or "", o.platform, extensions=o.extensions)
    old = {(where, key): action for where, key, action in before.bindings}
    new = {(where, key): action for where, key, action in after.bindings}
    added = [(where, key, action) for (where, key), action in new.items()
             if (where, key) not in old]
    lost = [(where, key, action) for (where, key), action in old.items()
            if new.get((where, key)) != action]
    return added, lost


def lost_bindings(o: Outcome) -> list[str]:
    """A failure line per fixture binding the run took away or replaced."""
    _added, lost = binding_changes(o)
    return [f"the user's binding {key} ({where}) is gone or changed"
            for where, key, _action in lost]


def failures(case, o: Outcome) -> list[str]:
    """Everything wrong with ``o`` as an answer to ``case``."""
    return common_failures(o, case) + case.check(o)
