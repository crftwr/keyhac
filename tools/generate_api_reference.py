"""Generate doc/config-api.md from the docstrings of the public API.

Run it with `make api-reference`; `make api-reference-check` verifies that the
committed file still matches the source, the same way `icons-check` does for
the generated icon assets.

The pipeline is keyhac-mac's (lazydocs' MarkdownGenerator over a hand-curated
list of names), minus its DocumentSource stub package: keyhac-mac had to hand
Python a stub module for keyhac_core, whose real implementation was Swift and
not introspectable. Keyhac 2 is pure Python by policy, so importing the package
is enough.

WHAT LANDS IN THE REFERENCE. API_NAMES below picks the classes and functions;
within each class, lazydocs documents every member that does not start with an
underscore. Several of those are public only in the naming sense - hook
callbacks, wiring called by main(), constructors of objects a configuration
receives rather than builds. Each of those carries a `lazydocs: ignore` line in
its docstring, which is lazydocs' own opt-out marker, so the decision sits next
to the definition instead of in a list here that would silently drift.

Keeping the two apart matters: this file answers "which symbols does a
configuration author see", and the marker answers "is this member part of that
symbol's surface".

WRITING EXAMPLES. Put them in the docstring body as a ```python fence, not
under a Google-style `Example:` heading - lazydocs flattens the lines of a
heading block onto one line, which turns a code sample into an unreadable
run-on. Inside a fence it also adds one leading space to every line that is
indented at all; that shifts nested lines uniformly (harmless), but it breaks
a continuation line aligned to an opening parenthesis by one column, so keep
example statements on a single line.

Two more things lazydocs reads structurally rather than as prose, anywhere in
a docstring: a line starting with "-" becomes a bullet, and inside an
Args:/Attributes: block a line starting with "word:" starts a new entry. Both
are what you want when you mean them; a *continuation* line that begins that
way silently splits the entry it belongs to. Wrap those lines differently.
"""

import os
import sys

# https://github.com/ml-tooling/lazydocs
from lazydocs import MarkdownGenerator

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import keyhac
from keyhac.platform.base import Window

# Config-facing API, grouped the way doc/configuration.md introduces it rather
# than alphabetically: someone reading top to bottom meets the keymap first and
# the leaf actions last.
API_NAMES = [
    # The engine and its collaborators
    "Keymap",
    "KeyTable",
    "KeyCondition",
    "FocusCondition",
    "InputContext",
    "Focus",
    "KeyEvent",
    "Window",
    # Actions
    "ThreadedAction",
    "InputText",
    "LaunchApplication",
    "ActivateWindow",
    "MoveWindow",
    "SnapWindow",
    "MouseMove",
    "MouseButtonDown",
    "MouseButtonUp",
    "MouseButtonClick",
    "MouseWheel",
    "MouseHorizontalWheel",
    "StartRecordingKeys",
    "StopRecordingKeys",
    "ToggleRecordingKeys",
    "PlaybackRecordedKeys",
    # Clipboard
    "ClipboardHistory",
    "ChooserAction",
    "ShowClipboardHistory",
    "ShowClipboardSnippets",
    "ShowClipboardTools",
    "DateTimeSnippet",
    # Logging
    "getLogger",
    "Console",
]

HEADER = """# API reference

Every class and function a `config.py` can reach, generated from the
docstrings. It answers "what are the arguments of X"; for "how do I do Y",
read [Configuration](configuration.md) first — it introduces these APIs in the
order you meet them, with worked examples.

"""

FOOTER = """Generated from the docstrings by `make api-reference`. Edit the
docstrings, not this file.
"""

#: The action API is generated into its own document. It is a different
#: audience with a different question: a config author asks "how do I bind a
#: key", an action asks "how do I read that table". Mixing them is what grew a
#: `press` and a `focus` into the config namespace in the first place.
ACTION_API_NAMES = [
    "UI",
    "UINode",
    "WaitTimeout",
    "FillFailed",
    "ActionCancelled",
    "StaleElement",
]

