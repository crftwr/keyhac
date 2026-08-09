"""The action classes in `extensions/`, as the MCP endpoint sees them.

This is the whole of what `list_actions` and `start_action` reach. There is no
second kind of action and no registry beside it: an action is a
`ThreadedAction` subclass in a file under `~/.keyhac/extensions/`, addressed by
`module.Class`, and a `config.py` decides only which of them get a key.

That is what removed the `config.py` edit from the front of the authoring loop,
where it used to sit as three lines in the middle of a working
several-hundred-line file, written for an action nobody yet knew worked.

**Listing does not import.** The catalogue comes from `ast.parse`, so a file is
read and never executed to find out what is in it. That preserves the property
the directory has always had: a module no `config.py` imports is inert on disk.
Importing a directory's worth of half-written files to enumerate them would have
created exactly the auto-execution Keyhac has never done - see
`Keymap._prepare_extensions`, which puts the directory on `sys.path`, evicts
stale modules, and imports nothing.

So a class here executes at exactly one moment: when something names it. That
is `start_action("module.Class")` - one module, by name, and only while the
endpoint is open, which is for an hour after the operator ticks the switch.

**What runs is always the current file.** `Loader` keys its instances on the
file's mtime and re-imports when it moves, so `write_extension` followed by
`start_action` runs what was just written, with no `reload_config` between them
- and it evicts the whole directory first, so a helper module the action
imports is re-read too rather than answering out of `sys.modules`.

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
import shutil
import sys


class ActionClass:
    """One `class X(ThreadedAction)` found in a file, not yet imported."""

    def __init__(self, module: str, class_name: str, path: str,
                 required: list[str], summary: str | None):
        #: How start_action addresses it: `translate_clipboard.Translate`.
        self.name = f"{module}.{class_name}"
        self.module = module
        self.class_name = class_name
        self.path = path
        #: Constructor parameters with no default. These are instantiated with
        #: no arguments, so a class needing any cannot be run from here - and
        #: saying which ones up front beats a TypeError at start time.
        self.required = required
        self.summary = summary

    def describe(self) -> str:
        if self.required:
            return (f"{self.name}: needs constructor arguments "
                    f"({', '.join(self.required)}) - give them defaults to run "
                    f"it from here")
        return f"{self.name}: {self.summary or 'no docstring'}"


def discover(extensions_dir: str) -> list[ActionClass]:
    """Every action class under `extensions_dir`, found without importing.

    Files whose names start with `_` are skipped **as candidates**: an
    extension named that way is a helper the operator split out, not something
    to offer as runnable. They are still parsed, because a helper is exactly
    where a shared base class lives and a subclass of one is an action.
    A file that does not parse is skipped rather than reported - it is being
    edited, and half a file is not a finding.
    """
    parsed = _parse_directory(extensions_dir)
    found: list[ActionClass] = []
    for module in sorted(parsed):
        if module.startswith("_"):
            continue
        source = parsed[module]
        for name, classdef in source.classes.items():
            if not _is_action(parsed, module, name, set()):
                continue
            found.append(ActionClass(module, name, source.path,
                                     _required_arguments(classdef),
                                     _first_line(ast.get_docstring(classdef))))
    return found


class _Parsed:
    """One file's top-level classes, and where its names came from."""

    def __init__(self, path: str):
        self.path = path
        self.classes: dict[str, ast.ClassDef] = {}   # in file order
        self.from_imports: dict[str, tuple[str, str]] = {}
        self.modules: dict[str, str] = {}


def _parse_directory(extensions_dir: str) -> dict[str, _Parsed]:
    parsed: dict[str, _Parsed] = {}
    try:
        entries = sorted(os.listdir(extensions_dir))
    except OSError:
        return parsed
    for entry in entries:
        if not entry.endswith(".py"):
            continue
        path = os.path.join(extensions_dir, entry)
        try:
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read(), path)
        except (OSError, SyntaxError):
            continue
        parsed[entry[:-3]] = _read_module(tree, path)
    return parsed


def _read_module(tree, path: str) -> _Parsed:
    source = _Parsed(path)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            source.classes[node.name] = node
        elif isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                source.modules[alias.asname or root] = root
        elif isinstance(node, ast.ImportFrom) and not node.level:
            origin = (node.module or "").split(".")[0]
            for alias in node.names:
                source.from_imports[alias.asname or alias.name] = (origin,
                                                                   alias.name)
    return source


