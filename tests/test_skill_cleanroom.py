"""The clean room's cases can fail and can pass, and its runner cleans up.

A clean-room run costs minutes and money, so a check that is wrong has to be
caught here instead (issue #193): every case's ``check`` is run against the
starting state - a run that did nothing must fail - and against its
``reference`` outcome - a run that did the task must pass. On both platforms,
because a key-table check presses keys through the platform's own key names
and a config that runs on one machine can be silently dead on the other.

The runner's half is the part an interrupted run used to get wrong: the
operator's config.py comes back, and everything the room made is collected,
whatever ended the run. No session is started here.
"""

import json
import pathlib
import sys
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLEANROOM = ROOT / ".claude" / "skills" / "keyhac-skill-cleanroom"
sys.path.insert(0, str(CLEANROOM / "scripts"))

import outcome  # noqa: E402
import open_room  # noqa: E402
import run_room  # noqa: E402

CASES = outcome.load_cases()
PLATFORMS = ("mac", "windows")


def _both(cases):
    return [pytest.param(case, platform, id=f"{case.NAME}-{platform}")
            for case in cases for platform in PLATFORMS]


# --- the cases -------------------------------------------------------------------

def test_the_cases_are_numbered_in_order_and_complete():
    numbers = [case.NUMBER for case in CASES]
    assert numbers == list(range(1, len(CASES) + 1))
    for case in CASES:
        assert case.TITLE and case.PROMPT.strip() and case.MUST.strip(), case.NAME
        assert callable(case.check) and callable(case.reference), case.NAME


def test_key_table_configuration_is_covered():
    """Since #164 the skill covers key tables too, and no case used to."""
    config_only = [c for c in CASES if c.EDITS_CONFIG and not c.SCREEN]
    assert len(config_only) >= 3


#: Words that only make sense next to another case. A room is given one case
#: and nothing else, so each of these sent one looking for the missing half.
CONTINUATIONS = ("same as above", "as above", "do the same", "it died",
                 "the deploy", "the settings window")

#: The skill's own vocabulary. A prompt that names the mechanism has done the
#: skill's work for it: the eval is whether the right action comes out of an
#: intent-only description.
API_WORDS = ("all_text", "set_text", "set_value", "set_checked", "wait_for",
             "get_ui_tree", "describe_screen", "content_access", "define_keytable",
             "describe_keymap", "write_config", "reload_config", "Locator")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.NAME)
def test_every_prompt_stands_alone_and_names_no_mechanism(case):
    prompt = case.PROMPT.lower()
    assert not [w for w in CONTINUATIONS if w in prompt]
    assert not [w for w in API_WORDS if w.lower() in prompt]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.NAME)
def test_every_screen_and_staged_file_exists(case):
    screen = case.SCREEN or {}
    for page in screen.get("pages", []):
        assert (outcome.FIXTURES / page).is_file()
    if "terminal" in screen:
        assert (outcome.FIXTURES / screen["terminal"][0]).is_file()
    for fixture, _name in case.STAGED:
        assert (outcome.FIXTURES / fixture).is_file()
    assert case.SCREEN or case.NEEDS or case.EDITS_CONFIG, \
        "a case with no screen, nothing it needs and no config edit tests nothing"


@pytest.mark.parametrize("platform", PLATFORMS)
def test_the_fixture_config_loads_and_carries_the_user_s_edits(platform):
    config = outcome.fixture_config()
    assert outcome.replay(config, platform).errors == []
    assert config.count(outcome.KEEP) == 5
    untouched = outcome.Outcome.untouched(CASES[0], platform)
    assert outcome.common_failures(untouched, CASES[0]) == []


@pytest.mark.parametrize("case,platform", _both(CASES))
def test_doing_nothing_fails(case, platform):
    found = outcome.failures(case, outcome.Outcome.untouched(case, platform))
    assert found, "a run that did nothing would pass this check"


@pytest.mark.parametrize("case,platform", _both(CASES))
def test_the_reference_passes(case, platform):
    done = case.reference(outcome.Outcome.untouched(case, platform))
    assert outcome.failures(case, done) == []


def _case(number):
    return next(case for case in CASES if case.NUMBER == number)


def _reference(number, platform="mac"):
    case = _case(number)
    return case, case.reference(outcome.Outcome.untouched(case, platform))


