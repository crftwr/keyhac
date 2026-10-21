"""``keyhac deps ...`` -- sync the Python packages ``~/.keyhac/requirements.txt``
declares (#272), from a shell and without starting Keyhac.

A thin layer over :mod:`keyhac.core.python_packages`, the same code the tray
menu's Sync Python Packages runs inside the app. Reachable where ``keyhac`` is
a command (a pip, pipx or uv install); the app bundles hand their launcher a
fixed argv, so there the tray menu is the way in.
"""

import argparse

from keyhac.core import paths
from keyhac.core import python_packages as pp


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="keyhac deps",
        description="Install the Python packages listed in "
                    "~/.keyhac/requirements.txt where Keyhac can import them.")
    parser.add_argument("-c", "--config", metavar="PATH", default=None,
                        help="config file path; requirements.txt and "
                             "site-packages/ sit beside it")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="make site-packages/ match requirements.txt "
                   "(reaches the network)")
    sub.add_parser("status", help="say whether a sync is needed (offline)")
    return parser


def main(argv: list[str] | None = None, layout: pp.Layout | None = None) -> int:
    args = _parser().parse_args(argv)
    layout = layout or pp.Layout(paths.resolve(args.config).data_dir)

    if args.command == "status":
        status = pp.check(layout)
        if status.state == pp.UNUSED:
            print(f"nothing declared in {layout.requirements}")
            return 0
        if status.state == pp.IN_SYNC:
            print(f"in sync: {layout.target}")
            return 0
        print(status.message)
        return 1

    try:
        result = pp.sync(layout, log=print)
    except pp.SyncError as e:
        print(f"sync failed: {e}")
        return 1
    for line in result.installed:
        print(f"installed: {line}")
    if result.pruned:
        print(f"provided by Keyhac itself: {', '.join(result.pruned)}")
    if result.pending_restart:
        print(f"{layout.target} is in use; the new packages take its place "
              "when Keyhac next starts")
    else:
        print(f"in sync: {layout.target}")
    return 0
