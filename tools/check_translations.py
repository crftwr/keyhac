"""Check the Japanese translations of README.md and doc/*.md (#283).

English is the source of truth; each translation sits next to its source as
``NAME.ja.md``. doc/dev/ is not translated, and neither are the pages in
``UNTRANSLATED``. ``make check-docs`` runs this and
reports:

- a source with no translation yet (a warning; ``--strict`` makes it an error
  once every page is in)
- a translation with no source, or a pair missing the "English | 日本語" line
  that is GitHub's only language switcher (written ``English \\| [日本語](…)``:
  kramdown, which builds the Pages site, reads a bare ``|`` as a table)
- a link in a translation to an English page that has a translation — it
  should point at the ``.ja.md``
- a ``#anchor`` in a translation that matches no heading of its target; the
  translated headings have Japanese anchors, so links copied from the English
  page break silently
- ``**bold**`` that will not close: a closing ``**`` or ``*`` after
  punctuation (``)``, a backtick, ``」``) must be followed by a space or
  punctuation, which Japanese text, having no spaces, often is not
- a translation whose source changed after it did (stale): by last commit,
  with uncommitted changes counting as newer than any commit

The exit status is 1 when anything other than a warning was found.

``--fix`` (``make fix-docs``) first does the mechanical part: it points links
in the translations at the other translations, maps their anchors to the
Japanese headings, and adds missing switcher lines. A translation therefore
copies its links from the English page as they are.
doc/dev/TRANSLATION.md holds the conventions and the glossary.
"""

import argparse
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: A markdown link or image target: ``[text](target)``. Targets with spaces or
#: a "title" part are not matched; the docs have none.
MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")

#: An HTML link target, as the docs write them: ``<a href="target">``.
HTML_HREF = re.compile(r'<a\s[^>]*href="([^"]+)"')

#: An explicit HTML anchor a link may aim at: ``<a id="x">``, ``<a name="x">``.
HTML_ID = re.compile(r'<a\s[^>]*(?:id|name)="([^"]+)"')

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
INLINE_CODE = re.compile(r"`+[^`]*`+")
SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")

#: A run of ``*`` emphasis delimiters.
STARS = re.compile(r"\*+")


#: doc/*.md pages that stay English only: the API references are generated
#: from the docstrings (`make api-reference`), and the template page is
#: Liquid that renders keyhac/_config.py on the Pages site.
UNTRANSLATED = {"config-api.md", "action-api.md", "config-py-template.md"}


def sources() -> list[Path]:
    """README.md and doc/*.md — the pages that get a translation."""
    pages = [REPO_ROOT / "README.md"] + sorted((REPO_ROOT / "doc").glob("*.md"))
    return [p for p in pages if not is_translation(p) and p.name not in UNTRANSLATED]


def is_translation(path: Path) -> bool:
    return path.name.endswith(".ja.md")


def translation_of(source: Path) -> Path:
    return source.with_name(source.name[:-3] + ".ja.md")


def source_of(translation: Path) -> Path:
    return translation.with_name(translation.name[:-6] + ".md")


def prose_lines(text: str) -> list[str]:
    """The lines outside fenced code blocks."""
    lines, fenced = [], False
    for line in text.splitlines():
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(line)
    return lines


def links(text: str) -> list[str]:
    """Every link target in the prose, inline code spans left out."""
    found = []
    for line in prose_lines(text):
        code = [m.span() for m in INLINE_CODE.finditer(line)]
        for pattern in (MD_LINK, HTML_HREF):
            found += [m.group(1) for m in pattern.finditer(line)
                      if not any(a <= m.start() < b for a, b in code)]
    return found


def slug(heading: str) -> str:
    """The anchor GitHub gives a heading: rendered text, lowercased, with
    everything but letters, digits, ``_``, ``-`` and spaces dropped and the
    spaces turned into hyphens. Japanese letters survive; ``「」`` do not."""
    text = re.sub(r"<[^>]+>", "", heading)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = text.replace("`", "").replace("*", "")
    text = re.sub(r"[^\w\- ]", "", text.lower())
    return text.replace(" ", "-")


def heading_slugs(path: Path) -> list[str]:
    """The anchors of ``path``'s headings in order, duplicates numbered as
    GitHub numbers them (``name``, ``name-1``, ...)."""
    found, seen = [], {}
    for line in prose_lines(path.read_text(encoding="utf-8")):
        m = HEADING.match(line)
        if not m:
            continue
        base = slug(m.group(2))
        n = seen.get(base, 0)
        seen[base] = n + 1
        found.append(base if n == 0 else f"{base}-{n}")
    return found


