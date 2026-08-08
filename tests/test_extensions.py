"""``~/.keyhac/extensions`` as an importable, re-importable module directory.

doc/configuration.md promised this from the first release and the code never
did it: the directory was created and then never referred to again, so every
config that tried to `import` from it raised ModuleNotFoundError.  Pinned here
because the promise is a documented one, and because the reload half of it
fails *silently* when it regresses -- a stale module answers the import and the
run looks like it succeeded.
"""

import os
import sys
import textwrap

import pytest

from keyhac.core.keymap import Keymap


@pytest.fixture
def clean_imports():
    """Undo what a test's imports did to the interpreter it shares."""
    before_path = list(sys.path)
    before_modules = set(sys.modules)
    yield
    sys.path[:] = before_path
    for name in set(sys.modules) - before_modules:
        del sys.modules[name]


@pytest.fixture
def extensions(tmp_path, clean_imports):
    directory = tmp_path / "extensions"
    directory.mkdir()
    return directory


def write(directory, name, body):
    (directory / f"{name}.py").write_text(textwrap.dedent(body))


def test_the_directory_becomes_importable(extensions):
    write(extensions, "an_action", "VALUE = 'first'")
    Keymap._prepare_extensions(str(extensions))

    import an_action
    assert an_action.VALUE == "first"


def test_it_is_appended_so_it_cannot_shadow_the_stdlib(extensions):
    """An action module is named after what it does, and `queue` is a plausible
    name.  Prepending would break concurrent.futures for the whole process."""
    write(extensions, "queue", "raise AssertionError('stdlib was shadowed')")
    Keymap._prepare_extensions(str(extensions))

    assert sys.path[-1] == os.path.realpath(str(extensions))

    import queue
    assert queue.Queue, "the standard library must still win"


def test_registering_twice_does_not_pile_up_entries(extensions):
    """configure() runs on every reload, and a growing sys.path is a leak."""
    Keymap._prepare_extensions(str(extensions))
    Keymap._prepare_extensions(str(extensions))
    Keymap._prepare_extensions(str(extensions))

    resolved = os.path.realpath(str(extensions))
    assert [os.path.realpath(e) for e in sys.path if e].count(resolved) == 1


def test_a_reload_picks_up_an_edit(extensions):
    """The failure this guards is silent: without the cache purge the import
    is answered from sys.modules, the edited file is never read, and the action
    reports success while running the code it had before the fix."""
    write(extensions, "edited_action", "VALUE = 'before'")
    Keymap._prepare_extensions(str(extensions))
    import edited_action
    assert edited_action.VALUE == "before"

    write(extensions, "edited_action", "VALUE = 'after'")
    Keymap._prepare_extensions(str(extensions))       # what a reload does

    import edited_action
    assert edited_action.VALUE == "after", "the edit was not picked up"


def test_a_reload_leaves_unrelated_modules_alone(extensions):
    """Only modules loaded *from* the directory are dropped."""
    Keymap._prepare_extensions(str(extensions))
    marker = object()
    sys.modules["json"].__dict__.setdefault("_keyhac_marker", marker)

    Keymap._prepare_extensions(str(extensions))

    assert sys.modules["json"].__dict__.get("_keyhac_marker") is marker
    del sys.modules["json"].__dict__["_keyhac_marker"]


def test_a_missing_directory_is_not_fatal(tmp_path):
    """A read-only data directory loses the extensions folder, not the config
    load - so this runs on a path that does not exist."""
    Keymap._prepare_extensions(str(tmp_path / "never-created"))


def test_configure_actually_calls_it(engine, tmp_path, clean_imports):
    """Every test above drives the helper directly, so deleting the call from
    configure() would leave them all green while the feature was gone.  This
    one goes through a real config load."""
    directory = tmp_path / "extensions"
    directory.mkdir(exist_ok=True)
    write(directory, "wired_check", "VALUE = 'loaded from extensions'")

    seen = {}

    def configure(keymap):
        import wired_check
        seen["value"] = wired_check.VALUE

    engine(configure)

    assert seen["value"] == "loaded from extensions", \
        "configure() no longer puts extensions/ on sys.path"
