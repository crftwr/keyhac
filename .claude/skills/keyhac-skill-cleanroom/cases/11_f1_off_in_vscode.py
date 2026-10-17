"""A per-application table: one key, one app, nothing else touched."""

import outcome

TITLE = "Stop F1 doing anything in VS Code"
EDITS_CONFIG = True
PROMPT = """\
I keep hitting F1 by accident in VS Code and it throws up the command palette.
Make F1 do nothing in VS Code - everywhere else it should stay as it is.
"""
MUST = """\
- With VS Code focused, F1 does nothing; in any other application it still
  arrives as F1. On macOS that includes Fn+F1, which is how F1 is typed with
  the top row on media keys (the default).
- The table is conditioned on the application (app=), spelled the way this
  platform reports VS Code - not a focus path written from memory.
- One new table, or one line in an existing one; the rest of config.py is
  left as it was; reload_config's answer is read; the reply asks the user to
  press F1 in VS Code, since nothing in the tools can.
Must not: bind F1 in the global table; report it working before anyone has
pressed it.
"""


def check(o):
    apps = outcome.APPS[o.platform]
    # On macOS F1 is also typed as Fn+F1 - the only way, with the top row on
    # its media functions - and Keyhac sees that as Fn-F1 (2026-10-17, on the
    # operator's own Mac: the console showed LFn-F1).
    typed = ["F1", "Fn-F1"] if o.platform == "mac" else ["F1"]
    title = "main.py - project - Visual Studio Code"
    loaded = outcome.replay(o.config_after or "", o.platform, [
        *({"app": apps["vscode"], "title": title, "keys": [key]} for key in typed),
        {"app": apps["editor"], "keys": ["F1"]},
        {"app": apps["browser"], "keys": ["F1"]},
    ], extensions=o.extensions)
    if loaded.errors:
        return []           # common_failures already says it does not load
    *vscode_probes, editor, browser = loaded.probes
    failures = []
    for key, vscode in zip(typed, vscode_probes):
        if outcome.passed_through(vscode) or outcome.sends(vscode, "F1"):
            failures.append(f"{key} still reaches VS Code ({apps['vscode']}): "
                            f"sent {vscode['sent']}")
    for name, probe in (("an editor", editor), ("a browser", browser)):
        if not outcome.passed_through(probe):
            failures.append(f"F1 no longer reaches {name}")
    failures += outcome.lost_bindings(o)
    return failures


def reference(o):
    o.config_after = o.config.insert(o.config_before,
        "    # F1 opens the command palette in VS Code, and I hit it by accident.\n"
        "    kt_vscode = keymap.define_keytable(app=\"Code|Visual Studio Code\")\n"
        "    kt_vscode[\"F1\"] = lambda: None\n"
        "    if keymap.platform == \"mac\":\n"
        "        kt_vscode[\"Fn-F1\"] = lambda: None\n")
    o.calls = [outcome.ToolCall("mcp__keyhac__write_config", {}),
               outcome.ToolCall("mcp__keyhac__reload_config", {}, result="config reloaded")]
    return o
