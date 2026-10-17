"""An Electron target: it shows nothing until asked, and must be asked."""

import re

TITLE = "List the Slack channels with unread messages"
#: No fixture can stand in for Slack, so the operator puts it up and names the
#: case to run it; a run of every case leaves it out.
NEEDS = "the Slack desktop app, signed in, with a channel or two unread"
PROMPT = """\
Make me an action that lists the Slack channels where I have unread messages -
from the Slack desktop app - and shows them to me so I can jump to one.
"""
MUST = """\
- It turns Slack's content on before reading (content_access, or
  enable_content_access on and then off) and hands it back when done; the
  reply says Slack shows nothing to assistive tools until asked.
- Run against Slack as it is, it lists the unread channels the sidebar
  shows, by name.
Must not: decide Slack has no readable UI because its window is empty;
fall back to coordinates or a screenshot.
"""

ASKS = re.compile(r"\bcontent_access\s*\(|\benable_content_access\s*\(")


def check(o):
    actions = o.actions()
    if not actions:
        return ["no action was written to extensions/"]
    if not any(ASKS.search(source) for source in actions.values()):
        return ["no action asks Slack to expose its content "
                "(content_access / enable_content_access)"]
    return []


def reference(o):
    o.extensions["slack_unread.py"] = (
        "from keyhac import *\n\n\n"
        "class SlackUnread(ThreadedAction):\n"
        "    def run(self):\n"
        "        with self.ui.content_access():\n"
        "            return []\n")
    return o
