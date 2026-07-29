"""Locate and rewrite Keyhac's single version literal.

The version lives in exactly one place — ``keyhac/__init__.py``'s
``__version__`` — and every consumer derives it: pyproject.toml through
setuptools' dynamic ``version = { attr = "keyhac.__version__" }``, and the
M5 bundle builders will extract this same literal. Both release scripts go
through this module so they can never disagree about where the literal is.

The literal is read statically (regex, no ``import keyhac``) so the release
tooling never needs Keyhac's runtime deps merely to learn the version. That
is the same static approach setuptools itself uses to resolve the ``attr``
at build time, and it keeps these scripts runnable with a bare interpreter.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INIT = REPO_ROOT / "keyhac" / "__init__.py"

#: Anchored to the start of a line so nothing else in the file can match —
#: in particular the ``"__version__"`` entry in ``__all__`` and mentions in
#: comments. Deliberately does not touch the end of the line: a trailing
#: ``\s*$`` would cross the newline and eat the blank line after the literal.
PATTERN = re.compile(r'^(__version__\s*=\s*")([^"]+)(")', re.M)


def read_version() -> str:
    """Return the current version literal.

    Raises SystemExit if it is absent or duplicated — either means the single
    source of truth has been disturbed, which a release must not paper over.
    """
    found = PATTERN.findall(INIT.read_text(encoding="utf-8"))
    if len(found) != 1:
        raise SystemExit(
            f'ERROR: expected exactly one `__version__ = "..."` line in {INIT}, '
            f"found {len(found)}"
        )
    return found[0][1]


def write_version(new: str) -> str:
    """Rewrite the literal to ``new``. Returns the previous value."""
    old = read_version()
    # newline="" both ways: no newline translation, so the rewrite changes
    # exactly the version literal and never the file's line endings.
    with INIT.open(encoding="utf-8", newline="") as f:
        text = f.read()
    # A lambda replacement, so backslashes/group refs in `new` stay literal.
    new_text, count = PATTERN.subn(lambda m: m.group(1) + new + m.group(3), text)
    if count != 1:
        raise SystemExit(f"ERROR: expected 1 substitution in {INIT}, made {count}")
    with INIT.open("w", encoding="utf-8", newline="") as f:
        f.write(new_text)
    return old
