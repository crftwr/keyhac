"""Package the authoring skill for upload to Claude Desktop.

Claude Desktop takes a skill as an uploaded bundle (Settings -> Skills -> Add
-> Upload skill), so the folder in keyhac/skills/ has to be zipped before it
can be installed. This writes dist/keyhac-action-authoring-skill.zip.

The version is stamped into SKILL.md on the way in. A skill describes one
version's API - it names methods and reports measured behaviour - and an
uploaded copy is a snapshot that cannot know the package underneath it changed.
Stamping is what makes that visible to a reader instead of silently wrong.
"""

import os
import pathlib
import re
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import keyhac  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "keyhac" / "skills" / "action-authoring"
OUTPUT = ROOT / "dist" / "keyhac-action-authoring-skill.zip"

#: An uploaded skill leaves the repository that carries its licence, so the
#: bundle carries its own copy - the same shape anthropics/skills uses, where
#: the frontmatter's `license` field points at a LICENSE.txt beside SKILL.md.
LICENSE = ROOT / "LICENSE"

#: Not shipped to the model: evals are for this repo's CI, and the fixture is a
#: deliberately-wrong action that has no business in a skill's context.
EXCLUDE = {"evals"}

STAMP = (f"\n\n---\n\nPackaged from Keyhac {keyhac.__version__}. This skill "
         f"describes that version's API; re-upload it after upgrading Keyhac.\n")

#: Resolved on the way into the bundle, the same treatment and for the same
#: reason as STAMP: the skill's prose links to files that live in the
#: repository rather than in the skill - the examples, the measurement tools -
#: and an uploaded copy is a snapshot that must keep pointing at the version it
#: describes. Written as a token rather than a literal because bump_version.py
#: rewrites exactly one line on purpose, and a second place to bump is a place
#: to drift.
VERSION_TOKEN = "{VERSION}"
TEXT_SUFFIXES = {".md", ".py", ".txt"}

#: A link into this repository must name a tag. `main` drifts out from under an
#: uploaded skill, and a mistyped token ships as literal text in a URL that
#: 404s quietly - the failure this is here to make loud, because the reader is
#: a model that will report what it fetched rather than that it fetched
#: nothing.
#: The ref sits in a different position per host, and the two are spelled out
#: rather than made optional: an optional group lets the engine decline to
#: consume `tree/` and report *that* as the ref, which is a false positive on
#: a correct URL.
MOVING_REF = re.compile(
    r"(?:github\.com/crftwr/keyhac/(?:tree|blob|raw)/"
    r"|raw\.githubusercontent\.com/crftwr/keyhac/)"
    r"(?!v\d)(?P<ref>[^/\s)]+)")


def main() -> int:
    if not SKILL.is_dir():
        print(f"ERROR: {SKILL} not found")
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    written = []
    problems = []
    with zipfile.ZipFile(OUTPUT, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(SKILL.rglob("*")):
            if path.is_dir() or any(part in EXCLUDE for part in path.parts):
                continue
            # SKILL.md at the zip root, not nested under a folder: the
            # uploader's stated requirement is that the archive "must contain a
            # SKILL.md file", and a nested one is a coin flip on how strictly
            # that is read. references/ keeps its relative position either way,
            # which is all SKILL.md's links need.
            name = str(path.relative_to(SKILL))
            if path.suffix in TEXT_SUFFIXES:
                text = path.read_text(encoding="utf-8").replace(
                    VERSION_TOKEN, keyhac.__version__)
                problems += [f"{name}: link to a moving ref {m['ref']!r} - "
                             f"the bundle must point at a tag"
                             for m in MOVING_REF.finditer(text)]
                if path.name == "SKILL.md":
                    text += STAMP
                bundle.writestr(name, text)
            else:
                bundle.write(path, name)
            written.append(name)

        if LICENSE.is_file():
            bundle.write(LICENSE, "LICENSE.txt")
            written.append("LICENSE.txt")

    if problems:
        # Remove it rather than leave a bundle that looks built: this one is
        # uploaded by hand, and a stale zip on disk is exactly what gets
        # uploaded when the build output has scrolled away.
        OUTPUT.unlink(missing_ok=True)
        for problem in problems:
            print(f"ERROR: {problem}")
        return 1

    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(written)} files, "
          f"{OUTPUT.stat().st_size // 1024} KB)")
    for name in written:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
