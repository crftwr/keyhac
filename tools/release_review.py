"""Review what is about to ship against CLAUDE.md's rules.

    .venv/bin/python tools/release_review.py                 # last tag..origin/main
    .venv/bin/python tools/release_review.py --base v2.7.0 --head HEAD

The keyhac-release skill runs this (``make release-review``) in step 2, before
tagging. It reports and fixes nothing: the user decides what to fix while the
tag is still retractable. Keyhac has no CI and most changes land through the
maintainer's own sessions, so one pass over everything about to ship is where
drift across many small changes shows - and the public API compared across
the whole release sees what no single PR shows (an entry added and removed
again inside the release is fine; one that shipped at the last tag and is gone
now is not).

Two kinds of output, both from the same diff:

- **Findings** - CLAUDE.md's mechanical rules, judged the same way every time,
  on the lines the range added: no OS module imported under ``keyhac/core/``,
  no AppKit / Win32 under ``keyhac/ui/``, every Windows DLL function called
  with its ``restype`` declared, no camelCase in the public API - plus the
  public API compared tag to head (an entry or a parameter removed, a
  parameter added with no default) and the existing generated-doc and
  translation checks. Exit status 1 when there are any.
- **For review** - pointers for the judgment calls: new modules, new files at
  the repo root or tests outside ``tests/``, new or moved dependencies,
  user-facing docstrings (read from the generated API reference) naming
  keyhac-mac / keyhac-win - migration help, or porting history that belongs
  in a comment - core reaching into a platform module, the hooks (whose
  subtle behaviours doc/dev/design-notes.md lists), UINode's identity and
  lifetime (settled until the next major), the MCP tool surface, and the API
  additions the release note and the version bump should account for. They
  never set the exit status.

The generated-doc and translation checks run the existing tools on the
working tree, which should be a checkout of ``--head`` (the output says so
when it is not).
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Modules that are OS access, which keyhac/core/ reaches only through
#: keyhac/platform/ and keyhac/ui/ only through PuiKit.
OS_MODULES = {
    "ctypes", "winreg", "msvcrt", "_winapi", "win32api", "win32con", "win32gui",
    "objc", "AppKit", "Foundation", "Quartz", "Cocoa", "ApplicationServices",
    "CoreFoundation", "HIServices",
}

#: The generated user documentation, one entry per heading.
API_DOCS = ("doc/config-api.md", "doc/action-api.md")
API_HEADING = re.compile(r"^#{2,4} <kbd>(\w+)</kbd> `?([\w.]+)`?\s*$")

PORTING_HISTORY = re.compile(
    r"keyhac-(mac|win)|Keyhac for (Windows|macOS)|\bport(ed|ing)\b",
    re.IGNORECASE)
CAMEL = re.compile(r"[a-z0-9][A-Z]")

#: A DLL handle as the Windows platform layer spells one.
DLL_HANDLE = re.compile(
    r"^\s*(\w+)\s*=\s*(?:ctypes\.)?(?:windll\.\w+|WinDLL\(|OleDLL\(|oledll\.\w+)", re.M)

HOOKS = ("keyhac/platform/mac/hook.py", "keyhac/platform/win/hook.py")
UITREE = "keyhac/core/uitree.py"
UINODE_SHAPE = re.compile(r"\b(identity_key|reread|StaleElement|identifier)\b")
MCP_TOOLS = "keyhac/mcp/tools.py"


@dataclass
class Item:
    rule: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"  [{self.rule}] {self.where}: {self.message}"


def git(*args: str) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=True,
                            encoding="utf-8", cwd=REPO_ROOT)
    if result.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def show(ref: str, path: str) -> str | None:
    result = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True,
                            text=True, encoding="utf-8", cwd=REPO_ROOT)
    return result.stdout if result.returncode == 0 else None


def changed_files(base: str, head: str) -> list[tuple[str, str]]:
    """(status letter, path at *head*) for every file the range touched."""
    out = []
    for line in git("diff", "--name-status", "-M", base, head).splitlines():
        fields = line.split("\t")
        if fields[0].startswith("D"):
            continue
        out.append((fields[0][0], fields[-1]))
    return out


def added_lines(base: str, head: str, path: str) -> set[int]:
    """Line numbers in *head*'s copy of *path* that the range added."""
    lines: set[int] = set()
    diff = git("diff", "-U0", "-M", base, head, "--", path)
    for m in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", diff, re.M):
        start, count = int(m.group(1)), int(m.group(2) or 1)
        lines.update(range(start, start + count))
    return lines


# --- Findings --------------------------------------------------------------