@pytest.mark.parametrize("platform", PLATFORMS)
def test_f1_off_everywhere_fails_the_vscode_case(platform):
    case, done = _reference(11, platform)
    done.config_after = done.config_before.replace(
        '    kt[f"{MOD1}-I"] = "Up"',
        '    kt["F1"] = lambda: None\n    kt[f"{MOD1}-I"] = "Up"', 1)
    assert any("no longer reaches" in f for f in outcome.failures(case, done))


def test_emacs_keys_left_on_in_the_terminal_fail():
    case, done = _reference(12)
    done.config_after = done.config_before.replace(
        '    kt[f"{MOD1}-I"] = "Up"',
        '    kt["Ctrl-F"] = "Right"\n    kt["Ctrl-B"] = "Left"\n'
        '    kt["Ctrl-N"] = "Down"\n    kt["Ctrl-P"] = "Up"\n'
        '    kt[f"{MOD1}-I"] = "Up"', 1)
    assert any("in the terminal" in f for f in outcome.failures(case, done))


def test_a_save_key_hard_coded_for_one_platform_fails_on_the_other():
    case, done = _reference(13, "windows")
    done.config_after = done.config_after.replace('f"{MOD2}-S"', '"Cmd-S"')
    assert any("not Ctrl-S" in f for f in outcome.failures(case, done))


def test_losing_a_user_line_fails():
    case, done = _reference(11)
    done.config_after = done.config_after.replace("  # keep: chat", "")
    assert any("lost the user's line" in f for f in outcome.failures(case, done))


def test_overriding_a_user_binding_fails():
    case, done = _reference(11)
    done.config_after = done.config_after.replace(
        '"Code|Visual Studio Code")\n',
        '"Code|Visual Studio Code")\n    kt[f"{MOD1}-3"] = "Ctrl-3"\n', 1)
    assert any("is gone or changed" in f for f in outcome.failures(case, done))


def test_a_config_that_no_longer_loads_fails():
    case, done = _reference(11)
    done.config_after += "\nthis is not python\n"
    assert any("could not load" in f for f in outcome.failures(case, done))


def test_an_edit_never_reloaded_fails():
    case, done = _reference(11)
    done.calls = done.calls[:1]
    assert any("never reloaded" in f for f in outcome.failures(case, done))


def test_editing_config_unasked_fails():
    case, done = _reference(1)
    done.config_after = done.config_before + "# tidied\n"
    assert any("did not ask for" in f for f in outcome.failures(case, done))


def test_submitting_a_done_row_again_fails():
    case, done = _reference(3)
    done.events.insert(0, done.events[0] | {"ref": "REF-201", "title": "first item",
                                            "amount": "120", "urgent": True,
                                            "answer": "accepted REF-201"})
    assert any("submitted again" in f for f in outcome.failures(case, done))


def test_pressing_delete_all_fails():
    case, done = _reference(4)
    done.events.append({"page": "dialog.html", "what": "delete-all"})
    assert any("every record is gone" in f for f in outcome.failures(case, done))


def test_reading_the_terminal_is_not_running_the_action():
    """The screen says parser.py line 42 too - only the action's answer counts."""
    case, done = _reference(6)
    done.calls = [outcome.ToolCall("mcp__keyhac__read_text", {},
                                   result='File "/tmp/acme/src/parser.py", line 42')] \
        + done.calls[2:]
    assert any("parser.py line 42" in f for f in outcome.failures(case, done))


def test_a_settings_dump_missing_a_tab_fails():
    case, done = _reference(8)
    data = json.loads(done.outputs["preferences.json"])
    del data["Advanced"]
    done.outputs["preferences.json"] = json.dumps(data)
    assert outcome.failures(case, done)


# --- reading a room back ---------------------------------------------------------

def test_an_outcome_reads_back_from_the_room(tmp_path):
    room = tmp_path / "room"
    evidence = room / outcome.EVIDENCE
    (evidence / "desktop").mkdir(parents=True)
    (evidence / "extensions").mkdir()
    (evidence / "run.json").write_text(json.dumps({"case": "x", "platform": "windows"}))
    (evidence / "config.before.py").write_text("before\n")
    (evidence / "config.after.py").write_text("after\n")
    (evidence / "desktop" / "orders.csv").write_text("id\nREC-001\n")
    (evidence / "extensions" / "act.py").write_text("x = 1\n")
    (evidence / "screen-events.jsonl").write_text('{"what": "approve"}\n')
    (room / "transcript.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "mcp__keyhac__reload_config",
             "input": {}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "a",
             "content": [{"type": "text", "text": "config reloaded"}]}]}},
        {"type": "result", "result": "All done."},
    ]))
    found = outcome.Outcome.from_room(room)
    assert found.platform == "windows" and found.config_changed
    assert found.csv_rows("orders.csv") == [{"id": "REC-001"}]
    assert found.actions() == {"act": "x = 1\n"}
    assert found.screen("approve") == [{"what": "approve"}]
    assert found.reply == "All done."
    assert found.keyhac_said("reloaded", "reload_config")


