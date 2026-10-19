"""Keyhac's quality-loop half: scenarios, screens, configs and evidence tools.

No room is driven here. What is checked is everything a run depends on that
can go wrong without one: a scenario that does not parse or names a screen
nobody declared, a fixture config that no longer loads, and an evidence tool
that answers wrongly on a room whose outcome is known.
"""

import json
import pathlib
import subprocess
import sys

import pytest

pytest.importorskip("quality_loop", reason="quality-loop is not installed in this venv")

from quality_loop import scenario  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
QA = ROOT / "qa"
sys.path.insert(0, str(QA))

import configs  # noqa: E402
import evidence  # noqa: E402

SCENARIOS = scenario.load(QA / "scenarios")


def _run_module():
    import importlib
    return importlib.import_module("run")


def test_every_scenario_parses_and_they_are_numbered_in_order():
    assert [s.number for s in SCENARIOS] == list(range(1, len(SCENARIOS) + 1))


@pytest.mark.parametrize("case", SCENARIOS, ids=lambda s: s.name)
def test_every_screen_a_scenario_names_is_declared(case):
    run = _run_module()
    named = {word.strip("`") for word in case.section("Screen").split()
             if word.startswith("`") and word.endswith("`")}
    # Backticked words in the section are screen names, and the staged files'.
    screens = named - {name for _source, name in case.meta.get("staged", [])}
    assert screens <= set(run.SCREENS), f"undeclared: {screens - set(run.SCREENS)}"
    for page in (s for s in run.SCREENS.values() if s.kind == "page"):
        assert (run.FIXTURES / page.file).is_file()
    for terminal in (s for s in run.SCREENS.values() if s.kind == "terminal"):
        assert (run.FIXTURES / terminal.script).is_file()


@pytest.mark.parametrize("case", SCENARIOS, ids=lambda s: s.name)
def test_every_staged_file_exists(case):
    for source, _name in case.meta.get("staged", []):
        assert (ROOT / "examples" / "actions" / "fixtures" / source).is_file()


@pytest.mark.parametrize("name", list(configs.DESCRIBED))
@pytest.mark.parametrize("platform", ["mac", "windows"])
def test_every_fixture_config_loads_and_carries_keep_lines(name, platform):
    text = configs.config(name)
    assert text.count(configs.KEEP) >= 4
    assert evidence.replay(text, platform, [])["errors"] == []


@pytest.fixture
def room(tmp_path):
    """A finished room whose config gained F1-off in VS Code and Ctrl-X Ctrl-S."""
    base = configs.config("template")
    anchor = "    # ---- An action written for you by an agent ------------------------\n"
    edit = ('    kt_vscode = keymap.define_keytable(app="Code")\n'
            '    kt_vscode["F1"] = lambda: None\n'
            '    kt_cx = keymap.define_keytable(name="Ctrl-X")\n'
            '    kt["Ctrl-X"] = kt_cx\n'
            '    kt_cx["Ctrl-S"] = f"{MOD2}-S"\n')
    for part in ("before", "after", "desktop"):
        (tmp_path / "evidence" / part).mkdir(parents=True)
    (tmp_path / "evidence" / "before" / "config.py").write_text(base)
    (tmp_path / "evidence" / "after" / "config.py").write_text(base.replace(anchor, edit + anchor, 1))
    rows = "".join(f"REC-{n:03d},item {n},{37 * n}\n" for n in range(1, 9))
    (tmp_path / "evidence" / "desktop" / "orders.csv").write_text(
        "id,name,amount\n" + rows + "REC-001,item 1,37\n")
    return tmp_path