def check_imports(path: str, source: str, added: set[int]) -> list[Item]:
    """An OS module imported on an added line of keyhac/core/ or keyhac/ui/."""
    if path.startswith("keyhac/core/"):
        layer, fix = "core", "go through a keyhac/platform/ interface"
    elif path.startswith("keyhac/ui/"):
        layer, fix = "ui", "go through PuiKit"
    else:
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [Item("syntax", f"{path}:{exc.lineno}", str(exc.msg))]
    found = []
    for node in ast.walk(tree):
        if getattr(node, "lineno", None) not in added:
            continue
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        for name in names:
            if name.split(".")[0] in OS_MODULES:
                found.append(Item(layer, f"{path}:{node.lineno}",
                                  f"imports {name} - {fix}"))
    return found


def check_prototypes(path: str, source: str, added: set[int]) -> list[Item]:
    """A DLL function called on an added line whose restype the file never
    declares: the default c_int restype truncates a 64-bit handle."""
    handles = DLL_HANDLE.findall(source)
    if not handles:
        return []
    declared = set(re.findall(r"\b(\w+\.\w+)\.restype\s*=", source))
    call = re.compile(r"\b((?:%s)\.\w+)\(" % "|".join(map(re.escape, handles)))
    found = []
    for n, text in enumerate(source.splitlines(), 1):
        if n not in added:
            continue
        for name in call.findall(text):
            if name not in declared:
                found.append(Item("ctypes", f"{path}:{n}",
                                  f"{name} is called with no restype/argtypes declared"))
    return found


def check_placement(path: str, base_root: set[str]) -> list[Item]:
    name = path.rsplit("/", 1)[-1]
    if path.startswith("temp/"):
        return [Item("place", path, "a temporary file is committed")]
    if "/" not in path and path not in base_root:
        return [Item("place", path, "new file at the repo root - meant to be there?")]
    if name.startswith("test_") and name.endswith(".py") \
            and not path.startswith("tests/"):
        return [Item("place", path, "a test outside tests/ - pytest will not find it")]
    return []


def api_entries(text: str | None) -> dict[str, tuple[str, str | None]]:
    """``Name.member`` -> (kind, signature line or None) from a generated
    API reference."""
    entries: dict[str, tuple[str, str | None]] = {}
    current = None
    in_code = False
    for line in (text or "").splitlines():
        m = API_HEADING.match(line)
        if m:
            current = m.group(2)
            entries[current] = (m.group(1), None)
            continue
        if current is None:
            continue
        if line.startswith("```python"):
            in_code = True
        elif line.startswith("```"):
            in_code = False
        elif in_code and entries[current][1] is None and line.strip():
            entries[current] = (entries[current][0], line.strip())
    return entries


def parameters(signature: str | None) -> dict[str, bool] | None:
    """Parameter name -> whether it has a default, from ``name(a, b=1) → T``."""
    if not signature:
        return None
    m = re.match(r"[\w.]+\((.*)\)", signature.split(" → ")[0].strip())
    if not m:
        return None
    params, depth, piece = {}, 0, ""
    for ch in m.group(1) + ",":
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            piece = piece.strip()
            if piece and piece not in ("*", "/", "self", "cls"):
                name = re.split(r"[:=]", piece, maxsplit=1)[0].strip().lstrip("*")
                params[name] = "=" in piece or piece.startswith("*")
            piece = ""
        else:
            piece += ch
    return params


def api_changes(base: str, head: str) -> tuple[list[Item], list[Item]]:
    """(findings, for-review items) from the public API across the release."""
    findings, notes = [], []
    for doc in API_DOCS:
        old_text, new_text = show(base, doc), show(head, doc)
        if old_text is None or new_text is None:
            continue
        old, new = api_entries(old_text), api_entries(new_text)
        added_text = "\n".join(line for line in new_text.splitlines()
                               if line not in set(old_text.splitlines()))
        for line in added_text.splitlines():
            if PORTING_HISTORY.search(line):
                notes.append(Item("docstring", doc, "for a migrating user, or porting "
                                  f"history? {line.strip()[:70]}"))
        for name in sorted(set(old) - set(new)):
            findings.append(Item("api", name, f"shipped at {base}, gone at {head} - "
                                 "the public API is additive-only (doc/dev/next-major.md)"))
        for name in sorted(set(new) - set(old)):
            kind = new[name][0]
            if CAMEL.search(name.rsplit(".", 1)[-1]) and kind != "class":
                findings.append(Item("api", name, "camelCase in the public API"))
            notes.append(Item("api", name, f"new {kind} - the note's \"Also\", and "
                                           "at least a minor bump"))
        for name in sorted(set(old) & set(new)):
            before, after = parameters(old[name][1]), parameters(new[name][1])
            if before is None or after is None or old[name][1] == new[name][1]:
                continue
            for param in before:
                if param not in after:
                    findings.append(Item("api", name, f"parameter {param} removed"))
            for param, has_default in after.items():
                if param not in before:
                    if has_default:
                        notes.append(Item("api", name, f"new parameter {param}"))
                    else:
                        findings.append(Item("api", name, f"new parameter {param} has "
                                             "no default - existing calls break"))
            if not any(f.where == name for f in findings) and set(before) == set(after):
                notes.append(Item("api", name, f"signature changed: {new[name][1]}"))
    return findings, notes


