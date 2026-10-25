"""One screen operation of quality-loop's own OS backend, run in the guest.

Not run on the host. qa/guest_screens.py, the screen backend the operator
uses when Keyhac runs in a VM, writes a request beside this file's stage and
runs it here: over SSH on macOS, from a scheduled task in the desktop session
on Windows (a window opened from SSH lands where nobody sees it). The
request names the operation and its arguments, and where to write the
answer; the backend waits for that file.

The operations are quality-loop's own (quality_loop.screens._mac and
_windows, copied onto the stage), so a page or a terminal in the guest is put
up and recognised exactly as on a desktop the operator shares.
"""

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "ql"))

with open(sys.argv[1], encoding="utf-8") as f:
    request = json.load(f)

try:
    from quality_loop.screens import ScreenError
    if sys.platform == "darwin":
        from quality_loop.screens import _mac as os_backend
    else:
        from quality_loop.screens import _windows as os_backend
    op = request["op"]
    if op == "open_page":
        value = os_backend.open_page(request["url"], request["title"], request["patience"])
    elif op == "open_terminal":
        value = os_backend.open_terminal(request["command"], request["title"],
                                         request["patience"])
    elif op == "close":
        os_backend.close(request["kind"], request["handle"])
        value = None
    else:
        raise ValueError(f"no operation {op!r}")
    answer = {"ok": True, "value": value}
except ScreenError as error:
    answer = {"ok": False, "error": str(error)}
except Exception:
    answer = {"ok": False, "error": traceback.format_exc()}

# Written whole, then renamed: the host polls for the name.
partial = request["result"] + ".partial"
with open(partial, "w", encoding="utf-8") as f:
    json.dump(answer, f)
os.replace(partial, request["result"])
