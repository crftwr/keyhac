"""Fail-fast checks run before `make tag` mutates anything.

`make tag` does one irreversible thing: it pushes a tag that every later
release-* target names. This script runs FIRST and refuses to tag unless every
precondition holds, so a dirty tree, a stale checkout or a duplicate version
fails loudly *before* any commit, tag or push happens. It collects all problems
and reports them together rather than stopping at the first.

Publishing is not checked here — each release-* target guards its own upload.
In particular the PyPI upload's irreversibility (a version can never be reused)
is guarded by `make release-whl`, which requires HEAD to sit on the release tag.

Warnings (printed, non-fatal) cover what a checkout can't decide for you:
whether the PuiKit build this release depends on is actually published, and
whether `gh` — needed by every target after this one, but not by `make tag`
itself — is ready to go.

Usage: release_preflight.py <new-version>
"""

import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from _version_source import INIT, read_version

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

# X.Y.Z core, with an optional PEP 440-ish pre/post/dev suffix (e.g. 2.0.0a1).
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[.-]?(?:a|b|rc|alpha|beta|post|dev)\d+)?$")


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=REPO_ROOT)


def core(version: str) -> tuple[int, int, int]:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", version)
    return tuple(int(g) for g in m.groups()) if m else (0, 0, 0)


def editable_puikit() -> str | None:
    """Return the checkout path if PuiKit is installed editable, else None.

    Keyhac's Makefile installs PuiKit editable from PUIKIT_DIR for
    co-development, but the sdist/wheel this release builds depends on the
    *published* PuiKit (the ``puikit>=...`` pin in pyproject.toml). If Keyhac
    has come to rely on unreleased PuiKit changes, the release would install
    for nobody but you.
    """
    try:
        import importlib.metadata as md

        raw = md.distribution("puikit").read_text("direct_url.json")
    except Exception:
        return None
    if not raw:
        return None
    info = json.loads(raw)
    return info.get("url") if info.get("dir_info", {}).get("editable") else None


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: release_preflight.py <new-version>", file=sys.stderr)
        return 2
    new = sys.argv[1]
    problems: list[str] = []
    warnings: list[str] = []

    # 1. Version string is well-formed.
    if not VERSION_RE.match(new):
        problems.append(f"VERSION '{new}' is not X.Y.Z (optionally +a1/rc1/.post1/…)")

    # 2. New version is strictly ahead of the current one (no re-release /
    #    rollback). Read from the single source of truth, keyhac/__init__.py's
    #    __version__.
    current = read_version()
    if new == current:
        problems.append(f"VERSION {new} equals the current version in {INIT.name}")
    elif core(new) < core(current):
        problems.append(f"VERSION {new} is older than the current {current}")

    # 2b. pyproject.toml still DERIVES the version rather than hardcoding it.
    #     A static [project].version would silently win over __version__ at build
    #     time, so the wheel could ship a different number than the bundles embed.
    pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    if "version" in pyproject.get("project", {}):
        problems.append(
            "pyproject.toml has a static [project].version — it must stay in "
            'dynamic = ["version"] so the build derives keyhac.__version__'
        )
    elif "version" not in pyproject.get("project", {}).get("dynamic", []):
        problems.append('pyproject.toml no longer declares dynamic = ["version"]')

    # 3. On the main branch.
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        problems.append(f"on branch '{branch}', not 'main'")

    # 4. Working tree is clean.
    if git("status", "--porcelain").stdout.strip():
        problems.append("working tree is dirty — commit or stash first")

    # 5. The tag does not already exist.
    if git("tag", "--list", f"v{new}").stdout.strip():
        problems.append(f"tag v{new} already exists")

    # 6. Local main is not behind its upstream (a non-fast-forward push would
    #    otherwise fail mid-release). Skipped cleanly if there is no upstream.
    git("fetch", "--quiet")
    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream.returncode == 0:
        behind = git("rev-list", "--count", "HEAD..@{u}").stdout.strip()
        if behind and behind != "0":
            problems.append(
                f"local branch is {behind} commit(s) behind {upstream.stdout.strip()} — pull first"
            )

    # 7. Non-fatal: `gh` is usable. `make tag` is pure git + version work and does
    #    not touch GitHub, so a missing `gh` must not block it — but every target
    #    that follows (release-github and the release-<artifact> ones) needs it,
    #    so surfacing it here saves finding out after the tag is public.
    if shutil.which("gh") is None:
        warnings.append(
            "`gh` not found — install the GitHub CLI and run `gh auth login` "
            "before `make release-github`"
        )
    elif subprocess.run(["gh", "auth", "status"], capture_output=True).returncode != 0:
        warnings.append("`gh` is not authenticated — run `gh auth login` before `make release-github`")

    # 8. Non-fatal: PuiKit is a separate repo with its own release cycle.
    puikit_dir = editable_puikit()
    if puikit_dir:
        warnings.append(
            f"PuiKit is installed editable from {puikit_dir}; the release depends on "
            "the PyPI build (the puikit pin in pyproject.toml). Release PuiKit first "
            "if Keyhac needs unreleased changes from it."
        )

    if problems:
        print("Release preflight failed:", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return 1

    for w in warnings:
        print(f"  ! {w}")
    print(f"Preflight OK: {current} -> {new} on {branch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