def anchors(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    """The anchors a link into ``path`` may use: its headings' and any
    explicit ``<a id>``."""
    if path not in cache:
        found = set(heading_slugs(path))
        for line in prose_lines(path.read_text(encoding="utf-8")):
            found.update(HTML_ID.findall(line))
        cache[path] = found
    return cache[path]


def punctuation(ch: str) -> bool:
    """A Unicode punctuation character as CommonMark counts one (P or S)."""
    return unicodedata.category(ch)[0] in "PS"


def stuck_emphasis(line: str) -> bool:
    """Whether a ``*`` / ``**`` pair in ``line`` will render as literal stars.

    The runs of each length are taken to pair up left to right. CommonMark
    lets a run open only before a letter or after a space or punctuation, and
    close only after a letter or before a space or punctuation. Japanese has
    no spaces, so ``**[設定](x.md)**を`` stays literal where
    ``**[設定](x.md)** を`` is bold.
    """
    line = INLINE_CODE.sub("`", line).replace("\\*", "")
    seen: dict[int, int] = {}
    for m in STARS.finditer(line):
        before = line[m.start() - 1] if m.start() else " "
        after = line[m.end()] if m.end() < len(line) else " "
        if before.isspace() and after.isspace():
            continue  # a bullet or a lone star, not emphasis
        n = len(m.group())
        opening = seen.get(n, 0) % 2 == 0
        seen[n] = seen.get(n, 0) + 1
        if opening:
            flanking = not after.isspace() and (
                not punctuation(after) or before.isspace() or punctuation(before))
        else:
            flanking = not before.isspace() and (
                not punctuation(before) or after.isspace() or punctuation(after))
        if not flanking:
            return True
    return False


def changed_at(path: Path) -> float:
    """When ``path`` last changed: its last commit's time, or infinity while
    it has uncommitted changes. Two pages edited together therefore never
    look stale, and an English edit with no Japanese one does at once."""
    rel = str(path.relative_to(REPO_ROOT))
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", rel],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    if dirty.strip():
        return float("inf")
    stamp = subprocess.run(
        ["git", "log", "-1", "--format=%ct", "--", rel],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    return float(stamp.strip() or 0)


def check(strict: bool) -> int:
    errors: list[str] = []
    warnings: list[str] = []
    anchor_cache: dict[Path, set[str]] = {}

    def rel(path: Path) -> str:
        return str(path.relative_to(REPO_ROOT))

    translations = [REPO_ROOT / "README.ja.md"] + sorted((REPO_ROOT / "doc").glob("*.ja.md"))
    translations = [t for t in translations if t.exists()]

    for source in sources():
        if not translation_of(source).exists():
            warnings.append(f"{rel(source)}: no translation yet")

    for ja in translations:
        en = source_of(ja)
        if not en.exists():
            errors.append(f"{rel(ja)}: no English source {en.name}")
            continue
        en_text = en.read_text(encoding="utf-8")
        ja_text = ja.read_text(encoding="utf-8")

        en_line, ja_line = switcher_lines(en, ja)
        if en_line not in en_text:
            errors.append(f"{rel(en)}: missing the line '{en_line}'")
        if ja_line not in ja_text:
            errors.append(f"{rel(ja)}: missing the line '{ja_line}'")

        for target in links(ja_text):
            if SCHEME.match(target):
                continue
            file_part, _, anchor = target.partition("#")
            dest = (ja.parent / file_part).resolve() if file_part else ja
            if file_part.endswith(".md") and not is_translation(dest):
                if dest != en and translation_of(dest).exists():
                    errors.append(
                        f"{rel(ja)}: links to {file_part}; use "
                        f"{translation_of(Path(file_part)).as_posix()}")
                    continue
            if anchor and dest.suffix == ".md" and dest.exists():
                if anchor not in anchors(dest, anchor_cache):
                    errors.append(f"{rel(ja)}: no heading for #{anchor} in {rel(dest)}")

        for line in prose_lines(ja_text):
            if stuck_emphasis(line):
                errors.append(f"{rel(ja)}: emphasis will not close (add a space after it): "
                              f"{line.strip()[:60]}")

        if changed_at(en) > changed_at(ja):
            errors.append(f"{rel(ja)}: stale — {en.name} changed after it")

    for line in warnings:
        print(f"{'error' if strict else 'warning'}: {line}")
    for line in errors:
        print(f"error: {line}")
    failed = bool(errors) or (strict and bool(warnings))
    print(f"{len(translations)} translated, {len(warnings)} untranslated, "
          f"{len(errors)} error(s)")
    return 1 if failed else 0


def switcher_lines(en: Path, ja: Path) -> tuple[str, str]:
    """The "English | 日本語" line of each page of a pair."""
    return f"English \\| [日本語]({ja.name})", f"[English]({en.name}) \\| 日本語"


def with_switcher(text: str, line: str) -> str:
    """``text`` with ``line`` as its own paragraph under the ``#`` title."""
    lines = text.split("\n")
    head, rest = (lines[:1], lines[1:]) if lines[0].startswith("# ") else ([], lines)
    while rest and not rest[0].strip():
        rest = rest[1:]
    return "\n".join(head + ([""] if head else []) + [line, ""] + rest)


def fix() -> int:
    """Point the translations' links at the other translations.

    A translation is written with its links copied from the English page;
    this rewrites ``X.md`` to ``X.ja.md`` wherever that exists, and turns
    each English ``#anchor`` into the Japanese heading at the same position,
    so translations must keep their source's headings one for one. It also
    adds a missing "English | 日本語" line to either page of a pair.
    """
    problems = []
    for ja in [REPO_ROOT / "README.ja.md"] + sorted((REPO_ROOT / "doc").glob("*.ja.md")):
        en = source_of(ja)
        if not ja.exists() or not en.exists():
            continue
        en_line, ja_line = switcher_lines(en, ja)
        en_text = en.read_text(encoding="utf-8")
        if en_line not in en_text:
            en.write_text(with_switcher(en_text, en_line), encoding="utf-8")
            print(f"{en.relative_to(REPO_ROOT)}: added the switcher line")

        def map_anchor(source: Path, target: Path, anchor: str) -> str:
            if not anchor or anchor in anchors(target, {}):
                return anchor
            en_slugs, ja_slugs = heading_slugs(source), heading_slugs(target)
            if anchor in en_slugs and len(en_slugs) == len(ja_slugs):
                return ja_slugs[en_slugs.index(anchor)]
            problems.append(f"{ja.relative_to(REPO_ROOT)}: cannot map #{anchor} "
                            f"into {target.relative_to(REPO_ROOT)}")
            return anchor

        def retarget(target: str) -> str:
            if SCHEME.match(target):
                return target
            file_part, hashmark, anchor = target.partition("#")
            if not file_part:
                return "#" + map_anchor(en, ja, anchor) if anchor else target
            dest = (ja.parent / file_part).resolve()
            if not file_part.endswith(".md") or dest == en:
                return target
            if is_translation(dest):
                source, translated, new_file = source_of(dest), dest, file_part
            elif translation_of(dest).exists():
                source, translated = dest, translation_of(dest)
                new_file = file_part[:-3] + ".ja.md"
            else:
                return target
            if not source.exists():
                return target
            return new_file + (hashmark + map_anchor(source, translated, anchor) if anchor else "")

        def rewrite(line: str) -> str:
            # A link inside a code span is code; one whose text holds a code
            # span is still a link, so only where the match starts counts.
            code = [m.span() for m in INLINE_CODE.finditer(line)]

            def repl(m: re.Match, close: str) -> str:
                if any(a <= m.start() < b for a, b in code):
                    return m.group(0)
                return m.group(0)[:m.start(1) - m.start()] + retarget(m.group(1)) + close

            line = MD_LINK.sub(lambda m: repl(m, ")"), line)
            return HTML_HREF.sub(lambda m: repl(m, '"'), line)

        text = ja.read_text(encoding="utf-8")
        out, fenced = [], False
        for line in text.split("\n"):
            if FENCE.match(line):
                fenced = not fenced
            if fenced or FENCE.match(line) or line == ja_line:
                out.append(line)
                continue
            out.append(rewrite(line))
        new = "\n".join(out)
        if ja_line not in new:
            new = with_switcher(new, ja_line)
        if new != text:
            ja.write_text(new, encoding="utf-8")
            print(f"{ja.relative_to(REPO_ROOT)}: links updated")
    for line in problems:
        print(f"error: {line}")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true",
                        help="treat a page with no translation as an error")
    parser.add_argument("--fix", action="store_true",
                        help="point links at translations and add switcher lines, then check")
    args = parser.parse_args()
    if args.fix and fix():
        return 1
    return check(args.strict)


if __name__ == "__main__":
    sys.exit(main())
