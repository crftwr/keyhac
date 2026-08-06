#!/usr/bin/env python3
"""
Keyhac Dependency Collection Script (shared, platform-agnostic)

Collects the *runtime* Python dependencies of Keyhac into a bundle's packages
directory. Shared by both bundle builds — ``macos_app/build.sh`` (into
``Resources/python_packages``) and ``windows_app/build.ps1`` (into
``Lib\\site-packages``); it makes no OS assumptions.

Rather than copying the entire virtual environment (which drags in test/build
tooling such as pytest), this resolves the dependency closure of the project's
declared dependencies — ``[project] dependencies`` in pyproject.toml — using
the installed package metadata and copies only those distributions.
Environment markers are honoured, so platform-specific requirements
(``pyobjc-framework-Cocoa; sys_platform == "darwin"``) are included/excluded
correctly for the build machine.

Each distribution is copied file-for-file from its ``RECORD`` (via
``importlib.metadata``), so its ``.dist-info`` — including the bundled license
text — travels with it. That ``.dist-info`` set is what
``tools/generate_third_party_notices.py`` later reads to build the bundle's
THIRD_PARTY_NOTICES file, so trimming to the real runtime set here also trims
the notices to what is actually shipped.

Note: PuiKit is installed editable during development and is copied into the
bundle separately by each build script (its editable shim would be broken on the
target machine), so it is intentionally not collected here. Pass
``--include-deps-of puikit`` to still pull in PuiKit's own runtime deps (e.g.
``numpy``, which the Windows Direct2D backend imports) without copying PuiKit.
"""

import argparse
import importlib.metadata as importlib_metadata
import os
import re
import shutil
import sys
import tomllib
from importlib.metadata import PackageNotFoundError
from pathlib import Path


def log_info(message):
    print(f"[INFO] {message}")


def log_error(message):
    print(f"[ERROR] {message}", file=sys.stderr)


def log_success(message):
    print(f"[SUCCESS] {message}")


def log_warning(message):
    print(f"[WARNING] {message}")


# Build/packaging tooling that must never be shipped even if it somehow appears
# in a dependency closure.
_SKIP_DISTRIBUTIONS = {
    "pip", "setuptools", "wheel", "distribute", "pkg-resources",
}

# Test suites a *runtime* distribution owns, and so cannot be dropped at the
# distribution level. PyObjCTest is pyobjc-core's own test suite: 560 of that
# distribution's files, and in the macOS bundle 16 MB, 140 .so files and their
# 140 .dSYM bundles - more than half of every per-binary codesign call, spent
# on code the app never imports.
_SKIP_TOP_LEVEL = {
    "PyObjCTest",
}


