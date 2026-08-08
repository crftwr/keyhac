"""Mechanical checks for a generated action.

The rules in SKILL.md that can be read off the source, so a regression in the
skill shows up as a failing file rather than as a subtly worse action three
weeks later.  Judgement-shaped rules (are the preconditions the *right* ones?)
are in cases.md for a human or a model to score; everything here is decidable.

    python keyhac/skills/action-authoring/evals/check.py path/to/action.py [...]

Exit status is the number of files with violations.  Calibrated against
examples/actions/*/*.py, which must pass.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

#: (pattern, what is wrong) - matched against source lines outside comments.
FORBIDDEN = [
    (re.compile(r"\btime\.sleep\s*\(|\bsleep\s*\(\s*\d"),
     "sleep: wait for a condition, not for time (SKILL.md rule 1)"),
    (re.compile(r"\bclick\s*\(\s*\d+\s*,\s*\d+|\bmouse_move\s*\(\s*\d+\s*,\s*\d+"),
     "coordinates: address elements by identifier/name (rule 2)"),
    (re.compile(r"set_text\([^)]*verify\s*=\s*False"),
     "verify=False: the read-back is what makes a write safe (rule 3)"),
]

#: Rules that need the syntax tree rather than a regex.
def _check_ast(tree: ast.AST) -> list[str]:
    problems = []

    for node in ast.walk(tree):
        # rule 7: a while loop that follows links must be bounded.
        if isinstance(node, ast.While):
            test = ast.dump(node.test)
            bounded = ("Compare" in test and "True" not in test) or any(
                isinstance(inner, (ast.Break,)) for inner in ast.walk(node))
            if not bounded:
                problems.append(
                    f"line {node.lineno}: unbounded while loop - a 'Next' that "
                    f"links to itself runs forever (rule 7)")

        # rule 4: pressing a checkbox directly instead of set_checked().
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name == "perform_action":
                arg = node.args[0] if node.args else None
                if isinstance(arg, ast.Constant) and arg.value == "Toggle":
                    problems.append(
                        f"line {node.lineno}: Toggle pressed directly - "
                        f"set_checked() reads before pressing (rule 4)")
    return problems


def _accumulator_escapes(tree: ast.AST) -> list[str]:
    """Rule 6: a list built inside a function that raises is lost with it.

    Flags a local list that is appended to and also referenced by an `except`
    handler's sibling code - the shape that discarded every page already read.
    Heuristic and deliberately quiet: it only fires when a function both
    initialises a list and contains a bare `raise` after appending to it.
    """
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        locals_lists = {
            target.id
            for statement in node.body
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Name)
            and isinstance(statement.value, ast.List)
            and not statement.value.elts
        }
        if not locals_lists:
            continue
        appends = {}
        for call in ast.walk(node):
            if (isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "append"
                    and isinstance(call.func.value, ast.Name)):
                name = call.func.value.id
                appends[name] = min(appends.get(name, call.lineno), call.lineno)
        raise_lines = [n.lineno for n in ast.walk(node) if isinstance(n, ast.Raise)]
        returns_it = any(
            isinstance(n, ast.Return) and isinstance(n.value, ast.Name)
            and n.value.id in locals_lists
            for n in ast.walk(node))
        for name in locals_lists & set(appends):
            # Only a raise that can happen *after* something was collected
            # loses anything.  Validating up front and raising before the first
            # append is the correct shape, not the bug.
            late_raise = any(line > appends[name] for line in raise_lines)
            if late_raise and returns_it:
                problems.append(
                    f"line {node.lineno}: '{name}' accumulates inside "
                    f"{node.name}(), which also raises - everything collected "
                    f"so far is discarded with the exception (rule 6)")
    return problems


def _phantom_names(tree: ast.AST) -> list[str]:
    """`logger` and `keymap` look importable and are not.

    Both were invented by the first real generation session against this skill,
    whose header read:

        from keyhac import *  # ThreadedAction, WaitTimeout, logger, keymap

    Two of those four names the package does not export.  The failures land far
    apart - `keymap` at import time, `logger` on the first line that logs, which
    may be inside the branch nobody exercised - so neither is reliably caught by
    running the action once.
    """
    problems = []

    defines_logger = any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "logger"
                for target in node.targets)
        for node in ast.walk(tree))

    if not defines_logger:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "logger":
                problems.append(
                    f'line {node.lineno}: `logger` is not importable - add '
                    f'logger = getLogger("YourAction")')
                break

    # Module scope only: `keymap` is legitimate inside a def that takes it.
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
            continue
        for node in ast.walk(statement):
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "keymap"):
                problems.append(
                    f"line {node.lineno}: `keymap` at module scope raises "
                    f"NameError at import - it is configure()'s argument, so "
                    f"register from config.py instead")
                break

    return problems


#: Calls that mean this action *changes* the UI rather than only reading it.
ACTS_ON_UI = re.compile(r"\b(press|set_text|set_checked|perform_action)\s*\(")

#: Every spelling of a wait in the method-style API: node.wait_for(),
#: node.wait_until_gone(), node.wait_until_stable(), ui.wait().  The first
#: version of this pattern knew only the function-style `wait_for(`, and went
#: red on two actions the moment the API changed under it - which is the
#: checker doing its job, but it has to learn the new vocabulary to keep doing
#: it rather than being switched off.
REQUIRED = [
    (re.compile(r"\bwait_for\w*\s*\(|\bwait_until_\w+\s*\(|\.wait\s*\("),
     "no wait: an action that acts on the UI must wait for it (rule 1)"),
]


def check(path: pathlib.Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    code_lines = [
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    ]
    code = "\n".join(code_lines)

    problems = []
    for pattern, message in FORBIDDEN:
        for number, line in enumerate(code_lines, 1):
            if pattern.search(line):
                problems.append(f"line {number}: {message}")
    # A read-only action - jump-to-the-error-line, scrape-this-screen - has
    # nothing to wait for; requiring a wait there is a false positive.
    if ACTS_ON_UI.search(code):
        for pattern, message in REQUIRED:
            if not pattern.search(code):
                problems.append(message)

    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return problems + [f"does not parse: {error}"]
    problems += _check_ast(tree)
    problems += _accumulator_escapes(tree)
    problems += _phantom_names(tree)
    return problems


def main(argv: list[str]) -> int:
    paths = [pathlib.Path(a) for a in argv[1:]]
    if not paths:
        print(__doc__)
        return 0
    failed = 0
    for path in paths:
        problems = check(path)
        if problems:
            failed += 1
            print(f"{path}:")
            for problem in problems:
                print(f"    {problem}")
        else:
            print(f"{path}: ok")
    return failed


if __name__ == "__main__":
    sys.exit(main(sys.argv))
