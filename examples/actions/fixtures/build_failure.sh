#!/bin/bash
# Case 6's screen: a build that failed, in a terminal.
#
# Carries both shapes the action has to handle - "path:line:" from a linter and
# a Python traceback's `File "...", line N` - and the last match is not the
# first, because "take me to the file and line" means the one that stopped the
# build.
set -u
project="${TMPDIR:-/tmp}/acme"
mkdir -p "$project/src"
python3 - "$project" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
(root / "src" / "parser.py").write_text(
    "".join(f"# line {n}\n" for n in range(1, 60)))
(root / "src" / "loader.py").write_text(
    "".join(f"# line {n}\n" for n in range(1, 40)))
PY
printf '\033]0;build - acme\007'
echo "$ make build"
echo "acme 0.4.1 - building 2 modules"
echo "$project/src/loader.py:17:5: warning: unused import 'os'"
echo ""
echo "Traceback (most recent call last):"
echo "  File \"$project/src/loader.py\", line 12, in load"
echo "    return parse(text)"
echo "  File \"$project/src/parser.py\", line 42, in parse"
echo "    raise ValueError(f\"unbalanced bracket at {index}\")"
echo "ValueError: unbalanced bracket at 137"
echo ""
echo "make: *** [build] Error 1"
echo ""
echo "(this window is the screen; leave it open)"
exec bash -i
