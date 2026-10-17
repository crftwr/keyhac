"""A settings walk: open every tab, wait for it, key values by what they are."""

import json

TITLE = "Back up every tab of a settings page to JSON"
#: Only the open tab's fields are on the page, and a tab's panel is emptied
#: before it is refilled - so one read of the window cannot hold all three.
SCREEN = {"pages": ["settings.html"]}
PROMPT = """\
Back up every setting in the Preferences page open in my browser - all of its
tabs - to preferences.json on my Desktop, so I can check nothing changed after
the next update.
"""
MUST = """\
- preferences.json holds all nine values from all three tabs - General,
  Network, Advanced - with each checkbox as on or off.
- It opens each tab and waits for that tab's fields before reading, and keys
  each value by its label or id, not by its position.
- The tab buttons themselves are not recorded as settings.
Must not: read the window once and assume it holds tabs nobody opened.
"""

#: (any of these in the value's path, the value) - a path is the keys leading
#: to a value, lowercased, so {"General": {"Display name": ...}} and
#: {"name": ...} both find the first one.
TEXT = [
    (("display name", "name"), "Acme workstation"),
    (("theme",), "dark"),
    (("proxy",), "proxy.acme.test:8080"),
    (("timeout",), "30"),
    (("cache",), "512"),
    (("log level", "logs"), "warning"),
]
CHECKED = [
    (("open at login", "startup"), True),
    (("work offline", "offline"), False),
    (("beta",), False),
]
TABS = ("general", "network", "advanced")


def _leaves(value, path=()):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield from _leaves(inner, path + (str(key).lower(),))
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            # A list of {"label": ..., "value": ...} records keys itself.
            if isinstance(inner, dict):
                named = next((str(inner[k]).lower() for k in ("label", "name", "id", "key")
                              if k in inner and not isinstance(inner[k], (dict, list))), None)
                if named is not None and any(k in inner for k in ("value", "checked", "state")):
                    yield from _leaves({k: v for k, v in inner.items()
                                        if k in ("value", "checked", "state")},
                                       path + (named,))
                    continue
            yield from _leaves(inner, path + (str(index),))
    else:
        yield path, value


def _on(value):
    text = str(value).strip().lower()
    if text in ("true", "on", "checked", "1", "yes", "selected"):
        return True
    if text in ("false", "off", "unchecked", "0", "no", "", "none", "not checked"):
        return False
    return None


def check(o):
    text = o.output("preferences.json")
    if text is None:
        return ["no preferences.json on the Desktop"]
    try:
        data = json.loads(text)
    except ValueError as error:
        return [f"preferences.json is not JSON: {error}"]
    leaves = list(_leaves(data))
    failures = []
    for names, want in TEXT:
        if not any(str(value) == want and any(n in " ".join(path) for n in names)
                   for path, value in leaves):
            failures.append(f"preferences.json has no {names[0]} = {want!r}")
    for names, want in CHECKED:
        found = [_on(value) for path, value in leaves
                 if any(n in " ".join(path) for n in names)]
        if want not in found:
            failures.append(f"preferences.json does not record {names[0]} as "
                            f"{'on' if want else 'off'}")
    joined = " ".join(" ".join(path) for path, _ in leaves).lower()
    if missing := [tab for tab in TABS if tab not in joined]:
        failures.append(f"preferences.json does not say which tab "
                        f"{', '.join(missing)} values came from")
    return failures


def reference(o):
    o.outputs["preferences.json"] = json.dumps({
        "General": {"Display name": "Acme workstation", "Theme": "dark",
                    "Open at login": True},
        "Network": {"Proxy": "proxy.acme.test:8080", "Timeout (s)": "30",
                    "Work offline": False},
        "Advanced": {"Cache size (MB)": "512", "Log level": "warning",
                     "Beta features": False},
    }, indent=2)
    return o
