"""The test suite, run inside a VM's desktop session - never on the host.

tools/vm_test.py stages the working tree and starts this in the guest:

    vm_guest_pytest.py <exit-code file> <pytest args...>

On Windows it runs from a scheduled task through quality-loop's launcher,
with python.exe - a console of its own, which the tests that run a console
program inherit - and its output goes to the log on the share. When
pytest is done it writes pytest's exit code to the file, which is how the
host knows the run is over. Written whole, then renamed: the host polls for
the name.
"""

import os
import sys

import pytest

done, args = sys.argv[1], sys.argv[2:]
try:
    code = int(pytest.main(args))
except BaseException as error:  # noqa: BLE001 - the host must hear of any end
    print(f"vm_guest_pytest: {error!r}", flush=True)
    code = 3
sys.stdout.flush()
with open(done + ".partial", "w", encoding="utf-8") as f:
    f.write(str(code))
os.replace(done + ".partial", done)
