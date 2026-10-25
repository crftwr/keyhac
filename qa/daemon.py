"""Keyhac from this checkout, with its MCP endpoint on and no timeout.

    python qa/daemon.py [keyhac args]

Started and stopped by qa/run.py, not by hand. It is `python -m keyhac` -
what `make run` does - with two things added once the tray is up:

- the MCP endpoint is switched on with no timeout, as ticking *AI Integration
  > MCP Server* with *No timeout* would. Keyhac itself never does this at
  start-up, on purpose: a start-up that reopened the endpoint is the way back
  to one nobody remembers arming (see main.py). A quality-loop run is the one
  start-up that *is* someone arming it, so the switch is thrown here, in a
  development tool, and the product keeps its rule.
- it quits when its stdin closes. That is the runner's way of saying "done":
  it works the same on macOS and Windows, where there is no SIGINT to send to
  a process that does not share a console, and it means a runner that dies
  without saying anything still takes this Keyhac with it.

On the way out the endpoint is stopped first, so mcp.json is removed rather
than left naming a port nothing answers on.

In a VM (qa/guest.py) there is no pipe to hold: Keyhac is started in the
guest's desktop session by `open` or a scheduled task. There the runner sets
KEYHAC_QA_QUIT_FILE, and the file appearing is "done".
"""

import os
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

#: The file whose appearance means "done", when there is no pipe to watch.
QUIT_FILE = os.environ.get("KEYHAC_QA_QUIT_FILE")

#: Taken before Keyhac starts: the console redirects the standard streams, and
#: what is watched here is the pipe the runner holds, not whatever sys.stdin
#: becomes. Not taken with a quit file: a scheduled task may have no stdin.
_RUNNER = None if QUIT_FILE else os.dup(0)


def _wait_for_the_runner() -> None:
    if QUIT_FILE:
        while not os.path.exists(QUIT_FILE):
            time.sleep(0.5)
        return
    try:
        while os.read(_RUNNER, 1024):
            pass
    except OSError:
        pass


def _arm(console, keymap) -> None:
    """Endpoint on, and a thread that quits Keyhac when the runner lets go."""
    keymap.start_mcp_server(timeout=None)
    print("qa: MCP server on, no timeout", flush=True)

    def on_main_thread(func):
        backend = console.backend
        if backend.capabilities.supports("main_thread_dispatch"):
            backend.call_on_main_thread(func)
        else:
            keymap.call_on_main_thread(func)

    def quit_keyhac():
        keymap.stop_mcp_server()
        console.quit()

    def wait_for_the_runner():
        _wait_for_the_runner()
        print("qa: runner done - quitting Keyhac", flush=True)
        on_main_thread(quit_keyhac)

    threading.Thread(target=wait_for_the_runner, daemon=True,
                     name="qa-quit").start()


def arm_at_tray(tray) -> None:
    """Make keyhac.ui.tray's install_tray arm the endpoint once the tray is up.

    The last thing main() wires before the loop runs, and it is handed the
    console and the keymap - so the endpoint opens on a Keyhac that is
    otherwise exactly the one `make run` starts. Also called by the guest
    bootstrap (qa/guest_boot/sitecustomize.py), where Keyhac.app runs main().
    """
    install_tray = tray.install_tray

    def install_tray_and_arm(console, keymap, hook):
        result = install_tray(console, keymap, hook)
        _arm(console, keymap)
        return result

    tray.install_tray = install_tray_and_arm


def main() -> int:
    import keyhac.ui.tray as tray
    arm_at_tray(tray)

    from keyhac.main import main as keyhac_main
    sys.argv = ["keyhac"] + sys.argv[1:]
    return keyhac_main()


if __name__ == "__main__":
    raise SystemExit(main())
