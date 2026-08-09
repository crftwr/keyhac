"""Action classes sitting in `extensions/` that no `config.py` has registered.

The authoring loop's first round used to need a `config.py` edit before the
model could run anything at all: three lines, in the middle of a working
several-hundred-line file, for an action nobody yet knows works. Drafts move
that edit to the *end* - you register what has been shown to work, rather than
editing to find out.

**Listing does not import.** The catalogue comes from `ast.parse`, so a file in
`extensions/` is read and never executed to find out what is in it. That
preserves the property the directory has always had: a module no `config.py`
imports is inert on disk. Importing a directory's worth of half-finished drafts
to enumerate them would have created exactly the auto-execution that Keyhac has
never done - see `Keymap._prepare_extensions`, which puts the directory on
`sys.path` and evicts stale modules, and imports nothing.

So a draft executes at exactly one moment: when something names it. That is
`start_action("module.Class")`, one module, by name, and only while the
operator has action authoring switched on.

**A draft is always the current file.** The loader keys its instances on the
file's mtime and re-imports when it moves, so `write_extension` followed by
`start_action` runs what was just written - no `reload_config` in between.
Registered actions still need one, because those come from `config.py`.

The marker is `ThreadedAction`. An action that drives a UI has to be one (a key
press must return immediately), the authoring skill mandates it, and every
shipped example uses it - so restricting the scan to it keeps the rule legible:
what appears here is what the skill tells the model to write. It also means
everything reaching `start_action` has `cancellable()`, `starting()` and
`finished()`, which is why that tool has one path through it rather than a
duck-typed fallback.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import sys


class Draft:
    """One `class X(ThreadedAction)` found in a file, not yet imported."""

    def __init__(self, module: str, cls: str, path: str,
                 required: list[str], summary: str | None):
        #: How start_action addresses it. The dot keeps it from colliding with
        #: the operator's own names, which are theirs to choose freely.
        self.name = f"{module}.{cls}"
        self.module = module
        self.cls = cls
        self.path = path
        #: Constructor parameters with no default. A draft is instantiated with
        #: no arguments, so a class needing any cannot be run from here - and
        #: saying which ones up front beats a TypeError at start time.
        self.required = required
        self.summary = summary

    def describe(self) -> str:
        if self.required:
            return (f"{self.name}: needs constructor arguments "
                    f"({', '.join(self.required)}) - register it in config.py "
                    f"to pass them")
        return f"{self.name}: {self.summary or 'no docstring'}"


def discover(extensions_dir: str) -> list[Draft]:
    """Every action class under `extensions_dir`, found without importing.

    Files whose names start with `_` are skipped: an extension named that way
    is a helper the operator split out, not something to offer as runnable.
    A file that does not parse is skipped rather than reported - it is being
    edited, and half a file is not a finding.
    """
    drafts: list[Draft] = []
    try:
        entries = sorted(os.listdir(extensions_dir))
    except OSError:
        return drafts
    for entry in entries:
        if not entry.endswith(".py") or entry.startswith("_"):
            continue
        path = os.path.join(extensions_dir, entry)
        try:
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
        except OSError:
            continue
        for cls, required, summary in _action_classes(source, path):
            drafts.append(Draft(entry[:-3], cls, path, required, summary))
    return drafts


def _action_classes(source: str, path: str):
    try:
        tree = ast.parse(source, path)
    except SyntaxError:
        return []
    found = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and any(
                _is_threaded_action(base) for base in node.bases):
            found.append((node.name, _required_arguments(node),
                          _first_line(ast.get_docstring(node))))
    return found


def _is_threaded_action(base) -> bool:
    # Both spellings the skill's import header can produce: `ThreadedAction`
    # from `from keyhac import ...`, and a dotted one if it was reached through
    # the package. Matching on the trailing name is deliberately loose - the
    # cost of a false positive is one row in a list, and the cost of a false
    # negative is an action the model cannot see.
    if isinstance(base, ast.Name):
        return base.id == "ThreadedAction"
    if isinstance(base, ast.Attribute):
        return base.attr == "ThreadedAction"
    return False


def _required_arguments(classdef) -> list[str]:
    for node in classdef.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "__init__":
            continue
        args = node.args
        positional = args.posonlyargs + args.args
        # Defaults bind to the *last* parameters, so what is left after
        # removing that many from the tail is required. Drop `self` with [1:].
        required = positional[:len(positional) - len(args.defaults)][1:]
        names = [argument.arg for argument in required]
        names += [argument.arg
                  for argument, default in zip(args.kwonlyargs, args.kw_defaults)
                  if default is None]
        return names
    return []


def _first_line(docstring: str | None) -> str | None:
    if not docstring:
        return None
    return docstring.strip().splitlines()[0]


class DraftLoader:
    """Imports a draft's module when something asks to run it, not before.

    Instances are cached per name and invalidated by the file's mtime, so an
    edited draft is re-imported on its next run. Without that the second run of
    a fix loop would execute the first version - the same silent staleness
    `_prepare_extensions` exists to prevent for `config.py`'s own imports.

    Caching is also what makes cancellation work: `cancel_action` looks the
    action up again to reach its flag, and a fresh instance per start would
    hand it one that is not running.
    """

    def __init__(self):
        self._instances: dict[str, tuple[float, object]] = {}

    def cached(self, name: str):
        """The live instance for `name`, or None. Never imports.

        `get_action_result` and `cancel_action` go through this: a run that has
        already started must stay readable even after the authoring window has
        closed underneath it.
        """
        entry = self._instances.get(name)
        return None if entry is None else entry[1]

    def instantiate(self, draft: Draft):
        stamp = _mtime(draft.path)
        entry = self._instances.get(draft.name)
        if entry is not None and entry[0] == stamp:
            return entry[1]
        module = _import_file(draft.module, draft.path)
        action_class = getattr(module, draft.cls, None)
        if action_class is None:
            raise KeyError(f"{draft.path} no longer defines {draft.cls}")
        instance = action_class()
        # The scan matched a base *named* ThreadedAction; this is where that
        # becomes a fact. Shadowing the name is far-fetched, but the caller
        # relies on the real base's cancellable() and would otherwise fail with
        # an AttributeError on a thread nobody is watching.
        from keyhac.core.action import ThreadedAction
        if not isinstance(instance, ThreadedAction):
            raise KeyError(f"{draft.name} does not subclass keyhac's "
                           f"ThreadedAction, so it cannot be run from here")
        self._instances[draft.name] = (stamp, instance)
        return instance

    def forget(self) -> None:
        """Drop every cached instance - a config reload rebuilt the world."""
        self._instances.clear()


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return -1


def _import_file(module_name: str, path: str):
    """Import `path` as `module_name`, replacing any previous copy.

    By file location rather than by name: `extensions/` is *appended* to
    `sys.path`, so a module sharing a name with the standard library would
    never be reached by a plain import - and importing the wrong `queue.py` to
    look for a class in it is a confusing way to fail. This loads the file the
    scan actually read.

    It is still registered in `sys.modules` under its plain name, so a later
    `import thing` from `config.py` reaches the same object and
    `_prepare_extensions` evicts it on the next reload like any other.
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module