def run_tool(rule: str, argv: list[str], fix: str) -> list[Item]:
    result = subprocess.run([sys.executable, *argv], capture_output=True,
                            text=True, encoding="utf-8", cwd=REPO_ROOT)
    if result.returncode == 0:
        return []
    detail = (result.stderr or result.stdout).strip().splitlines()
    return [Item(rule, argv[0], f"{detail[-1] if detail else 'failed'} ({fix})")]


# --- For review ------------------------------------------------------------

def dependencies(text: str | None) -> dict[str, str]:
    """Requirement name -> the requirement, from pyproject's [project]."""
    if not text:
        return {}
    out = {}
    for req in tomllib.loads(text).get("project", {}).get("dependencies", []):
        m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
        if m:
            out[m.group(1).lower().replace("_", "-")] = req
    return out


def review_hints(base: str, head: str, changed: list[tuple[str, str]],
                 added: dict[str, set[int]]) -> list[Item]:
    hints = []
    for status, path in changed:
        if status == "A" and path.startswith("keyhac/") and path.endswith(".py"):
            hints.append(Item("layout", path, "new module - in the layer its imports "
                                              "allow (core / platform / ui)?"))
    old, new = dependencies(show(base, "pyproject.toml")), \
        dependencies(show(head, "pyproject.toml"))
    for name, req in sorted(new.items()):
        if name not in old:
            hints.append(Item("dependency", req, "new dependency - license? bundled "
                                                 "on both OSes?"))
        elif old[name] != req:
            hints.append(Item("dependency", req, f"was {old[name]} - say why in the note "
                                                 "when users see the difference"))
    for _, path in changed:
        if not (path.startswith("keyhac/") and path.endswith(".py")):
            continue
        lines = (show(head, path) or "").splitlines()
        for n in sorted(added.get(path, ())):
            if n > len(lines):
                continue
            text = lines[n - 1]
            if path.startswith("keyhac/core/") and \
                    re.match(r"\s*from keyhac\.(platform\.(mac|win)|ui)\b", text):
                hints.append(Item("layer", f"{path}:{n}",
                                  "core reaches into a platform or UI module - "
                                  "through an interface instead?"))
            if path == UITREE and UINODE_SHAPE.search(text):
                hints.append(Item("uinode", f"{path}:{n}",
                                  "near UINode's identity or lifetime, settled "
                                  "until the next major (CLAUDE.md)"))
        if path in HOOKS and added.get(path):
            hints.append(Item("hook", path, "the hook changed - check it against "
                                            "doc/dev/design-notes.md's load-bearing list"))
        if path == MCP_TOOLS and added.get(path):
            hints.append(Item("mcp", path, "the MCP tool surface changed - additive "
                                           "only, like the rest of the public API"))
    return hints


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--head", default="origin/main")
    parser.add_argument("--base", help="default: the last tag before --head")
    args = parser.parse_args(argv)
    head = args.head
    base = args.base or git("describe", "--tags", "--abbrev=0", head).strip()

    changed = changed_files(base, head)
    added = {path: added_lines(base, head, path) for _, path in changed}
    base_root = set(git("ls-tree", "--name-only", base).split())

    findings: list[Item] = []
    for status, path in changed:
        if path.endswith(".py") and path.startswith("keyhac/") and added[path]:
            source = show(head, path) or ""
            findings += check_imports(path, source, added[path])
            findings += check_prototypes(path, source, added[path])
    api_findings, api_notes = api_changes(base, head)
    findings += api_findings

    notes = []
    if git("rev-parse", "HEAD") != git("rev-parse", head) or \
            git("status", "--porcelain", "--untracked-files=no").strip():
        notes.append(f"the working tree is not a clean checkout of {head}: the "
                     "API-reference and translation checks below judged the "
                     "working tree")
    findings += run_tool("api-reference", ["tools/generate_api_reference.py", "--check"],
                         "make api-reference")
    findings += run_tool("translations", ["tools/check_translations.py"],
                         "make check-docs")
    hints = [item for status, path in changed if status == "A"
             for item in check_placement(path, base_root)]
    hints += review_hints(base, head, changed, added) + api_notes

    print(f"Release review {base}..{head}: {len(changed)} files changed")
    for note in notes:
        print(f"Note: {note}")
    print(f"\nFindings - CLAUDE.md's mechanical rules ({len(findings)})")
    print("\n".join(map(str, findings)) or "  none")
    print(f"\nFor review - judgment calls ({len(hints)})")
    print("\n".join(map(str, hints)) or "  none")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