ACTION_HEADER = """# Action API reference

The surface an action uses to drive another application: finding windows,
searching element trees, waiting for the screen to change, filling fields.
Reached through `keymap.ui` (or `self.ui` inside a `ThreadedAction`) and the
methods on the nodes it hands back — the three names below are the only ones a
config imports.

Generated from the docstrings. For how to *write* an action, the authoring
skill in `keyhac/skills/keyhac-action-authoring/` is the procedural half, and
`examples/actions/` holds working ones.

> **Experimental.** This surface may change in ways a release number normally
> promises it will not — 2.2.x is the line this feature is being built in, so
> even a patch release can move it — and an upgrade may require editing
> actions you have written. The
> unsettled part is `UINode` itself — how an element is identified, and how
> long a node you are holding stays valid — which is the shape everything
> below is built on. The rest of Keyhac's API is not affected; see
> [AI Integration](ai-integration.md) for what this covers and what it
> would take to settle it.

**Cross-platform by shape, not by data.** Every method here exists and behaves
the same on Windows and macOS. What differs is the tree it reads: roles are
`AXTable` / `Table`, macOS keeps a control's state in one value where Windows
splits it across Value, ToggleState and IsSelected, and neither platform's
attribute names mean anything to the other. An action is written against a
screen that was inspected first, so it is not portable — the framework is.
`UI.enable_content_access()` is the one deliberately one-sided call, exposed so
an action can make it unconditionally.

"""


class _Generator(MarkdownGenerator):
    """MarkdownGenerator that drops two kinds of members lazydocs would emit.

    lazydocs documents every non-underscore member whose defining module is
    the class's own, which pulls in more than the class's own surface:

    * Members a *documented* base class already covers, e.g. the three
      ThreadedAction hooks reappearing under LaunchApplication. Members
      inherited from an undocumented base are kept - MouseButtonDown's
      constructor comes from the private _MouseButtonAction, and it is the
      only place its `button` argument is described.
    * Constructors with no docstring of their own: the dataclass-generated
      __init__ of Focus and KeyEvent, which would print a bare signature
      restating the Attributes block above it.

    class2md skips a member whose markdown is empty, separator included, so
    returning "" is all it takes.
    """

    def func2md(self, func, clsname: str = "", depth: int = 3) -> str:
        if clsname:
            owner = getattr(func, "__qualname__", "").rsplit(".", 1)[0]
            if owner != clsname and owner in API_NAMES:
                return ""
            if func.__name__ == "__init__" and not func.__doc__:
                return ""
        return super().func2md(func, clsname=clsname, depth=depth)


def _resolve(name):
    """The object a name in API_NAMES refers to.

    Everything is exported from the package except Window and UI, which a
    configuration only ever receives - from keymap.get_active_window() and
    from keymap.ui respectively - so neither is in `keyhac.__all__`.
    """
    if name == "Window":
        return Window
    if name == "UI":
        from keyhac.core.ui import UI
        return UI
    return getattr(keyhac, name)


def _anchor(name):
    """GitHub's anchor for the heading lazydocs emits for `name`.

    The headings carry a <kbd>class</kbd> / <kbd>function</kbd> badge, and
    GitHub builds the anchor from the visible text with the markup stripped:
    "## <kbd>class</kbd> `Keymap`" becomes "#class-keymap".
    """
    obj = _resolve(name)
    kind = "class" if isinstance(obj, type) else "function"
    return f"#{kind}-{name.lower()}"


def generate(names=None, header=None) -> str:
    generator = _Generator()

    names = names or API_NAMES
    parts = [header or HEADER]

    parts.append("**Contents:** ")
    parts.append(" · ".join(f"[{name}]({_anchor(name)})" for name in names))
    parts.append("\n\n")

    for name in names:
        print(f"Generating API reference for {name}")
        obj = _resolve(name)
        if isinstance(obj, type):
            markdown = generator.class2md(obj, depth=2)
        else:
            markdown = generator.func2md(obj, depth=2)
        parts.append(markdown.rstrip("\n"))
        parts.append("\n\n---\n\n")

    parts.append(FOOTER)
    return "".join(parts)


DOCUMENTS = [
    ("config-api.md", None, None),
    ("action-api.md", "ACTION", "ACTION"),
]


def main() -> int:
    check = "--check" in sys.argv[1:]
    failed = 0
    for filename, names_key, header_key in DOCUMENTS:
        names = ACTION_API_NAMES if names_key else API_NAMES
        header = ACTION_HEADER if header_key else HEADER
        failed += _one(filename, names, header, check)
    return 1 if failed else 0


def _one(filename, names, header, check) -> int:
    output_path = os.path.join(
        os.path.dirname(__file__), "..", "doc", filename)
    generated = generate(names, header)

    if check:
        try:
            with open(output_path, encoding="utf-8") as fd:
                committed = fd.read()
        except OSError as e:
            print(f"ERROR: cannot read {output_path}: {e}")
            return 1
        if committed != generated:
            print(f"ERROR: {os.path.relpath(output_path)} is out of date; "
                  f"run 'make api-reference'.")
            return 1
        print(f"{os.path.relpath(output_path)} is up to date.")
        return 0

    with open(output_path, "w", encoding="utf-8") as fd:
        fd.write(generated)
    print(f"Wrote {os.path.abspath(output_path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
