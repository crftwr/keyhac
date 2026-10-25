"""Keyhac.app running this checkout's Keyhac, armed - in a macOS VM.

Not imported on the host. qa/guest.py copies this directory into the run's
stage and starts Keyhac.app in the guest with PYTHONPATH pointing at it, so
the app's Python imports this sitecustomize instead of the bundle's own.

Why the app and not `python qa/daemon.py`: the guest's Accessibility grant
belongs to Keyhac.app, and a bare interpreter would need one of its own,
given by hand in the guest. So the app runs main() as it always does, and
this file changes two things before it does:

- `keyhac` is imported from the stage (KEYHAC_QA_STAGE), the host's working
  tree copied there, not from the bundle's Resources - which the launcher
  puts first on sys.path after this file has run.
- keyhac.ui.tray is armed as qa/daemon.py arms it: the endpoint on with no
  timeout once the tray is up, and Keyhac quits when KEYHAC_QA_QUIT_FILE
  appears.
"""

import importlib.machinery
import os
import site
import sys

# What the bundle's own sitecustomize does, which this one shadows.
site.ENABLE_USER_SITE = False
if site.USER_SITE in sys.path:
    sys.path.remove(site.USER_SITE)

STAGE = os.environ.get("KEYHAC_QA_STAGE")


class _FromStage:
    """`keyhac` from the stage; keyhac.ui.tray armed as it loads."""

    def find_spec(self, name, path=None, target=None):
        if name == "keyhac":
            return importlib.machinery.PathFinder.find_spec(name, [STAGE])
        if name == "keyhac.ui.tray":
            spec = importlib.machinery.PathFinder.find_spec(name, path)
            if spec is not None:
                exec_module = spec.loader.exec_module

                def exec_and_arm(module):
                    exec_module(module)
                    sys.path.insert(0, os.path.join(STAGE, "qa"))
                    import daemon
                    daemon.arm_at_tray(module)

                spec.loader.exec_module = exec_and_arm
            return spec
        return None


if STAGE:
    sys.meta_path.insert(0, _FromStage())