def _canonical(name):
    """PEP 503 canonical distribution name (for de-duplication/lookup)."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _parse_requirement(req_str):
    """
    Parse a requirement string into (canonical_name, marker).

    Uses ``packaging`` when available (present in the build venv) for correct
    marker/extra handling, and falls back to a minimal parser otherwise.
    ``marker`` is a packaging Marker or None.
    """
    try:
        from packaging.requirements import Requirement
        req = Requirement(req_str)
        return _canonical(req.name), req.marker
    except ImportError:
        # Minimal fallback: "<name>[extras] <specifiers> ; <marker>".
        spec, _, _marker = req_str.partition(";")
        name = re.split(r"[<>=!~;\[\( ]", spec.strip(), maxsplit=1)[0]
        return _canonical(name), None
    except Exception:
        # Unparseable requirement — treat as a bare name, no marker.
        name = re.split(r"[<>=!~;\[\( ]", req_str.strip(), maxsplit=1)[0]
        return _canonical(name), None


def _marker_satisfied(marker):
    """Evaluate an environment marker for the current build machine."""
    if marker is None:
        return True
    try:
        # Evaluated with no active extra, i.e. base install only.
        return bool(marker.evaluate())
    except Exception as exc:
        log_warning(f"Could not evaluate marker '{marker}' ({exc}); including dependency")
        return True


def read_seed_requirements(pyproject_file):
    """Read pyproject.toml, returning the marker-satisfied [project] dependencies."""
    seeds = []
    if not os.path.exists(pyproject_file):
        log_error(f"pyproject.toml not found: {pyproject_file}")
        return seeds

    log_info(f"Reading dependencies from: {pyproject_file}")
    with open(pyproject_file, "rb") as handle:
        pyproject = tomllib.load(handle)

    for req_str in pyproject.get("project", {}).get("dependencies", []):
        name, marker = _parse_requirement(req_str)
        if not name:
            continue
        if not _marker_satisfied(marker):
            log_info(f"Skipping (marker not satisfied for this platform): {req_str}")
            continue
        seeds.append(name)

    log_info(f"Found {len(seeds)} applicable top-level requirement(s)")
    return seeds


def resolve_runtime_closure(seed_names):
    """
    Resolve the full runtime dependency closure for *seed_names*.

    Returns a dict of canonical-name -> importlib.metadata.Distribution.
    """
    resolved = {}
    queue = list(seed_names)

    while queue:
        cname = _canonical(queue.pop(0))
        if cname in resolved or cname in _SKIP_DISTRIBUTIONS:
            continue

        try:
            dist = importlib_metadata.distribution(cname)
        except PackageNotFoundError:
            log_warning(f"Required distribution not installed, skipping: {cname}")
            continue

        resolved[cname] = dist

        for req_str in (dist.requires or []):
            dep_name, marker = _parse_requirement(req_str)
            if not dep_name or not _marker_satisfied(marker):
                continue  # extras and off-platform deps are gated out here
            if _canonical(dep_name) not in resolved:
                queue.append(dep_name)

    return resolved


def _should_skip_file(rel_path):
    """
    Skip files that must not be copied into the bundle:
      - anything outside site-packages (scripts under ../../bin, etc.);
      - editable-install shims (a .pth or __editable__ finder points at the
        developer checkout and is meaningless on the target machine);
      - the _SKIP_TOP_LEVEL test packages that ride along inside a runtime
        distribution.
    """
    parts = rel_path.parts
    if not parts or parts[0] == ".." or parts[0].startswith(".."):
        return True
    first = parts[0]
    if first.endswith(".pth") or first.startswith("__editable__"):
        return True
    if first in _SKIP_TOP_LEVEL:
        return True
    return False


def copy_distribution(dist, dest_dir):
    """
    Copy every file a distribution owns (per its RECORD) into *dest_dir*,
    preserving the layout relative to site-packages. Returns the number of
    files copied.
    """
    files = dist.files or []
    copied = 0
    fallback_used = False

    for entry in files:
        rel_path = Path(str(entry))
        if _should_skip_file(rel_path):
            continue
        src = Path(dist.locate_file(entry))
        if not src.exists():
            continue
        dest = dest_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied += 1

    if copied == 0:
        # No usable RECORD (e.g. odd install). Fall back to copying the
        # top-level import packages the distribution declares.
        fallback_used = True
        site_packages = Path(dist.locate_file(""))
        try:
            tops = dist.read_text("top_level.txt")
        except Exception:
            tops = None
        for top in (tops or "").splitlines():
            top = top.strip()
            if not top:
                continue
            for candidate in (site_packages / top, site_packages / f"{top}.py"):
                if candidate.is_dir():
                    dest = dest_dir / candidate.name
                    if dest.exists():
                        shutil.rmtree(dest)
                    shutil.copytree(candidate, dest)
                    copied += 1
                elif candidate.is_file():
                    shutil.copy2(candidate, dest_dir / candidate.name)
                    copied += 1

    if fallback_used and copied:
        log_warning(f"  {dist.metadata['Name']}: RECORD unavailable, copied by top_level.txt")

    return copied


def verify_pyobjc(dest_dir):
    """Light sanity check that the PyObjC runtime landed (macOS platform layer)."""
    if sys.platform != "darwin":
        return True
    dest_dir = Path(dest_dir)
    required = ["objc", "Cocoa", "Quartz"]
    missing = [m for m in required if not (dest_dir / m).exists()]
    if missing:
        log_error(f"PyObjC runtime incomplete, missing: {', '.join(missing)}")
        log_error("Ensure the pyobjc frameworks are installed in the venv: make install")
        return False
    log_success("PyObjC runtime present")
    return True


def collect_dependencies(pyproject_file, dest_dir, include_deps_of=None):
    """
    Resolve and copy the runtime dependency closure. Returns True on success.

    *include_deps_of* is a list of distribution names that are bundled by some
    other means (e.g. PuiKit, whose source the build scripts copy in
    separately) but whose declared runtime dependencies must still be
    collected. Their dependency closure is included, but the named
    distributions themselves are not copied. This is how the Windows bundle
    picks up PuiKit's ``numpy`` dependency (the win32 backend imports numpy;
    the macOS backend does not).
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    deps_only = {_canonical(n) for n in (include_deps_of or [])}

    seeds = read_seed_requirements(pyproject_file)
    if not seeds and not deps_only:
        log_error("No applicable requirements found; nothing to collect")
        return False

    if deps_only:
        log_info(f"Also including runtime deps of: {', '.join(sorted(deps_only))} "
                 f"(these distributions are copied in separately, not here)")

    log_info("Resolving runtime dependency closure...")
    resolved = resolve_runtime_closure(seeds + sorted(deps_only))
    log_info(f"Runtime closure: {len(resolved)} distribution(s)")

    total_files = 0
    failed = []
    for cname in sorted(resolved):
        if cname in deps_only:
            # Provided by another build step (e.g. PuiKit source); take its deps
            # from the closure but do not copy the distribution itself.
            log_info(f"Skipping copy of {cname} (bundled separately; deps kept)")
            continue
        dist = resolved[cname]
        name = dist.metadata["Name"]
        version = dist.version
        copied = copy_distribution(dist, dest_dir)
        if copied:
            log_info(f"Collected {name} {version} ({copied} files)")
            total_files += copied
        else:
            log_warning(f"No files copied for {name} {version}")
            failed.append(name)

    copied_dists = len(resolved) - sum(1 for c in resolved if c in deps_only)
    log_success(f"Collected {copied_dists} distributions, {total_files} files "
                f"(skipped test/build tooling)")

    if not verify_pyobjc(dest_dir):
        return False

    if failed:
        log_warning(f"Distributions with no files copied: {', '.join(failed)}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description="Collect Keyhac runtime Python dependencies for the app bundle"
    )
    parser.add_argument("--pyproject", default="pyproject.toml",
                        help="Path to pyproject.toml (seeds come from [project] dependencies)")
    parser.add_argument("--dest", required=True,
                        help="Destination directory for packages")
    parser.add_argument("--include-deps-of", action="append", default=[], metavar="NAME",
                        help="Collect the runtime deps of NAME (a package bundled "
                             "separately, e.g. puikit) without copying NAME itself. "
                             "Repeatable.")
    args = parser.parse_args()

    pyproject_file = os.path.abspath(args.pyproject)
    dest_dir = os.path.abspath(args.dest)

    log_info("Keyhac Dependency Collection Script")
    log_info(f"pyproject: {pyproject_file}")
    log_info(f"Destination directory: {dest_dir}")

    if collect_dependencies(pyproject_file, dest_dir, include_deps_of=args.include_deps_of):
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
