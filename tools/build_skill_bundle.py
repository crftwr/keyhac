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
import sys
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import keyhac  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKILL = ROOT / "keyhac" / "skills" / "action-authoring"
OUTPUT = ROOT / "dist" / "keyhac-action-authoring-skill.zip"

#: Not shipped to the model: evals are for this repo's CI, and the fixture is a
#: deliberately-wrong action that has no business in a skill's context.
EXCLUDE = {"evals"}

STAMP = (f"\n\n---\n\nPackaged from Keyhac {keyhac.__version__}. This skill "
         f"describes that version's API; re-upload it after upgrading Keyhac.\n")


def main() -> int:
    if not SKILL.is_dir():
        print(f"ERROR: {SKILL} not found")
        return 1
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    written = []
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
            if path.name == "SKILL.md":
                bundle.writestr(name, path.read_text(encoding="utf-8") + STAMP)
            else:
                bundle.write(path, name)
            written.append(name)

    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(written)} files, "
          f"{OUTPUT.stat().st_size // 1024} KB)")
    for name in written:
        print(f"  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
