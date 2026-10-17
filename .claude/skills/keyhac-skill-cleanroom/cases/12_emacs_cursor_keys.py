"""A global remap with an exception: table conditions, and definition order."""

import outcome

TITLE = "Emacs cursor keys everywhere but the terminal"
EDITS_CONFIG = True
PROMPT = """\
Give me Emacs-style cursor movement everywhere: Ctrl-F, Ctrl-B, Ctrl-N and
Ctrl-P to move right, left, down and up. But not in my terminal - there those
keys have to keep doing what they do now.
"""
MUST = """\
- In an ordinary application Ctrl-F/B/N/P send Right/Left/Down/Up; in the
  terminal they arrive unchanged.
- The exception uses the config's own idea of a terminal where it has one
  (the template's is_terminal, or the app list of a terminal table it
  already has), otherwise application names; and the result is checked with
  describe_keymap: which tables are active, and whether a later one
  overrides the new keys.
- It says what the user loses: on Windows, Ctrl-F, Ctrl-N and Ctrl-P are
  Find, New and Print in most applications.
Must not: invent a focus path for the terminal; leave the keys remapped in
the terminal because a later table was never looked at.
"""

MOVES = {"F": "Right", "B": "Left", "N": "Down", "P": "Up"}


def check(o):
    apps = outcome.APPS[o.platform]
    probes = [{"app": apps[where], "keys": [f"Ctrl-{key}"]}
              for where in ("editor", "terminal") for key in MOVES]
    loaded = outcome.replay(o.config_after or "", o.platform, probes,
                            extensions=o.extensions)
    if loaded.errors:
        return []
    editor, terminal = loaded.probes[:len(MOVES)], loaded.probes[len(MOVES):]
    failures = []
    for (key, move), probe in zip(MOVES.items(), editor):
        if not outcome.sends(probe, move) or outcome.sends(probe, key):
            failures.append(f"Ctrl-{key} in an editor sent {probe['sent']}, not {move}")
    for (key, move), probe in zip(MOVES.items(), terminal):
        if outcome.sends(probe, move) or not outcome.passed_through(probe):
            failures.append(f"Ctrl-{key} in the terminal ({apps['terminal']}) "
                            f"no longer arrives as itself: sent {probe['sent']}")
    failures += outcome.lost_bindings(o)
    return failures


def reference(o):
    o.config_after = o.config.insert(o.config_before,
        "    # Emacs cursor keys, except in a terminal.\n"
        "    kt_emacs = keymap.define_keytable(\n"
        f"        custom_condition_func={o.config.not_terminal})\n"
        "    kt_emacs[\"Ctrl-F\"] = \"Right\"\n"
        "    kt_emacs[\"Ctrl-B\"] = \"Left\"\n"
        "    kt_emacs[\"Ctrl-N\"] = \"Down\"\n"
        "    kt_emacs[\"Ctrl-P\"] = \"Up\"\n")
    o.calls = [outcome.ToolCall("mcp__keyhac__write_config", {}),
               outcome.ToolCall("mcp__keyhac__reload_config", {}, result="config reloaded")]
    return o
