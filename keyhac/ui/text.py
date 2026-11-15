"""Text from elsewhere, made fit for the place it is drawn."""

import re

#: What `str.splitlines` splits on: every character that starts a new line for
#: a text renderer as well, not just the `\n` a name is likely to carry.
_LINE_BREAKS = re.compile(r"[\n\r\v\f\x1c-\x1e\x85  ]+")

#: One line break of any of those kinds, a Windows `\r\n` counting as one.
_LINE_BREAK = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def one_line(text: str) -> str:
    """`text` with each run of line breaks turned into one space.

    For a place that has room for one line and is showing text Keyhac did not
    write: an application is free to name a control `"Save\\nFile"`, and a window
    title or a tab's can hold the same.
    """
    return _LINE_BREAKS.sub(" ", text).strip()


def lf_lines(text: str) -> str:
    """`text` with each line break, whatever its kind, turned into `\\n`.

    For a place that draws several lines and splits them at `\\n` only: text
    copied on Windows ends its lines in `\\r\\n`, and a `\\r` left at the end
    of a line is a line break of its own to the text renderer - on Windows it
    failed the whole frame (issue #368).
    """
    return _LINE_BREAK.sub("\n", text)
