"""A long job: wait on the screen's own completion signal, then branch on it."""

TITLE = "Watch a deploy and keep the log if it fails"
#: A terminal running a deploy that takes about a minute and then fails, and
#: says so on screen.
SCREEN = {"terminal": ("deploy.sh", "deploy - acme")}
PROMPT = """\
There's a deploy running in my terminal (the "deploy - acme" window). Make me
something I can start that waits for it to finish and tells me how it went -
and if it failed, saves the log to deploy-failure.log on my Desktop. Start it
on this one.
"""
MUST = """\
- deploy-failure.log on the Desktop holds the deploy's output, down to
  DEPLOY FAILED.
- It waits for the screen to say the deploy is over, with a timeout long
  enough for a real deploy and a message naming what it waits for; running
  out of time raises rather than returning quietly.
- It tells success from failure by reading the result, and says which.
Must not: sleep; poll in a loop of its own.
"""


def check(o):
    log = o.output("deploy-failure.log")
    if log is None:
        return ["no deploy-failure.log on the Desktop"]
    failures = []
    for line in ("DEPLOY FAILED", "failed their health check"):
        if line not in log:
            failures.append(f"deploy-failure.log does not contain {line!r}")
    return failures


def reference(o):
    o.outputs["deploy-failure.log"] = (
        "deploy acme 0.4.1 -> production\n"
        "[10:00:54] ERROR: 2 of 3 replicas failed their health check\n"
        "DEPLOY FAILED\n")
    return o
