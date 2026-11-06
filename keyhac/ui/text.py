"""Text from elsewhere, made fit for a one-line place to draw it."""

import re

#: What `str.splitlines` splits on: every character that starts a new line for
#: a text renderer as well, not just the `\n` a name is likely to carry.
_LINE_BREAKS = re.compile(r"[\n\r\v\f\x1c-\x1e\x85  ]+")


def one_line(text: str) -> str:
    """`text` with each run of line breaks turned into one space.

    For a place that has room for one line and is showing text Keyhac did not
    write: an application is free to name a control `"Save\\nFile"`, and a window
    title or a tab's can hold the same.
    """
    return _LINE_BREAKS.sub(" ", text).strip()
