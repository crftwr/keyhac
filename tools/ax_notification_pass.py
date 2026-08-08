"""Do Electron applications post AX notifications on macOS? (macOS only)

The gap this fills, stated precisely because it is easy to think it is already
closed. Two separate things were measured on macOS on 2026-08-06:

  - **Notifications**, against **Safari**: a `<dialog>` opening posted nothing
    at all, registered on the application element and on the `AXWebArea` alike.
    That is a finding about *WebKit web content*.
  - **Tree exposure**, against **Chromium and Electron** (Chrome, Edge, VS
    Code, Slack): 59 nodes of browser chrome until `set_manual_accessibility()`
    was called, 119 after.

Nobody has measured *notifications from an Electron app*. It is reasonable to
expect the Safari answer to carry over - Electron is Chromium web content - but
that is inference, and `doc/dev/testing.md` should not record inference as
measurement. The Windows side has the matching hole from the other direction:
no observer exists there at all, and what was measured (2026-08-07) was that
VS Code's text is absent from the *first* read and present later, which is tree
exposure rather than notification delivery.

    python tools/ax_notification_pass.py [--app "Visual Studio Code"] [--seconds 20]

**Finder runs first as a control**, automatically, and the pass says so in its
output: a run where Finder posts nothing means the harness is broken - no run
loop, no Accessibility grant - and the Electron row that follows would then be
meaningless. This is the same discipline as `tools/text_pattern_survey.py`,
where the control caught two broken probes before they became findings.

**`set_manual_accessibility(True)` is called on the target before listening.**
Without it an Electron app exposes no content at all, and "no notifications"
would be confounded with "no tree to post about" - two different findings that
look identical in the output.

The in-page change is yours to make: this prints a prompt and listens. Driving
VS Code from an automated session is not reliable - the sandboxed agent shell
holds Accessibility but never window-server key focus (`doc/dev/testing.md`) -
and a pass that silently fails to cause the change would report "no
notifications" for the wrong reason. So the harness is automated and the
stimulus is not.

Notifications are delivered on the main run loop, which is why this turns a
real `MacEventLoop` on the main thread and does its work on a worker - the same
arrangement `keyhac.core.wait` is written against.
"""

import argparse
import pathlib
import subprocess
import sys
import threading
import time
import traceback

if sys.platform != "darwin":
    sys.exit(f"{__file__} is a macOS pass; this is {sys.platform}.")

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ApplicationServices as AS                                    # noqa: E402
from AppKit import NSWorkspace                                      # noqa: E402

from keyhac.core.keymap import Keymap                               # noqa: E402
from keyhac.core.uitree import get_ui_tree                          # noqa: E402
from keyhac.core.vk import init_key_names                           # noqa: E402
from keyhac.core.wait import evaluate_on_main_thread                # noqa: E402
from keyhac.platform.fake import FakeFocusProvider                  # noqa: E402
from keyhac.platform.mac.hook import MacInputHook                   # noqa: E402
from keyhac.platform.mac.loop import MacEventLoop                   # noqa: E402
from keyhac.platform.mac.observer import UIObserver                 # noqa: E402
from keyhac.platform.mac.uielement import UIElement                 # noqa: E402

#: Tried in order when --app is not given.
#: What NSWorkspace calls them, which is not always what the installer does:
#: VS Code reports "Code", and a list saying "Visual Studio Code" can never
#: auto-find the one Electron application most likely to be running.
ELECTRON_CANDIDATES = ("Code", "Visual Studio Code", "Slack", "Google Chrome",
                       "Discord", "Claude")

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def running_apps() -> dict:
    return {str(app.localizedName()): int(app.processIdentifier())
            for app in NSWorkspace.sharedWorkspace().runningApplications()
            if app.localizedName()}


def app_element(pid: int) -> UIElement:
    element = UIElement(AS.AXUIElementCreateApplication(pid))
    AS.AXUIElementSetMessagingTimeout(element._ref, 5.0)
    return element


def node_count(element) -> int:
    """Size of the tree, read the supported way.

    Dispatched even though an AX call into *another* process would probably
    survive being made from a worker: the documented way to read an element
    from off the loop thread is this, every action does it this way, and a pass
    that quietly did otherwise would be testing a path nothing else uses.
    """
    def read():
        try:
            return len(list(get_ui_tree(element, max_depth=12,
                                        max_nodes=1500).walk()))
        except Exception:                                # noqa: BLE001
            return -1

    return evaluate_on_main_thread(read)


def listen(observer: UIObserver, seconds: float) -> list:
    """Collect (elapsed, notification) for `seconds`.

    `UIObserver` is a doorbell rather than a queue - it sets an event and keeps
    only the last name - so this samples it. Two notifications inside one
    sampling gap are recorded as one, which is fine for the question being
    asked: whether *anything* arrives at all.
    """
    arrived = []
    started = time.monotonic()
    deadline = started + seconds
    seen = observer.count
    while time.monotonic() < deadline:
        if observer.count != seen:
            seen = observer.count
            arrived.append((round(time.monotonic() - started, 3), observer.last))
        observer.event.wait(0.05)
        observer.event.clear()
    return arrived


