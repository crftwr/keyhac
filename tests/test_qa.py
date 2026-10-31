"""Keyhac's quality-loop half: scenarios, screens, configs and evidence tools.

No room is driven here. What is checked is everything a run depends on that
can go wrong without one: a scenario that does not parse or names a screen
nobody declared, a fixture config that no longer loads, and an evidence tool
that answers wrongly on a room whose outcome is known.
"""

import json
import pathlib
import subprocess
import types
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
    (tmp_path / "evidence" / "before" / "config.py").write_text(base, encoding="utf-8")
    (tmp_path / "evidence" / "after" / "config.py").write_text(base.replace(anchor, edit + anchor, 1), encoding="utf-8")
    rows = "".join(f"REC-{n:03d},item {n},{37 * n}\n" for n in range(1, 9))
    (tmp_path / "evidence" / "desktop" / "orders.csv").write_text(
        "id,name,amount\n" + rows + "REC-001,item 1,37\n", encoding="utf-8")
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
        # Not the user's own ~/.keyhac (on Windows the temp directory is under
        # the home, so being under it proves nothing).
        assert pathlib.Path.home() / ".keyhac" not in home.root.parents
        assert ROOT not in home.root.parents, "a room must not reach the checkout"
        rules = product.rules()
        assert str(home.extensions) in rules and str(home.desktop) in rules
        assert "{" not in rules
        assert home.environment()["HOME"] == str(home.root)
        stand_in = product.stand_in(SCENARIOS[10], "short", home.root / "room")
        assert stand_in.home == home.keyhac and stand_in.marker == home.stand_in
    finally:
        product.home.remove()


def test_a_guest_run_s_home_is_on_the_share_and_reached_through_this_host_s_bridge(
        tmp_path, monkeypatch):
    run = _run_module()
    monkeypatch.setenv("KEYHAC_QA_GUEST", "some-vm")
    monkeypatch.setenv("KEYHAC_QA_SHARE", f"{tmp_path}=/Volumes/My Shared Files/qa")
    product = run.Keyhac()
    product.guest._windows = False  # not asked over SSH
    assert "--platform mac" in product.evidence_help
    try:
        home = product.home
        assert home.remote and tmp_path.resolve() in home.root.parents
        assert product.guest.path(home.config) == (
            f"/Volumes/My Shared Files/qa/{home.root.name}/.keyhac/config.py")
        # The guest's mcp.json names the guest's bridge, or none: the room
        # gets this venv's, which reads the same file through the share.
        monkeypatch.setattr(run, "endpoint", lambda _home: {"port": 1, "pid": 2})
        session = product.session(tmp_path / "13_ctrl_x_ctrl_s.mcp")
        bridge = session.mcp_servers["keyhac"]["command"]
        assert pathlib.Path(bridge).parent == pathlib.Path(sys.executable).parent
    finally:
        product.home.remove()


def test_a_windows_guest_s_paths_are_windows_paths_and_its_python_the_image_s(tmp_path):
    from guest import Guest
    from quality_loop.guest import GuestError
    guest = Guest("some-vm", tmp_path, r"C:\keyhac-qa")
    guest._windows = True
    assert guest.path(tmp_path / "home" / ".keyhac") == r"C:\keyhac-qa\home\.keyhac"
    with pytest.raises(GuestError, match="python ="):
        guest.python  # no Keyhac in the image: its Python has to be named
    guest.settings["python"] = r"C:\Python314\python.exe"
    assert guest.python == r"C:\Python314\python.exe"


def test_a_guest_needs_both_sides_of_the_share(monkeypatch):
    from guest import Guest
    monkeypatch.delenv("KEYHAC_QA_GUEST", raising=False)
    assert Guest.from_environment() is None
    monkeypatch.setenv("KEYHAC_QA_GUEST", "some-vm")
    monkeypatch.setenv("KEYHAC_QA_SHARE", "/only/the/host/side")
    with pytest.raises(SystemExit):
        Guest.from_environment()


def test_the_guest_installs_what_is_not_ours_and_stages_what_is(tmp_path):
    import importlib.metadata
    import guest
    try:
        importlib.metadata.distribution("puikit")
    except importlib.metadata.PackageNotFoundError:
        # In a VM test run PuiKit is staged, not installed (tools/vm_test.py),
        # and what it needs is read from the installed one.
        pytest.skip("PuiKit is staged here, not installed")
    requirements = guest.third_party_requirements()
    assert not any(r.lower().startswith("puikit") for r in requirements)
    assert any(r.startswith("pillow") for r in requirements)
    assert any("sys_platform" in r for r in requirements), "markers are left to pip"
    assert any(r.startswith("numpy") for r in requirements), "what PuiKit needs too"
    assert not any("extra ==" in r for r in requirements)
    stage = guest.Guest("some-vm", tmp_path, r"C:\qa").stage()
    assert (stage / "keyhac" / "__init__.py").exists()
    assert (stage / "qa" / "daemon.py").exists()
    assert (stage / "lib" / "puikit" / "__init__.py").exists()
    assert (stage / "ql" / "quality_loop" / "guest" / "boot" / "launch.py").exists()


def test_the_daemon_quits_when_the_quit_file_appears(tmp_path, monkeypatch):
    import threading
    import daemon
    quit_file = tmp_path / "quit"
    monkeypatch.setattr(daemon, "QUIT_FILE", str(quit_file))
    waiter = threading.Thread(target=daemon._wait_for_the_runner, daemon=True)
    waiter.start()
    waiter.join(1.0)
    assert waiter.is_alive(), "returned before the file appeared"
    quit_file.touch()
    waiter.join(5.0)
    assert not waiter.is_alive()