# --- the runner ------------------------------------------------------------------

def test_the_session_is_restricted_refuses_the_rest_and_is_not_kept(monkeypatch):
    monkeypatch.setattr(run_room, "find_claude", lambda: "claude")
    command = run_room.session_command("bridge")
    assert "--restricted" in command and "--strict-mcp-config" in command
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert "--no-session-persistence" in command
    assert command[command.index("-p") + 1] == run_room.PROMPT
    assert "bypassPermissions" not in " ".join(command)
    allow = json.loads(command[command.index("--settings") + 1])["permissions"]["allow"]
    assert not [tool for tool in allow if tool.startswith("Bash")]


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A Keyhac home and a Desktop of the operator's, in a temp directory."""
    home = tmp_path / "keyhac"
    (home / "extensions").mkdir(parents=True)
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    (home / "config.py").write_text("# the operator's own\n")
    (home / "config.py.bak-20260101-000000").write_text("# an old backup\n")
    (home / "mcp.json").write_text("{}")
    (home / "extensions" / "theirs.py").write_text("# someone else's action\n")
    (desktop / "notes.txt").write_text("mine\n")
    for name, value in (("KEYHAC_HOME", home), ("CONFIG", home / "config.py"),
                        ("EXTENSIONS", home / "extensions"), ("DESKTOP", desktop),
                        ("STAND_IN", home / "cleanroom-stand-in")):
        monkeypatch.setattr(run_room, name, value)
    reloads = []
    monkeypatch.setattr(run_room, "_reload_quietly", lambda: reloads.append(1) or "ok")
    room = tmp_path / "room"
    room.mkdir()
    return home, desktop, room, reloads


def test_the_stand_in_comes_back_and_everything_new_is_collected(machine):
    home, desktop, room, reloads = machine
    case = _case(5)                      # stages to_submit.csv
    with pytest.raises(KeyboardInterrupt):
        with run_room.Stage(room, case):
            assert (home / "config.py").read_text(encoding="utf-8") == outcome.fixture_config()
            assert (desktop / "to_submit.csv").exists()
            # What a room leaves: an action and a helper package, a state file,
            # a write_config backup of the fixture, and a Desktop output.
            (home / "extensions" / "submit_rows.py").write_text("x = 1\n")
            (home / "extensions" / "helpers").mkdir()
            (home / "extensions" / "helpers" / "__init__.py").write_text("")
            (home / "extensions" / "submit_rows.state.json").write_text("{}")
            (home / "config.py.bak-20991231-235959").write_text("# the fixture\n")
            (home / "config.py.bak-20260101-000000").unlink()     # pruned
            (desktop / "report.txt").write_text("done\n")
            (home / "config.py").write_text(outcome.fixture_config() + "# edited\n",
                                            encoding="utf-8")
            raise KeyboardInterrupt      # Ctrl-C, or SIGTERM turned into one
    assert (home / "config.py").read_text() == "# the operator's own\n"
    assert (home / "config.py.bak-20260101-000000").exists(), "a pruned backup came back"
    assert not (home / "cleanroom-stand-in").exists()
    assert sorted(p.name for p in (home / "extensions").iterdir()) == ["theirs.py"]
    assert sorted(p.name for p in desktop.iterdir()) == ["notes.txt"]
    assert (home / "mcp.json").exists(), "Keyhac's own file was left alone"
    evidence = room / outcome.EVIDENCE
    assert sorted(p.name for p in (evidence / "extensions").iterdir()) == [
        "helpers", "submit_rows.py", "submit_rows.state.json"]
    assert sorted(p.name for p in (evidence / "desktop").iterdir()) == [
        "report.txt", "to_submit.csv"]
    assert [p.name for p in (evidence / "keyhac").iterdir()] == [
        "config.py.bak-20991231-235959"]
    assert (evidence / "config.after.py").read_text().endswith("# edited\n")
    assert len(reloads) == 2, "the fixture was loaded, and then the operator's again"


