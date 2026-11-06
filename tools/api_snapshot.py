"""Write, check and compare the snapshot of Keyhac's public API (#278).

    .venv/bin/python tools/api_snapshot.py --check   # exit 1 on any difference
    .venv/bin/python tools/api_snapshot.py --write   # regenerate the file

`tests/data/api_snapshot.txt` is one line per item: every class and function the
references document and each of its documented members with its signature, the
marks' registries, and the MCP tools with their parameters. Each line says
`stable`, `preview` or `deprecated`. tests/test_api_snapshot.py regenerates it in
memory and fails on any difference, so every API change shows up as a diff of this
file, and the additive-only promise is checked on every change rather than only by
tools/release_review.py before a release.

A member with a `lazydocs: ignore` line is left out: those are public only in the
naming sense, which is what the promise does not cover.

Regenerating is the deliberate step, and it is where a breaking change is caught:
`--write` refuses one unless the major version moved in the same change. Deprecating
an API is not breaking. Removing it is, and it waits for the next major. A Preview
API can change or go without any of this.

Ported from XeFM's tools/api_snapshot.py. XeFM's `API_VERSION` is the major version
here.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import keyhac  # noqa: E402
import keyhac.core.ui as ui_module  # noqa: E402
from keyhac.core.api_marks import is_deprecated, is_preview  # noqa: E402
from keyhac.mcp.tools import ToolRegistry  # noqa: E402
from keyhac.platform.base import Window  # noqa: E402
from public_api import ACTION_API_NAMES, API_NAMES  # noqa: E402

SNAPSHOT = ROOT / "tests" / "data" / "api_snapshot.txt"
MAJOR_ROW = "keyhac.major"

#: Public on a Keymap, documented in doc/configuration.md, and assigned by main()
#: because they need the UI, so no class definition shows them.
ASSIGNED_TO_KEYMAP = ["pop_balloon", "close_balloon"]

_KIND = {
    inspect.Parameter.POSITIONAL_ONLY: "pos",
    inspect.Parameter.POSITIONAL_OR_KEYWORD: "pk",
    inspect.Parameter.KEYWORD_ONLY: "kw",
    inspect.Parameter.VAR_POSITIONAL: "*",
    inspect.Parameter.VAR_KEYWORD: "**",
}

#: Kinds whose detail is a signature, and so compared parameter by parameter.
_CALLABLE = ("function", "method", "classmethod", "staticmethod", "constructor")


def _default(value) -> str:
    """A default as text that does not move from run to run."""
    return re.sub(r" at 0x[0-9a-fA-F]+", "", repr(value))


def _signature(func) -> str:
    """`func`'s parameters as JSON, `[name, kind, default-or-null]` each. Annotations
    are left out: changing one breaks no caller."""
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return "?"
    return json.dumps([[p.name, _KIND[p.kind],
                        None if p.default is inspect.Parameter.empty else _default(p.default)]
                       for p in params], ensure_ascii=False)


def _ignored(raw) -> bool:
    func = raw.fget if isinstance(raw, property) else getattr(raw, "__func__", raw)
    return "lazydocs: ignore" in (getattr(func, "__doc__", None) or "")


def _member(owner, name: str, raw) -> tuple[str, str]:
    if isinstance(raw, property):
        return "property", "rw" if raw.fset else "ro"
    if isinstance(raw, classmethod):
        return "classmethod", _signature(getattr(owner, name))
    if isinstance(raw, staticmethod):
        return "staticmethod", _signature(getattr(owner, name))
    if inspect.isfunction(raw):
        return "method", _signature(raw)
    return "attribute", ""


def _resolve(name: str):
    if name == "Window":
        return Window
    return getattr(ui_module, name) if hasattr(ui_module, name) else getattr(keyhac, name)


def _tool_detail(schema: dict) -> str:
    """A tool's parameters as JSON, `[name, required]` each."""
    required = set(schema.get("required", ()))
    return json.dumps([[name, name in required] for name in sorted(schema.get("properties", {}))])


def build() -> dict[str, tuple[str, str, str]]:
    """`{name: (status, kind, detail)}` for the whole public surface."""
    items: dict[str, tuple[str, str, str]] = {}

    def put(name: str, kind: str, detail: str = "") -> None:
        status = ("deprecated" if is_deprecated(name) is not None
                  else "preview" if is_preview(name) else "stable")
        items[name] = (status, kind, detail)

    items[MAJOR_ROW] = ("stable", "version", keyhac.__version__.split(".")[0])
    put("PREVIEW_APIS", "data")
    put("DEPRECATED_APIS", "data")

    for name in dict.fromkeys(API_NAMES + ACTION_API_NAMES):
        obj = _resolve(name)
        if not inspect.isclass(obj):
            put(name, "function", _signature(obj))
            continue
        put(name, "class")
        init = inspect.getattr_static(obj, "__init__")
        if init is not object.__init__ and not _ignored(init):
            put(f"{name}.__init__", "constructor", _signature(init))
        for member in sorted(dir(obj)):
            if member.startswith("_"):
                continue
            raw = inspect.getattr_static(obj, member)
            # An exception's `args` and `with_traceback` are Python's, not ours, and
            # can change with the interpreter.
            builtin = any(member in vars(base) for base in obj.__mro__
                          if base.__module__ == "builtins")
            if not builtin and not _ignored(raw):
                put(f"{name}.{member}", *_member(obj, member, raw))

    for member in ASSIGNED_TO_KEYMAP:
        put(f"Keymap.{member}", "attribute")

    # The tool list needs no keymap to be built: what is bound to it runs when a
    # tool is called.
    for tool in ToolRegistry(None).tools.values():
        put(f"mcp.{tool.name}", "tool", _tool_detail(tool.schema))
    return items