def test_a_guest_s_settings_come_from_its_table_and_name_its_machine(tmp_path, monkeypatch):
    import guest
    share = tmp_path / "share"
    share.mkdir()
    settings = tmp_path / "guests.toml"
    settings.write_text(f"""
[some-mac]
share = "{share.as_posix()}=/Volumes/My Shared Files/qa"
tart = "base"

[some-win]
share = "{share.as_posix()}=C:\\\\qa"
vmx = "~/vm.vmx"
snapshot = "clean"
python = "D:\\\\Python\\\\python.exe"
""", encoding="utf-8")
    monkeypatch.setenv("KEYHAC_QA_GUESTS", str(settings))
    monkeypatch.delenv("KEYHAC_QA_SHARE", raising=False)
    monkeypatch.delenv("KEYHAC_QA_GUEST_KEYHAC", raising=False)

    monkeypatch.setenv("KEYHAC_QA_GUEST", "some-mac")
    mac = guest.Guest.from_environment()
    assert mac.guest_share == "/Volumes/My Shared Files/qa"
    assert isinstance(mac.machine(), guest.TartMachine)
    assert mac.machine().name.startswith("base-run-")

    monkeypatch.setenv("KEYHAC_QA_GUEST", "some-win")
    win = guest.Guest.from_environment()
    win._windows = True
    assert win.python == r"D:\Python\python.exe"
    machine = win.machine()
    assert isinstance(machine, guest.VMwareMachine)
    assert machine.snapshot == "clean" and not machine.vmx.startswith("~")

    # The environment still wins, and a guest with no table runs as it is.
    monkeypatch.setenv("KEYHAC_QA_GUEST", "elsewhere")
    monkeypatch.setenv("KEYHAC_QA_SHARE", f"{share}=/x")
    plain = guest.Guest.from_environment()
    assert plain.guest_share == "/x" and plain.settings == {}
    assert not isinstance(plain.machine(), (guest.TartMachine, guest.VMwareMachine))


def test_a_vmware_guest_is_shut_down_after_the_run(tmp_path, monkeypatch):
    # Left running headless, it is a VM the Fusion app cannot open.
    import guest
    share = tmp_path / "share"
    machine = guest.VMwareMachine(guest.Guest(
        "some-win", share, r"C:\qa",
        {"vmx": "w.vmx", "snapshot": "clean", "smb": "//qa@host/qa"}))
    ran = []
    monkeypatch.setattr(guest.ql.subprocess, "run",
                        lambda command, **_: ran.append(command) or types.SimpleNamespace(returncode=0))
    machine.__exit__(None, None, None)
    assert ran[0] == ["umount", str(share)]
    assert ran[1][-3:] == ["stop", "w.vmx", "hard"]


def test_a_clone_is_reached_at_its_own_address_under_the_alias_s_host_key(tmp_path):
    from guest import Guest
    guest = Guest("some-vm", tmp_path, "/x")
    assert guest.ssh_options() == []
    guest.hostname = "192.0.2.7"
    options = guest.ssh_options()
    assert "HostName=192.0.2.7" in options and "HostKeyAlias=some-vm" in options


def test_a_secret_is_read_from_the_keychain_only_when_it_says_so(monkeypatch):
    import guest
    asked = []

    def run(argv, **kwargs):
        asked.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="s3cret\n", stderr="")

    monkeypatch.setattr(guest.ql.subprocess, "run", run)
    assert guest.ql.secret("plain") == "plain" and asked == []
    assert guest.ql.secret("keychain:some-item") == "s3cret"
    assert asked == [["security", "find-generic-password", "-s", "some-item", "-w"]]


def test_in_a_guest_the_screens_go_up_there_and_the_operator_is_told_its_os(
        tmp_path, monkeypatch):
    run = _run_module()
    monkeypatch.setenv("KEYHAC_QA_GUESTS", str(tmp_path / "none.toml"))
    monkeypatch.delenv("KEYHAC_QA_GUEST", raising=False)
    local = run.Keyhac()
    assert local.screen_backend() is None and local.platform() == sys.platform

    monkeypatch.setenv("KEYHAC_QA_GUEST", "some-vm")
    monkeypatch.setenv("KEYHAC_QA_SHARE", f"{tmp_path}=C:\\qa")
    product = run.Keyhac()
    product.guest._windows = True
    product.guest.settings["python"] = r"C:\Python314\python.exe"
    assert product.screen_backend() is None, "nothing to point at before Keyhac is up"
    product._guest_up = types.SimpleNamespace(stage=tmp_path / "stage")
    spec = product.screen_backend()
    assert pathlib.Path(spec["path"]).as_posix().endswith("quality_loop/guest/screens.py")
    assert spec["settings"]["terminal_python"] == r"C:\Python314\python.exe"
    assert spec["settings"]["windows"] is True
    assert spec["settings"]["stage"] == str(tmp_path / "stage")
    assert product.platform() == "win32"


def test_a_mac_guest_without_a_named_python_runs_keyhac_app_s(tmp_path, monkeypatch):
    from guest import Guest
    monkeypatch.delenv("KEYHAC_QA_GUEST_KEYHAC", raising=False)
    mac = Guest("some-vm", tmp_path, "/Volumes/My Shared Files/qa")
    mac._windows = False
    assert mac.python.startswith("/Applications/Keyhac.app/Contents/Frameworks/")
    mac.settings["python"] = "/usr/local/bin/python3.14"
    assert mac.python == "/usr/local/bin/python3.14"
