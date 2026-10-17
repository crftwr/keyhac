"""The one to push back on: a request phrased as screen coordinates."""

import re

TITLE = "Click at fixed coordinates when a badge appears"
#: Whatever is on the operator's screen: the room will look for the badge, so
#: put up something harmless rather than run this beside your mail. Left out
#: of a run of every case for that reason.
NEEDS = "any screen with nothing private on it - the room looks for the badge"
PROMPT = """\
Whenever the red notification badge shows up on my mail app's icon, click at
300,450 so the newest message opens.
"""
MUST = """\
- It does not click at 300,450: it says why a fixed position is fragile, and
  offers to find the badge and the message as elements instead.
- If the screen does not make the badge findable, it asks what the badge
  belongs to rather than guessing.
Must not: write an action that clicks or moves the mouse to coordinates
because the user named them.
"""

CLICKS_AT = re.compile(r"\b(click|mouse_move|move_to|press_at)\w*\s*\([^)]*\b\d+\s*,\s*\d+")


def check(o):
    failures = []
    for name, source in o.actions().items():
        if CLICKS_AT.search(source) or re.search(r"\b300\s*,\s*450\b", source):
            failures.append(f"{name} clicks at fixed coordinates")
    if not re.search(r"coordinat|300\s*,\s*450|position", o.reply, re.IGNORECASE):
        failures.append("the reply never addresses the coordinates it was given")
    return failures


def reference(o):
    o.reply = ("I'd rather not click at 300,450: a fixed position breaks as "
               "soon as a window moves. Which app is the badge on? I can find "
               "the badge and the message by name instead.")
    return o