def test_a_stand_in_left_behind_refuses_the_next_run_until_restored(machine):
    home, _desktop, room, _reloads = machine
    stand_in = home / "cleanroom-stand-in"
    stand_in.mkdir()
    (home / "config.py").replace(stand_in / "config.py")
    (home / "config.py").write_text("# the fixture, left in place\n")
    with pytest.raises(SystemExit, match="--restore"):
        run_room.Stage(room, _case(11)).__enter__()
    assert run_room.restore() == 0
    assert (home / "config.py").read_text() == "# the operator's own\n"
    assert not stand_in.exists()


def test_a_staged_file_never_overwrites_the_operator_s(machine):
    _home, desktop, room, _reloads = machine
    (desktop / "to_submit.csv").write_text("theirs\n")
    with pytest.raises(SystemExit, match="already exists"):
        run_room.Stage(room, _case(5)).__enter__()
    assert (desktop / "to_submit.csv").read_text() == "theirs\n"


def test_the_audit_flags_the_checkout_in_either_path_spelling(tmp_path):
    room = run_room.ROOMS / "test-audit-room"
    events = [
        {"type": "system", "subtype": "init", "tools": [], "slash_commands": [],
         "mcp_servers": [{"name": "keyhac", "status": "connected"}]},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "a", "name": "Read",
             "input": {"file_path": str(ROOT / "keyhac" / "core" / "ui.py")}},
            {"type": "tool_use", "id": "b", "name": "Read",
             "input": {"file_path": str(room / "skills" / "keyhac" / "SKILL.md")}},
            {"type": "tool_use", "id": "c", "name": "Glob",
             "input": {"path": str(ROOT / "examples").replace("/", "\\")}},
        ]}},
    ]
    room.mkdir(parents=True, exist_ok=True)
    try:
        (room / "transcript.jsonl").write_text("\n".join(json.dumps(e) for e in events))
        breaches, refused = run_room.audit(room, set())
    finally:
        for path in room.iterdir():
            path.unlink()
        room.rmdir()
    assert any("ui.py" in b for b in breaches)
    assert any("examples" in b for b in breaches)
    assert not any("SKILL.md" in b for b in breaches)
    assert refused == []


def test_the_room_holds_the_skill_and_the_prompt_and_nothing_else(tmp_path, monkeypatch):
    import zipfile
    bundle = tmp_path / "keyhac-skill.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: keyhac\n---\n")
    monkeypatch.setattr(open_room, "bundles_for", lambda version: [bundle])
    case = _case(1)
    room = open_room.build_room(case.PROMPT, tmp_path / "room")
    assert sorted(p.name for p in room.iterdir()) == [
        "CLAUDE.md", "QUESTIONS.md", "RULES.md", "TASK.md", "skills"]
    task = (room / "TASK.md").read_text(encoding="utf-8")
    assert case.PROMPT.strip() in task
    assert case.MUST.strip().splitlines()[0] not in task, "the scoring key leaked"


@pytest.mark.parametrize("page", ["dialog.html", "submit.html"])
def test_the_fixture_pages_report_what_is_done_on_them(page):
    """And fail quietly from file://, which is how the examples open them."""
    text = (outcome.FIXTURES / page).read_text(encoding="utf-8")
    assert "fetch('event'" in text and ".catch(() => {})" in text


def test_the_screen_server_records_what_a_page_reports(tmp_path):
    events = []
    (tmp_path / "page.html").write_text("<title>t</title>")
    origin = run_room._serve(tmp_path, events)
    body = json.dumps({"page": "submit.html", "what": "submit", "ref": "REF-201"})
    request = urllib.request.Request(f"{origin}/event", data=body.encode(), method="POST")
    with urllib.request.urlopen(request, timeout=5) as reply:
        assert reply.status == 204
    with urllib.request.urlopen(f"{origin}/page.html", timeout=5) as reply:
        assert b"<title>t</title>" in reply.read()
    assert events == [{"page": "submit.html", "what": "submit", "ref": "REF-201"}]


def test_run_all_leaves_out_what_only_the_operator_can_put_up():
    needs = {case.NAME for case in CASES if case.NEEDS}
    assert needs == {"09_slack_unread", "10_click_badge_coordinates"}
