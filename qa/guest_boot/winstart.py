"""qa/daemon.py in a Windows guest, started with no console window.

Not run on the host. qa/guest.py copies this directory into the run's stage
and has a scheduled task run it with the bundle's pythonw.exe. A batch file
would do the same with less code, but cmd.exe opens a console - on Windows 11
a Windows Terminal window - on the desktop the room looks at, and Keyhac
would then hand the foreground back to it rather than to the desktop.

What the batch file did is done here instead, from env.json beside this
file: the environment (HOME, the quit file, ...), the search path
Keyhac.exe's launcher builds (windows_app/src/launcher.c), and the output
into the log. Then qa/daemon.py runs as __main__.
"""

import json
import os
import runpy
import site
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "env.json"), encoding="utf-8") as f:
    settings = json.load(f)

# pythonw has no stdout or stderr: the log is both. Keyhac's console mirrors
# to whatever sys.stderr is when it is made, so it lands here too.
log = open(settings["log"], "w", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log

os.environ.update(settings["environ"])
if site.USER_SITE in sys.path:
    sys.path.remove(site.USER_SITE)
sys.path[1:1] = settings["path"]

runpy.run_path(settings["daemon"], run_name="__main__")
