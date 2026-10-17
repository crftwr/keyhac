"""The config.py a room finds: the shipped template, or a config of another shape.

With only the template, a room that *read* the config and adapted to it looks
exactly like one that *assumed* the template - every room found MOD1 / MOD2
and is_terminal - and a skill line naming the template's own constants passes
every run (issue #202). So there are three:

- ``template``: keyhac/_config.py with a user's edits on it;
- ``short``: a dozen hand-written lines, no constants, its own names;
- ``grown``: an older keyhac-mac-style config, inline platform checks, a
  terminal table by app=.

Every one carries ``# keep:`` lines: the user's own edits, which a change
must leave as they are.
"""

from __future__ import annotations

import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
TEMPLATE = ROOT / "keyhac" / "_config.py"

#: Marks the lines that stand for the user's own edits.
KEEP = "# keep:"

#: The user's edits on the template: ``(anchor, replacement)``, each anchor a
#: whole line of keyhac/_config.py that must occur exactly once.
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

#: What each config is, for the run's labels.
DESCRIBED = {
    "template": "the shipped template, with a user's edits",
    "short": "a short hand-written config: no constants, its own names",
    "grown": "an older keyhac-mac-style config: inline platform checks, "
             "a terminal table by app=",
}

DEFAULT = "template"


def _template() -> str:
    lines = TEMPLATE.read_text(encoding="utf-8").split("\n")
    for anchor, replacement in _USER_EDITS:
        hits = [i for i, line in enumerate(lines) if line == anchor]
        if len(hits) != 1:
            raise RuntimeError(f"fixture anchor not found once in {TEMPLATE}: {anchor!r}")
        lines[hits[0]] = replacement
    return "\n".join(lines).rstrip("\n") + "\n# keep: a note of mine at the very end\n"


def config(name: str = DEFAULT) -> str:
    """The text of the config ``name``."""
    if name not in DESCRIBED:
        raise KeyError(f"no fixture config {name!r}; have: {', '.join(DESCRIBED)}")
    if name == "template":
        return _template()
    return (HERE / "configs" / f"{name}.py").read_text(encoding="utf-8")
