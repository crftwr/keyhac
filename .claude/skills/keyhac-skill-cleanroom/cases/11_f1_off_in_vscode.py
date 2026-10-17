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
  arrives as F1.
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
    loaded = outcome.replay(o.config_after or "", o.platform, [
        {"app": apps["vscode"], "title": "main.py - project - Visual Studio Code",
         "keys": ["F1"]},
        {"app": apps["editor"], "keys": ["F1"]},
        {"app": apps["browser"], "keys": ["F1"]},
    ], extensions=o.extensions)
    if loaded.errors:
        return []           # common_failures already says it does not load
    vscode, editor, browser = loaded.probes
    failures = []
    if outcome.passed_through(vscode) or outcome.sends(vscode, "F1"):
        failures.append(f"F1 still reaches VS Code ({apps['vscode']}): sent {vscode['sent']}")
    for name, probe in (("an editor", editor), ("a browser", browser)):
        if not outcome.passed_through(probe):
            failures.append(f"F1 no longer reaches {name}")
    failures += outcome.lost_bindings(o)
    return failures


def reference(o):
    o.config_after = o.config.insert(o.config_before,
        "    # F1 opens the command palette in VS Code, and I hit it by accident.\n"
        "    kt_vscode = keymap.define_keytable(app=\"Code|Visual Studio Code\")\n"
        "    kt_vscode[\"F1\"] = lambda: None\n")
    o.calls = [outcome.ToolCall("mcp__keyhac__write_config", {}),
               outcome.ToolCall("mcp__keyhac__reload_config", {}, result="config reloaded")]
    return o