def control_finder() -> bool:
    """Does the harness deliver at all? Finder posts generously if anything does."""
    print("\n=== control: Finder (native Cocoa) ===")
    pid = running_apps().get("Finder")
    if pid is None:
        check("Finder is running", False, "cannot validate the harness")
        return False

    observer = UIObserver(pid)
    check("observer installed on Finder", observer.active,
          f"accepted {len(observer.notifications)} notifications")
    try:
        # An automated stimulus, so the control needs nobody's cooperation -
        # but it has to *cause* something. `open -a Finder ~` is a no-op when a
        # Finder window is already open, which is exactly the state the pass's
        # own previous run leaves behind: the second run of the day then
        # reported a dead control and invalidated its own Electron row.
        subprocess.run(["osascript", "-e",
                        'tell application "Finder" to make new Finder window'],
                       check=False, capture_output=True)
        arrived = listen(observer, 6.0)
        subprocess.run(["osascript", "-e",
                        'tell application "Finder" to close front window'],
                       check=False, capture_output=True)
    finally:
        observer.close()

    check("Finder posted notifications", bool(arrived),
          ", ".join(name for _t, name in arrived[:6]) or
          "nothing arrived - the run loop or the Accessibility grant is the "
          "problem, not the applications below")
    return bool(arrived)


def survey_app(name: str, pid: int, seconds: float, prompt: str) -> bool:
    print(f"\n=== {name} (pid {pid}) ===")
    element = app_element(pid)

    before = node_count(element)
    evaluate_on_main_thread(lambda: element.set_manual_accessibility(True))
    time.sleep(1.5)
    after = node_count(element)
    print(f"  tree: {before} nodes -> {after} after "
          f"set_manual_accessibility(True)")
    check(f"{name} exposes a tree to post about", after > before or after > 60,
          "without this, 'no notifications' would be confounded with "
          "'no content'")

    observer = UIObserver(pid)
    check(f"observer installed on {name}", observer.active,
          f"accepted {len(observer.notifications)} of the requested names")
    if not observer.active:
        return False

    try:
        print(f"\n  >>> {prompt}")
        print(f"  >>> listening for {seconds:.0f}s ...")
        arrived = listen(observer, seconds)
    finally:
        observer.close()
        # Leave it as it was found: this switch changes how VS Code renders.
        evaluate_on_main_thread(lambda: element.set_manual_accessibility(False))

    if arrived:
        print("  arrived:")
        for elapsed, notification in arrived[:12]:
            print(f"    +{elapsed:>6.3f}s  {notification}")
    check(f"{name} posted anything at all", bool(arrived),
          f"{len(arrived)} notifications" if arrived else
          "nothing - matching the Safari result for web content")
    return bool(arrived)


def report() -> int:
    print()
    failed = [name for name, ok in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    for name in failed:
        print(f"  - {name}")
    print("\nA FAIL on the Electron row is a *finding*, not a broken pass, "
          "provided the Finder control passed.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--app", help="Electron application to watch")
    parser.add_argument("--seconds", type=float, default=20.0,
                        help="how long to listen for (default 20)")
    args = parser.parse_args()

    print(__doc__.split("\n\n")[0])
    if not AS.AXIsProcessTrusted():
        sys.exit("This interpreter has no Accessibility permission; nothing "
                 "below would mean anything.")

    running = running_apps()
    target = args.app
    if target is None:
        target = next((n for n in ELECTRON_CANDIDATES if n in running), None)
    if target is None or target not in running:
        sys.exit(f"No Electron application running (tried "
                 f"{', '.join(ELECTRON_CANDIDATES)}); pass --app.")

    init_key_names("mac", "ansi")
    loop = MacEventLoop()
    config = pathlib.Path(__file__).with_name("_ax_pass_config.py")
    config.write_text("def configure(keymap):\n    pass\n")
    keymap = Keymap(MacInputHook(), FakeFocusProvider(), "mac",
                    config_path=str(config), template_path=str(config))
    keymap.configure()
    # Installation, teardown and delivery all belong to the main run loop; the
    # probing below runs on a worker and hops back for every one of them.
    keymap.set_main_thread_dispatcher(loop.call_on_main_thread)

    status = {"code": 0}

    def worker():
        try:
            control_finder()
            survey_app(target, running[target], args.seconds,
                       prompt=(f"In {target}, make a change *inside the "
                               f"content* now - open the command palette, "
                               f"switch a tab, type into the editor."))
            status["code"] = report()
        except Exception:                                # noqa: BLE001
            traceback.print_exc()
            status["code"] = 1
        finally:
            loop.stop()

    threading.Thread(target=worker, daemon=True).start()
    try:
        loop.run()
    finally:
        config.unlink(missing_ok=True)
    return status["code"]


if __name__ == "__main__":
    sys.exit(main())
