"""A build that failed, on screen in a terminal - the build-error case's screen.

The cross-platform twin of build_failure.sh and build_failure.ps1. It carries
the shapes the action has to handle - "path:line:col" from a linter and a
Python traceback's `File "...", line N` - and the last match is not the first,
because "take me to the file and line" means the one that stopped the build.
"""

import pathlib
import tempfile

project = pathlib.Path(tempfile.gettempdir()) / "acme"
(project / "src").mkdir(parents=True, exist_ok=True)
parser, loader = project / "src" / "parser.py", project / "src" / "loader.py"
parser.write_text("".join(f"# line {n}\n" for n in range(1, 60)))
loader.write_text("".join(f"# line {n}\n" for n in range(1, 40)))

print("$ make build")
print("acme 0.4.1 - building 2 modules")
print(f"{loader}:17:5: warning: unused import 'os'")
print()
print("Traceback (most recent call last):")
print(f'  File "{loader}", line 12, in load')
print("    return parse(text)")
print(f'  File "{parser}", line 42, in parse')
print('    raise ValueError(f"unbalanced bracket at {index}")')
print("ValueError: unbalanced bracket at 137")
print()
print("make: *** [build] Error 1")
print()
print("(this window is the screen; leave it open)")