def _evidence(*args):
    done = subprocess.run([sys.executable, str(QA / "evidence.py"), *map(str, args)],
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_replay_answers_what_the_application_received(room):
    pressed = _evidence("replay", room, "--app", "Code", "--keys", "F1,Fn-F1",
                        "--platform", "mac")["presses"]
    assert [(p["key"], p["consumed"], p["received"]) for p in pressed] == [
        ("F1", True, []), ("Fn-F1", False, ["Fn-F1"])]
    for platform, save in (("mac", "Cmd-S"), ("windows", "Ctrl-S")):
        pressed = _evidence("replay", room, "--app", "Safari", "--keys", "Ctrl-X,Ctrl-S",
                            "--platform", platform)["presses"]
        assert [p["received"] for p in pressed] == [[], [save]], platform


def test_replay_of_the_starting_config(room):
    pressed = _evidence("replay", room, "--app", "Code", "--keys", "F1", "--before",
                        "--platform", "mac")["presses"]
    assert pressed[0]["consumed"] is False and pressed[0]["received"] == ["F1"]


def test_bindings_and_preserved(room):
    changes = _evidence("bindings", room, "--platform", "mac")
    assert changes["load_errors_after"] == [] and changes["changed_or_lost"] == []
    assert {(a["table"], a["key"]) for a in changes["added"]} == {
        ("app='Code'", "F1"), ("focus_path_pattern='*'", "Ctrl-X"),
        ("multi-stroke Ctrl-X", "Ctrl-S")}
    kept = _evidence("preserved", room)
    assert kept["changed"] and kept["keep_lines_lost"] == []
    assert kept["original_lines_still_there"] == kept["original_lines"]


def test_records_finds_what_is_missing_and_doubled(room):
    found = _evidence("records", room, "orders.csv", "--expect", "systema")
    assert found["missing"] == ["REC-009"] and found["more_than_once"] == ["REC-001"]
    assert found["wrong_amount"] == []


def test_writes_says_which_desktop_changes_came_after_the_last_action_run(room):
    def use(n, name, given):
        return {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": f"t{n}", "name": name, "input": given}]}}
    lines = [use(1, "Write", {"file_path": str(room / "QUESTIONS.md")}),
             use(2, "Write", {"file_path": "/tmp/home/.keyhac/extensions/orders.py"}),
             use(3, "Write", {"file_path": "/tmp/home/Desktop/orders.csv"}),
             use(4, "mcp__keyhac__start_action", {"name": "orders.Orders"}),
             use(5, "Edit", {"file_path": "C:\\Users\\run\\Desktop\\orders.csv"})]
    (room / "transcript.jsonl").write_text("\n".join(json.dumps(line) for line in lines))
    found = _evidence("writes", room)
    assert found["desktop"] == [
        {"file": "orders.csv", "tool": "Write", "call": 3, "an_action_ran_after": True},
        {"file": "orders.csv", "tool": "Edit", "call": 5, "an_action_ran_after": False}]
    assert found["last_start_action"] == {"call": 4, "action": "orders.Orders"}
    assert found["note"].startswith("1 Desktop change")
    assert [w["path"] for w in found["outside_the_room"]] == [
        "/tmp/home/.keyhac/extensions/orders.py"]
    (room / "transcript.jsonl").write_text("\n".join(json.dumps(line) for line in lines[:4]))
    assert _evidence("writes", room)["note"].startswith("no Desktop change")


def test_the_settings_page_is_what_its_ground_truth_says():
    """Written out by hand; checked against the page so the two cannot drift."""
    page = (ROOT / "examples" / "actions" / "fixtures" / "settings.html").read_text()
    for tab in evidence.SETTINGS.values():
        for label, value in tab.items():
            assert f">{label}<" in page.replace(f">{label}</", f">{label}<"), label
            if isinstance(value, str):
                assert f'value="{value}"' in page, value


def test_the_runs_skip_a_screen_only_you_can_put_up_unless_named():
    run = _run_module()
    product = run.Keyhac()
    everything = product.runs(SCENARIOS)
    assert not [c for c, _v in everything if c.meta.get("needs")]
    assert len(everything) == len(SCENARIOS) - 2, "one room per scenario"
    slack = [c for c in SCENARIOS if c.number == 9]
    assert product.runs(slack, named=True) == [(slack[0], "")]


def test_each_access_is_some_scenarios_and_names_its_room():
    run = _run_module()
    accesses = {c.meta.get("access", "files") for c in SCENARIOS}
    assert accesses == {"files", "mcp"}, "an access nobody runs, or one not known"
    rooms = dict((c.name, v) for c, v in run.Keyhac().runs(SCENARIOS, named=True))
    assert {name for name, v in rooms.items() if v == "mcp"} == {
        c.name for c in SCENARIOS if c.meta.get("access") == "mcp"}


def test_a_files_room_reaches_the_keyhac_folder_and_an_mcp_room_does_not(tmp_path,
                                                                        monkeypatch):
    run = _run_module()
    product = run.Keyhac()
    product._home = home = run.Home()
    try:
        monkeypatch.setattr(run, "endpoint", lambda _home: {"bridge": "/bin/true"})
        files = product.session(tmp_path / "01_orders_across_pages")
        mcp = product.session(tmp_path / "01_orders_across_pages.mcp")
        assert set(files.add_dirs) == {home.keyhac, home.desktop}
        assert list(mcp.add_dirs) == []
    finally:
        home.remove()


def test_each_config_editing_scenario_names_one_config_and_each_config_is_used():
    named = {c.name: c.meta.get("config", configs.DEFAULT) for c in SCENARIOS
             if c.meta.get("edits_config")}
    assert set(named.values()) <= set(configs.DESCRIBED)
    assert set(named.values()) == set(configs.DESCRIBED), "a fixture config nobody runs"
    assert not [c for c in SCENARIOS if "configs" in c.meta], "one config per scenario now"


def test_the_command_line_finds_this_adapter_and_its_scenarios_check():
    from quality_loop import cli, config
    settings = config.find(ROOT / "qa")
    assert settings.root == ROOT and settings.rooms() == (ROOT / "cleanroom").resolve()
    product = config.load_adapter(settings)
    assert product.name == "keyhac"
    errors, _warnings = cli.check(product)
    assert errors == []


def test_a_run_s_home_is_its_own_and_only_made_when_a_run_needs_it():
    run = _run_module()
    product = run.Keyhac()
    product.runs(SCENARIOS)
    assert product._home is None, "listing or checking scenarios makes no home"
    try:
        home = product.home
        assert pathlib.Path.home() not in home.root.parents
        assert ROOT not in home.root.parents, "a room must not reach the checkout"
        rules = product.rules()
        assert str(home.extensions) in rules and str(home.desktop) in rules
        assert "{" not in rules
        assert home.environment()["HOME"] == str(home.root)
        stand_in = product.stand_in(SCENARIOS[10], "short", home.root / "room")
        assert stand_in.home == home.keyhac and stand_in.marker == home.stand_in
    finally:
        product.home.remove()
