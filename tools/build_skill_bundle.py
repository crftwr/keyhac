"""Package the skills for upload to Claude Desktop.

Claude Desktop takes a skill as an uploaded bundle (Settings -> Skills -> Add
-> Upload skill), so each folder in keyhac/skills/ has to be zipped before it
can be installed. This writes one zip per skill into dist/.

**One bundle each, not one bundle holding both.** The uploader takes an archive
with a SKILL.md at its root, so two skills cannot share one; and they are
separately useful - somebody configuring key bindings has no use for the rules
about driving another application's accessibility tree, and carrying them would
cost context on every conversation that loads it.

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
SKILLS = ROOT / "keyhac" / "skills"

#: An uploaded skill leaves the repository that carries its licence, so the
#: bundle carries its own copy - the same shape anthropics/skills uses, where
#: the frontmatter's `license` field points at a LICENSE.txt beside SKILL.md.
LICENSE = ROOT / "LICENSE"

#: Copied in rather than restated by hand. The signatures are the half of the
#: skill's API knowledge that *drifts* - three hand-edits in one day when
#: exceptions were added and a tool was renamed - and they are already
#: generated from the docstrings, checked by `make api-reference-check`. So the
#: generated file travels with the bundle and references/practice.md keeps only
#: what cannot be generated: what each mechanism costs, and which of them fail
#: silently. It also makes the pointer true: practice.md used to cite a path
#: the uploaded skill did not carry.
#: Per skill, because they document different halves of the API and neither
#: reader needs the other's.  The key-table skill also carries the *guide*
#: rather than restating it: doc/configuration.md is the authoritative
#: description of key expressions and table conditions, already written and
#: already maintained, and a skill that paraphrased it would be a second copy
#: to drift - which is how references/quirks.md came to ship a false rule.
#: `config-api.md` travels with *both*, which is not duplication for its own
#: sake: an uploaded skill cannot reach the other one, and telling a model to
#: fetch a version-tagged URL is what the action skill used to do. It failed in
#: measurement - the model shelled out to `pbpaste` rather than use
#: `keymap.clipboard.get_text()`, because a URL that never entered the
#: conversation cannot be fetched - measured in a real authoring session.
GENERATED = {
    "keyhac-action-authoring": [
        (ROOT / "doc" / "action-api.md", "references/action-api.md"),
        (ROOT / "doc" / "config-api.md", "references/config-api.md"),
    ],
    "keyhac-key-table-configuration": [
        (ROOT / "doc" / "config-api.md", "references/config-api.md"),
        (ROOT / "doc" / "configuration.md", "references/configuration.md"),
    ],
}

#: Not shipped to the model: evals are for this repo's CI, and the fixture is a
#: deliberately-wrong action that has no business in a skill's context.  A
#: README.md is addressed to whoever is working on Keyhac - the one under
#: keyhac-key-table-configuration/references/ exists to explain why that folder
#: is empty in the source tree, which is a question the model will never have.
EXCLUDE = {"evals", "README.md"}

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


def build(skill: pathlib.Path) -> tuple[list[str], list[str], pathlib.Path]:
    """Zip one skill folder. Returns (written names, problems, output path)."""
    # The version is in the name as well as in the stamp inside. A downloaded
    # bundle is a file in somebody's Downloads folder for months: two of them
    # are indistinguishable without opening the zip, and the second one
    # silently overwrites the first. Keeping the `-skill.zip` tail means the
    # release globs still match on the suffix rather than on the whole shape.
    output = ROOT / "dist" / f"{skill.name}-{keyhac.__version__}-skill.zip"
    output.parent.mkdir(parents=True, exist_ok=True)

    written = []
    problems = []
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(skill.rglob("*")):
            if path.is_dir() or any(part in EXCLUDE for part in path.parts):
                continue
            # SKILL.md at the zip root, not nested under a folder: the
            # uploader's stated requirement is that the archive "must contain a
            # SKILL.md file", and a nested one is a coin flip on how strictly
            # that is read. references/ keeps its relative position either way,
            # which is all SKILL.md's links need.
            name = str(path.relative_to(skill))
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

        for source, name in GENERATED.get(skill.name, []):
            if not source.is_file():
                problems.append(f"{source.relative_to(ROOT)} is missing - run "
                                f"'make api-reference'")
                continue
            text = source.read_text(encoding="utf-8").replace(
                VERSION_TOKEN, keyhac.__version__)
            problems += [f"{name}: link to a moving ref {m['ref']!r} - "
                         f"the bundle must point at a tag"
                         for m in MOVING_REF.finditer(text)]
            bundle.writestr(name, text)
            written.append(name)

        if LICENSE.is_file():
            bundle.write(LICENSE, "LICENSE.txt")
            written.append("LICENSE.txt")

    return written, problems, output


def main() -> int:
    skills = sorted(p for p in SKILLS.iterdir()
                    if p.is_dir() and (p / "SKILL.md").is_file())
    if not skills:
        print(f"ERROR: no skills under {SKILLS}")
        return 1

    failed = False
    for skill in skills:
        written, problems, output = build(skill)
        if problems:
            # Remove it rather than leave a bundle that looks built: these are
            # uploaded by hand, and a stale zip on disk is exactly what gets
            # uploaded when the build output has scrolled away.
            output.unlink(missing_ok=True)
            for problem in problems:
                print(f"ERROR: {skill.name}: {problem}")
            failed = True
            continue
        print(f"Wrote {output.relative_to(ROOT)} ({len(written)} files, "
              f"{output.stat().st_size // 1024} KB)")
        for name in written:
            print(f"  {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