def _is_action(parsed: dict[str, _Parsed], module: str, class_name: str,
               seen: set) -> bool:
    """Whether `module.class_name` reaches `ThreadedAction` through its bases.

    **Transitively, and across files.** Matching only a *direct* base spelled
    `ThreadedAction` made the natural way to reuse an action - subclass it -
    the one way to write one this cannot see, so `start_action` could not run
    it while a key binding ran it perfectly (issue #43). The workaround was to
    name `ThreadedAction` a second time among the bases, which is a line of
    code written to satisfy a scanner.

    Still no import: reading a directory must not execute it, which is the
    property this whole module is built around. So the graph is the one the
    files describe - classes defined here, and names they imported from each
    other - and a base that leads out of `extensions/` simply ends the walk.

    `seen` breaks the cycle a file being edited can describe (`class A(B)` and
    `class B(A)`), which is unrunnable but must not hang a listing.
    """
    key = (module, class_name)
    if key in seen:
        return False
    seen.add(key)

    source = parsed.get(module)
    if source is None:
        return False
    classdef = source.classes.get(class_name)
    if classdef is None:
        return False

    for base in classdef.bases:
        # Both spellings the skill's import header can produce:
        # `ThreadedAction` from `from keyhac import ...`, and a dotted one if
        # it was reached through the package. Matching on the trailing name is
        # deliberately loose - the cost of a false positive is one row in a
        # list, and the cost of a false negative is an action nobody can see.
        if _base_name(base) == "ThreadedAction":
            return True
        target = _base_target(source, module, base)
        if target is not None and _is_action(parsed, *target, seen):
            return True
    return False


def _base_name(base) -> str | None:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return None


def _base_target(source: _Parsed, module: str, base) -> tuple[str, str] | None:
    """Which `(module, class)` in `extensions/` a base expression names."""
    # `helpers.Base` - a module imported here, addressed by attribute.
    if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
        origin = source.modules.get(base.value.id)
        return (origin, base.attr) if origin else None
    if not isinstance(base, ast.Name):
        return None
    # `Base` - defined in this file, or imported by name from another.
    if base.id in source.classes:
        return (module, base.id)
    imported = source.from_imports.get(base.id)
    return imported if imported else None


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


class Loader:
    """Imports a module when something asks to run a class in it, not before.

    Instances are cached per name and invalidated by the file's mtime, so an
    edited file is re-imported on its next run. Without that the second run of
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

    def instantiate(self, action: ActionClass):
        stamp = _mtime(action.path)
        entry = self._instances.get(action.name)
        if entry is not None and entry[0] == stamp:
            return entry[1]
        directory = os.path.dirname(action.path)
        # Evict the whole directory, not just this file. Re-importing the
        # action's own module by path leaves any *helper* it imports resolving
        # out of sys.modules - so an action split across two files would run
        # its edited half against the previous version of the other, and report
        # success. Measured. This is `_prepare_extensions`' own eviction, reused
        # rather than reimplemented, since getting it subtly different is the
        # whole hazard.
        from keyhac.core.keymap import Keymap
        Keymap._prepare_extensions(directory)
        _drop_bytecode(directory)
        module = _import_file(action.module, action.path)
        loaded = getattr(module, action.class_name, None)
        if loaded is None:
            raise KeyError(f"{action.path} no longer defines "
                           f"{action.class_name}")
        instance = loaded()
        # The scan matched a base *named* ThreadedAction; this is where that
        # becomes a fact. Shadowing the name is far-fetched, but the caller
        # relies on the real base's cancellable() and would otherwise fail with
        # an AttributeError on a thread nobody is watching.
        from keyhac.core.action import ThreadedAction
        if not isinstance(instance, ThreadedAction):
            raise KeyError(f"{action.name} does not subclass keyhac's "
                           f"ThreadedAction, so it cannot be run from here")
        self._instances[action.name] = (stamp, instance)
        return instance


def _drop_bytecode(directory: str) -> None:
    """Remove `__pycache__` under `directory`, so a re-import reads the source.

    Evicting `sys.modules` is not enough on its own. A cached `.pyc` is
    validated against the source's mtime **in whole seconds** and its size, so
    two edits inside one second that happen to leave the file the same length -
    a model fixing one character, twice - reload the *previous* bytecode and
    report success. Measured; it is the same silent staleness the eviction
    exists to prevent, one layer further down.

    Only on this path, not in `_prepare_extensions`: a config reload happens by
    hand and rarely, while this one runs in a loop that edits every few seconds.
    Bytecode is regenerable by definition, and these files are small.
    """
    shutil.rmtree(os.path.join(directory, "__pycache__"), ignore_errors=True)


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
