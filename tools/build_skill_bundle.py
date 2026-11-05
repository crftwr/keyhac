"""Package the skill for upload to Claude Desktop.

Claude Desktop takes a skill as an uploaded bundle (Settings -> Skills -> Add
-> Upload skill), so the folder in keyhac/skills/ has to be zipped before it
can be installed. This writes one zip per skill folder into dist/ - and there
is one folder, `keyhac` (issue #164).

**One skill, not one per job.** There were two, key-table configuration and
action authoring, kept apart so that a conversation about key bindings would
not carry the rules for driving another application's accessibility tree. That
cost was never real: a loaded skill brings its SKILL.md and nothing else, and
`references/` is read only when SKILL.md sends the model there. So SKILL.md is
a short router and each job's knowledge is a reference, which keeps the
context cost where it was while removing what two skills did cost - two
uploads, one of them easy to forget, and a task that crosses the line
("automate this and put it on a key") needing both installed and each knowing
the other existed.

The version is stamped into SKILL.md on the way in. A skill describes one
version's API - it names methods and reports measured behaviour - and an
uploaded copy is a snapshot that cannot know the package underneath it changed.
Stamping is what makes that visible to a reader instead of silently wrong.

Off a release tag, or with uncommitted changes, the stamp is a dev version
(`2.7.0+dev.g<sha>`), because `__version__` still names the last release while
the prose may describe newer API (#280). SKILL.md's version check compares major
and minor only, so such a bundle passes it against the same release. That is
accepted: dev bundles are for developers and quality-loop.
"""

import os
import pathlib
import re
import subprocess
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
#: The skill also carries the *guide* rather than restating it:
#: doc/configuration.md is the authoritative description of key expressions
#: and table conditions, already written and already maintained, and a skill
#: that paraphrased it would be a second copy to drift - which is how
#: references/quirks.md came to ship a false rule. All three travel in the
#: bundle rather than as URLs to fetch: telling a model to fetch a
#: version-tagged URL is what the action skill used to do, and it failed in
#: measurement - the model shelled out to `pbpaste` rather than use
#: `keymap.clipboard.get_text()`, because a URL that never entered the
#: conversation cannot be fetched.
GENERATED = {
    "keyhac": [
        (ROOT / "doc" / "action-api.md", "references/action-api.md"),
        (ROOT / "doc" / "config-api.md", "references/config-api.md"),
        (ROOT / "doc" / "configuration.md", "references/configuration.md"),
    ],
}

#: Not shipped to the model: a README.md is addressed to whoever is working on
#: Keyhac - the one under references/ explains why three references are
#: missing from the source tree, which is a question the model will never
#: have.  (The evals used to be excluded here too; they moved out of the skill
#: tree to the clean room in issue #190, so the folder is the skill and no
#: more.)
#: `__pycache__` because a folder that ever held Python leaves one behind
#: untracked: the evals' bytecode outlived their move and shipped in every
#: bundle since, the scoring rules of the clean room included.
EXCLUDE = {"README.md", "__pycache__"}

STAMP = ("\n\n---\n\nPackaged from Keyhac {VERSION}. This skill "
         "describes that version's API; re-upload it after upgrading Keyhac.\n")

#: Resolved on the way into the bundle, the same treatment and for the same
#: reason as STAMP: the skill's prose links to files that live in the
#: repository rather than in the skill - the examples, the measurement tools -
#: and an uploaded copy is a snapshot that must keep pointing at the version it
#: describes. Written as a token rather than a literal because bump_version.py
#: rewrites exactly one line on purpose, and a second place to bump is a place
#: to drift.
VERSION_TOKEN = "{VERSION}"
#: How the skill's links spell a tag. A dev version is not a tag, so links get
#: the commit instead - see identity().
TAG_TOKEN = "v{VERSION}"
TEXT_SUFFIXES = {".md", ".py", ".txt"}

#: A link into this repository must name a tag or a commit. `main` drifts out from under an
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
    r"(?!v\d|[0-9a-f]{40}[/\s)])(?P<ref>[^/\s)]+)")


def _git(*args: str) -> str | None:
    """One git answer about this tree, or None when it cannot give one."""
    # Checked first because git would otherwise search upward: an unpacked sdist
    # inside somebody's repository would report that repository's state.
    if not (ROOT / ".git").exists():
        return None
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], check=True,
                              capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def identity(git=_git) -> tuple[str, str]:
    """The version to stamp, and the ref the skill's links should name.

    With no git to ask (a build from an sdist) this is the plain version, as it
    was before dev versions. A commit named in a link exists on GitHub only once
    pushed, which is accepted for a dev bundle.
    """
    version = keyhac.__version__
    tag = f"v{version}"
    head = git("rev-parse", "HEAD")
    if not head:
        return version, tag
    status = git("status", "--porcelain")
    if head == git("rev-parse", "--verify", "--quiet", f"{tag}^{{commit}}") \
            and status == "":
        return version, tag
    # `.dirty` because the sha alone would claim the bundle is that commit.
    dirty = "" if status == "" else ".dirty"
    return f"{version}+dev.g{head[:9]}{dirty}", head


def _resolve_tokens(text: str, version: str, ref: str) -> str:
    return text.replace(TAG_TOKEN, ref).replace(VERSION_TOKEN, version)


def build(skill: pathlib.Path) -> tuple[list[str], list[str], pathlib.Path]:
    """Zip one skill folder. Returns (written names, problems, output path)."""
    # The version is in the name as well as in the stamp inside. A downloaded
    # bundle is a file in somebody's Downloads folder for months: two of them
    # are indistinguishable without opening the zip, and the second one
    # silently overwrites the first. Keeping the `-skill.zip` tail means the
    # release globs still match on the suffix rather than on the whole shape.
    version, ref = identity()
    output = ROOT / "dist" / f"{skill.name}-{version}-skill.zip"
    output.parent.mkdir(parents=True, exist_ok=True)

    written = []
    problems = []
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(skill.rglob("*")):
            if path.is_dir() or path.suffix == ".pyc" \
                    or any(part in EXCLUDE for part in path.parts):
                continue
            # SKILL.md at the zip root, not nested under a folder: the
            # uploader's stated requirement is that the archive "must contain a
            # SKILL.md file", and a nested one is a coin flip on how strictly
            # that is read. references/ keeps its relative position either way,
            # which is all SKILL.md's links need.
            name = path.relative_to(skill).as_posix()
            if path.suffix in TEXT_SUFFIXES:
                text = _resolve_tokens(path.read_text(encoding="utf-8"),
                                       version, ref)
                problems += [f"{name}: link to a moving ref {m['ref']!r} - "
                             f"the bundle must point at a tag or commit"
                             for m in MOVING_REF.finditer(text)]
                if path.name == "SKILL.md":
                    text += _resolve_tokens(STAMP, version, ref)
                bundle.writestr(name, text)
            else:
                bundle.write(path, name)
            written.append(name)

        for source, name in GENERATED.get(skill.name, []):
            if not source.is_file():
                problems.append(f"{source.relative_to(ROOT)} is missing - run "
                                f"'make api-reference'")
                continue
            text = _resolve_tokens(source.read_text(encoding="utf-8"),
                                   version, ref)
            problems += [f"{name}: link to a moving ref {m['ref']!r} - "
                         f"the bundle must point at a tag or commit"
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
