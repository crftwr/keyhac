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


def test_a_reload_picks_up_an_edit_a_pyc_would_hide(extensions):
    """Issue #41: the same silent staleness, one layer below sys.modules.

    A timestamp .pyc is validated on int(mtime) and size, so an edit inside one
    whole second that leaves the file the same length reloads the *previous*
    bytecode through an eviction that did everything else right. Not the corner
    it sounds like - write_extension replaces whole files, and a one-character
    fix to a format string is a file of identical length seconds later.

    The mtimes here are pinned rather than raced: the point is the arithmetic
    the import system does, not whether this machine is fast enough to lose.
    """
    write(extensions, "same_length", "VALUE = 'aaa'")
    path = extensions / "same_length.py"
    os.utime(path, (1_000_000_000.25, 1_000_000_000.25))

    Keymap._prepare_extensions(str(extensions))
    import same_length
    assert same_length.VALUE == "aaa"
    assert (extensions / "__pycache__").exists(), \
        "no .pyc was written, so this test is not testing anything"

    write(extensions, "same_length", "VALUE = 'bbb'")     # same byte length
    os.utime(path, (1_000_000_000.75, 1_000_000_000.75))  # same whole second

    Keymap._prepare_extensions(str(extensions))           # what a reload does

    import same_length
    assert same_length.VALUE == "bbb", "the edit was hidden by stale bytecode"


def test_a_loaded_module_is_recognized_as_current(extensions):
    """Issue #40's other half: knowing when *not* to re-import.

    Nothing else can answer this. A plain `import` leaves no record of when it
    read the file, so without the stamp taken after a config load, every reader
    has to assume the worst and load its own copy - which is exactly what made
    two of every action.
    """
    write(extensions, "shared_state", "VALUE = 'loaded'")
    path = str(extensions / "shared_state.py")

    Keymap._prepare_extensions(str(extensions))
    import shared_state
    Keymap._stamp_extensions(str(extensions))             # end of configure()

    assert Keymap._loaded_extension("shared_state", path) is shared_state

    write(extensions, "shared_state", "VALUE = 'edited'")
    assert Keymap._loaded_extension("shared_state", path) is None, \
        "an edited file must not be answered out of sys.modules"


def test_an_unstamped_module_is_not_claimed_as_current(extensions):
    """A module nobody vouched for is not one to reuse."""
    write(extensions, "unstamped", "VALUE = 1")
    Keymap._prepare_extensions(str(extensions))
    import unstamped                                       # noqa: F401

    assert Keymap._loaded_extension(
        "unstamped", str(extensions / "unstamped.py")) is None


def test_a_reload_forgets_what_it_evicted(extensions):
    """The stamp must not outlive the module it describes.

    Otherwise a reload leaves a stamp pointing at an unchanged file with no
    module behind it, and the next lookup would claim whatever `sys.modules`
    happened to hold under that name.
    """
    write(extensions, "evicted", "VALUE = 1")
    path = str(extensions / "evicted.py")
    Keymap._prepare_extensions(str(extensions))
    import evicted                                         # noqa: F401
    Keymap._stamp_extensions(str(extensions))

    Keymap._prepare_extensions(str(extensions))
    sys.modules["evicted"] = object()                      # someone else's

    assert Keymap._loaded_extension("evicted", path) is None


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


class TestSubdirectories:
    """`_prepare_extensions` puts the directory on `sys.path`, so
    `extensions/pkg/nested.py` is importable as `pkg.nested` from a
    `config.py`. Listing only the top level made a file Keyhac would happily
    import invisible to everything that lists - and "I can bind it but it does
    not appear" is the worst kind of inconsistency, because nothing about it
    looks like a rule.
    """

    ACTION = """
        from keyhac.core.action import ThreadedAction

        class {name}(ThreadedAction):
            \"\"\"{doc}\"\"\"
            def run(self):
                return "{name}"
            def finished(self, result):
                pass
    """

    def _tree(self, extensions):
        package = extensions / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("")
        write(extensions, "top", self.ACTION.format(name="Top", doc="At the top."))
        write(package, "nested", self.ACTION.format(name="Nested",
                                                    doc="In a package."))
        return extensions

    def test_a_nested_action_is_listed_by_its_dotted_name(self, extensions):
        from keyhac.mcp.extensions import discover
        names = [a.name for a in discover(str(self._tree(extensions)))]
        assert "pkg.nested.Nested" in names
        assert "top.Top" in names

    def test_a_package_init_is_not_offered(self, extensions):
        """`__init__` starts with `_`, so the helper rule already covers it."""
        from keyhac.mcp.extensions import discover
        names = [a.name for a in discover(str(self._tree(extensions)))]
        assert not any(".__init__." in name for name in names)

    def test_an_underscored_package_is_a_helper(self, extensions):
        """The same rule a `_helpers.py` file gets, applied per component."""
        from keyhac.mcp.extensions import discover
        helpers = extensions / "_helpers"
        helpers.mkdir()
        (helpers / "__init__.py").write_text("")
        write(helpers, "shared", self.ACTION.format(name="Helper", doc="Helper."))
        assert [a.name for a in discover(str(extensions))] == []

    def test_a_base_class_in_a_package_is_still_followed(self, extensions):
        """The cross-file graph has to survive dotted module names, or
        subclassing a shared base becomes the one way to write an action this
        cannot see - which is issue #43 again."""
        from keyhac.mcp.extensions import discover
        self._tree(extensions)
        write(extensions, "derived", """
            from pkg.nested import Nested

            class Derived(Nested):
                \"\"\"Subclassed across a package boundary.\"\"\"
                pass
        """)
        names = [a.name for a in discover(str(extensions))]
        assert "derived.Derived" in names

    def test_a_nested_action_can_be_loaded_and_run(self, extensions):
        from keyhac.mcp.extensions import Loader, discover
        Keymap._prepare_extensions(str(self._tree(extensions)))
        found = {a.name: a for a in discover(str(extensions))}
        instance = Loader().instantiate(found["pkg.nested.Nested"])
        assert type(instance).__module__ == "pkg.nested"
        assert instance.run() == "Nested"

    def test_the_parent_package_is_imported_first(self, extensions):
        """Loading `pkg.nested` by file location without `pkg` in sys.modules
        leaves a module whose parent is missing, and a relative import inside
        it fails on a name that is right there on disk."""
        from keyhac.mcp.extensions import Loader, discover
        package = extensions / "pkg"
        package.mkdir()
        (package / "__init__.py").write_text("VALUE = 'from the package'\n")
        write(package, "uses_parent", """
            from keyhac.core.action import ThreadedAction
            from . import VALUE

            class UsesParent(ThreadedAction):
                \"\"\"Imports from its own package.\"\"\"
                def run(self):
                    return VALUE
                def finished(self, result):
                    pass
        """)
        Keymap._prepare_extensions(str(extensions))
        found = {a.name: a for a in discover(str(extensions))}
        instance = Loader().instantiate(found["pkg.uses_parent.UsesParent"])
        assert instance.run() == "from the package"