def render(items: dict[str, tuple[str, str, str]]) -> str:
    lines = [f"{status}\t{kind}\t{name}\t{detail}".rstrip("\t")
             for name, (status, kind, detail) in sorted(items.items())]
    return ("# Keyhac public API snapshot - regenerate with `make api-snapshot`.\n"
            "# status\tkind\tname\tdetail\n" + "\n".join(lines) + "\n")


def parse(text: str) -> dict[str, tuple[str, str, str]]:
    items = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        status, kind, name, *rest = line.split("\t")
        items[name] = (status, kind, rest[0] if rest else "")
    return items


def _signature_breaks(old: str, new: str) -> list[str]:
    """Why a caller written against `old` would break on `new`."""
    try:
        before, after = json.loads(old), json.loads(new)
    except ValueError:
        return [f"signature changed from {old} to {new}"]
    reasons = []
    now = {p[0]: (i, p) for i, p in enumerate(after)}
    has_var_kw = any(p[1] == "**" for p in after)
    for i, (name, kind, default) in enumerate(before):
        if name not in now:
            if not (kind in ("kw", "pk") and has_var_kw):
                reasons.append(f"parameter '{name}' removed")
            continue
        j, (_, new_kind, new_default) = now[name]
        if kind in ("pos", "pk") and new_kind in ("pos", "pk") and i != j:
            reasons.append(f"parameter '{name}' moved from position {i} to {j}")
        if kind == "pk" and new_kind in ("pos", "kw"):
            reasons.append(f"parameter '{name}' is no longer positional-or-keyword")
        if kind == "kw" and new_kind == "pos":
            reasons.append(f"parameter '{name}' is no longer a keyword")
        if default is not None and new_default is None:
            reasons.append(f"parameter '{name}' lost its default")
        elif default is not None and new_default != default:
            reasons.append(f"parameter '{name}' default changed from {default} to {new_default}")
    old_names = {p[0] for p in before}
    for name, kind, default in after:
        if name not in old_names and default is None and kind in ("pos", "pk", "kw"):
            reasons.append(f"required parameter '{name}' added")
    return reasons


def _tool_breaks(old: str, new: str) -> list[str]:
    """Why a call written against tool parameters `old` would break on `new`."""
    before, after = dict(json.loads(old)), dict(json.loads(new))
    reasons = [f"parameter '{name}' removed" for name in before if name not in after]
    reasons += [f"parameter '{name}' is now required" for name, required in after.items()
                if required and not before.get(name)]
    return reasons


def _changes(before, after) -> list[str]:
    """Why a promised item's change breaks callers; empty when it only adds."""
    (_, kind, old), (_, new_kind, new) = before, after
    if kind != new_kind:
        return [f"changed from {kind} to {new_kind}"]
    if kind in _CALLABLE:
        return _signature_breaks(old, new)
    if kind == "tool":
        return _tool_breaks(old, new)
    if kind == "property":
        return ["lost its setter"] if old == "rw" and new == "ro" else []
    return [f"changed from {old!r} to {new!r}"]


def classify(old, new) -> list[tuple[str, str, str]]:
    """Every difference as `(category, name, why)`: `breaking` (to a promised API,
    which is stable or deprecated), `addition`, `deprecation` or `preview`."""
    promised = ("stable", "deprecated")
    out = []
    for name in sorted(set(old) | set(new)):
        before, after = old.get(name), new.get(name)
        if before == after or name == MAJOR_ROW:
            continue
        if after is None:
            if before[0] == "preview":
                out.append(("preview", name, "removed"))
            elif before[0] == "deprecated":
                out.append(("breaking", name, "removed"))
            else:
                out.append(("breaking", name, "removed without being deprecated first"))
        elif before is None:
            out.append(("addition" if after[0] in promised else "preview", name, "added"))
        elif before[0] == "stable" and after[0] == "deprecated":
            out.append(("deprecation", name, "deprecated"))
        elif before[0] == "deprecated" and after[0] == "stable":
            out.append(("addition", name, "no longer deprecated"))
        elif before[0] in promised and after[0] == "preview":
            out.append(("breaking", name, f"demoted from {before[0]} to Preview"))
        elif before[0] == "preview" and after[0] in promised:
            out.append(("addition", name, f"promoted from Preview to {after[0]}"))
        elif before[0] == "preview":
            out.append(("preview", name, f"{before[1:]} -> {after[1:]}"))
        else:
            reasons = _changes(before, after)
            out.append(("breaking", name, "; ".join(reasons)) if reasons
                       else ("addition", name, f"{before[2]} -> {after[2]}"))
    return out


def major(items) -> int | None:
    row = items.get(MAJOR_ROW)
    return int(row[2]) if row else None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in (["--check"], ["--write"]):
        print(__doc__.split("\n\n")[1])
        return 2
    current = build()
    stored = parse(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.exists() else {}
    changes = classify(stored, current)
    for category, name, why in changes:
        print(f"{category}: {name}: {why}")
    if args == ["--check"]:
        if changes or major(stored) != major(current):
            print(f"{SNAPSHOT.relative_to(ROOT)} is out of date: run make api-snapshot.")
            return 1
        print("The public API matches the snapshot.")
        return 0
    breaking = [change for change in changes if change[0] == "breaking"]
    if breaking and stored and major(stored) == major(current):
        print("Refusing to write: the public API is additive-only within a major version "
              "(doc/dev/next-major.md). Undo the breaking change above, or record it there.")
        return 1
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(render(current), encoding="utf-8", newline="\n")
    print(f"Wrote {SNAPSHOT.relative_to(ROOT)}: {len(current)} items.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
