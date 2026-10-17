"""A multi-stroke prefix, and saying what taking a key costs."""

import re

import outcome

TITLE = "Ctrl-X then Ctrl-S saves, Emacs style"
EDITS_CONFIG = True
PROMPT = """\
Old Emacs habit: I want Ctrl-X followed by Ctrl-S to save the file I'm in, in
every app.
"""
MUST = """\
- Ctrl-X then Ctrl-S sends the platform's own save shortcut (Cmd-S on
  macOS, Ctrl-S on Windows) in any application.
- Ctrl-X becomes a prefix in a multi-stroke table; the save key is picked
  per platform the way the config already does it (the template's MOD2, a
  constant of its own, or keymap.platform), so one file works on both
  machines.
- It says Ctrl-X is Cut in most Windows applications and that this takes it
  away, before or as it makes the change.
Must not: bind a plain Ctrl-S; leave Ctrl-X doing nothing when another key
follows it without saying so.
"""


def check(o):
    apps = outcome.APPS[o.platform]
    loaded = outcome.replay(o.config_after or "", o.platform, [
        {"app": apps["editor"], "keys": ["Ctrl-X", "Ctrl-S"]},
        {"app": apps["browser"], "keys": ["Ctrl-X", "Ctrl-S"]},
    ], extensions=o.extensions)
    if loaded.errors:
        return []
    failures = []
    save = outcome.SHORTCUT[o.platform]
    for name, probe in zip(("an editor", "a browser"), loaded.probes):
        # Ctrl is physically down for the second stroke, which is part of
        # what the application receives along with whatever the engine sent.
        if outcome.modifiers_at(probe, "S", held=("LCtrl",)) != {save}:
            failures.append(f"Ctrl-X Ctrl-S in {name} sent {probe['sent']}, "
                            f"not {save}-S")
        if outcome.sends(probe, "X"):
            failures.append(f"Ctrl-X in {name} still went through as Ctrl-X")
    if o.platform == "windows" and not re.search(r"\bcut", o.reply, re.IGNORECASE):
        failures.append("the reply does not say Ctrl-X stops being Cut")
    failures += outcome.lost_bindings(o)
    return failures


def reference(o):
    o.config_after = o.config.insert(o.config_before,
        "    # Ctrl-X Ctrl-S saves, as in Emacs.\n"
        "    kt_cx = keymap.define_keytable(name=\"Ctrl-X\")\n"
        f"    {o.config.table}[\"Ctrl-X\"] = kt_cx\n"
        f"    kt_cx[\"Ctrl-S\"] = {o.config.save}\n")
    o.calls = [outcome.ToolCall("mcp__keyhac__write_config", {}),
               outcome.ToolCall("mcp__keyhac__reload_config", {}, result="config reloaded")]
    o.reply = ("Ctrl-X is now a prefix: Ctrl-X then Ctrl-S saves. On Windows "
               "that means Ctrl-X no longer cuts.")
    return o
