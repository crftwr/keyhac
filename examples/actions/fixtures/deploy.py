"""A deploy that runs for about a minute and then fails - the deploy case's screen.

The cross-platform twin of deploy.sh. Long enough that polling in a tight
loop is visibly wrong, and it ends in a state an action has to *read* rather
than assume, with a log on disk for "if it fails, grab the log".
"""

import pathlib
import tempfile
import time

log = pathlib.Path(tempfile.gettempdir()) / "acme-deploy.log"
log.write_text("")


def say(line: str) -> None:
    print(line, flush=True)
    with log.open("a") as out:
        out.write(line + "\n")


say("deploy acme 0.4.1 -> production")
for step in ("pushing image", "waiting for capacity", "draining old pods",
             "starting 3 replicas", "health checks", "switching traffic"):
    say(f"[{time.strftime('%H:%M:%S')}] {step} ...")
    time.sleep(9)
say(f"[{time.strftime('%H:%M:%S')}] ERROR: 2 of 3 replicas failed their health check")
say("DEPLOY FAILED")
say(f"log: {log}")
print()
print("(this window is the screen; leave it open)")
