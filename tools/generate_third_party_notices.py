#!/usr/bin/env python3
"""
Third-Party Notices Generator (platform-agnostic).

Scans one or more directories for installed Python distributions
(``*.dist-info``) and produces a single ``THIRD_PARTY_NOTICES`` text file that
reproduces every bundled component's license, plus any extra non-Python
components (fonts, the Python interpreter itself, an editable-installed library
whose source is copied in separately, ...) supplied on the command line.

It is deliberately dependency-free (stdlib only) and makes no OS assumptions, so
the same script drives the macOS ``build.sh`` and a Windows bundle build. Point
``--scan`` at each directory that ends up inside the shipped app (e.g. the
collected ``python_packages`` folder); add ``--extra`` for anything bundled that
is not a Python distribution.

License text is located from each ``.dist-info`` in this order:
  1. ``License-File:`` entries in ``METADATA`` (resolved against both the
     dist-info root and its PEP 639 ``licenses/`` subdirectory);
  2. failing that, a scan of the dist-info (and its ``licenses/`` subdir) for
     files whose names look like a license/notice/copying file.
The human-readable license *label* prefers ``License-Expression`` (SPDX), then a
short ``License:`` value, then ``License :: ...`` trove classifiers.

Verification guard: in the default strict mode the script exits non-zero and
lists any bundled distribution for which no license text could be found, so a
distributed build fails loudly rather than shipping an incomplete notices file.
Pass ``--allow-missing`` to downgrade that to a warning.

Identical license texts are de-duplicated: components that share byte-identical
license text are grouped under a single reproduced block (this keeps files with
many same-license distributions, e.g. the ~170 PyObjC framework wheels, both
complete and readable).

Usage:
    python tools/generate_third_party_notices.py \
        --title "Keyhac" \
        --scan  path/to/app/Resources/python_packages \
        --extra "Python interpreter (Python Software Foundation License)=.../lib/python3.14/LICENSE.txt" \
        --extra "PuiKit (MIT License)=.../puikit/LICENSE" \
        --extra "Noto Sans & Noto Sans Mono (SIL Open Font License 1.1)=.../puikit/fonts/OFL.txt" \
        --output path/to/app/Resources/THIRD_PARTY_NOTICES.txt

``--extra`` uses ``NAME=PATH`` (split on the first ``=``) so Windows drive-letter
paths such as ``C:\\...`` are handled correctly. Output is deterministic (sorted,
no embedded timestamp) so it is safe for reproducible builds.
"""

import argparse
import re
import sys
from email.parser import Parser
from pathlib import Path


# Filenames (case-insensitive, without directory) that look like a bundled
# license/notice when METADATA gives no explicit License-File pointer.
_LICENSE_STEMS = ("license", "licence", "copying", "copyright", "notice", "authors")


# ---------------------------------------------------------------------------
# SPDX fallback templates
# ---------------------------------------------------------------------------
# Some distributions declare a well-known license in their metadata but do NOT
# ship a copy of the text (e.g. most PyObjC framework wheels declare "MIT" with
# no LICENSE file). When we cannot recover the real text from the distribution
# itself or from a sibling in the same family, we reproduce the canonical SPDX
# license text so the notices file still carries the required permission notice.
# These are the standard SPDX templates; MIT/BSD/ISC keep the <year>/<holder>
# placeholders because the specific copyright line is not recoverable here.

_MIT_TEMPLATE = """\
MIT License

Copyright (c) <year> <copyright holders>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE."""

_ISC_TEMPLATE = """\
ISC License

Copyright (c) <year> <copyright holders>

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH
REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND
FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT,
INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF
THIS SOFTWARE."""

_BSD2_TEMPLATE = """\
BSD 2-Clause License

Copyright (c) <year> <copyright holders>

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""

_BSD3_TEMPLATE = """\
BSD 3-Clause License

Copyright (c) <year> <copyright holders>

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its contributors
   may be used to endorse or promote products derived from this software
   without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE."""

# Canonical SPDX id -> template text.
_SPDX_TEMPLATES = {
    "MIT": _MIT_TEMPLATE,
    "ISC": _ISC_TEMPLATE,
    "BSD-2-Clause": _BSD2_TEMPLATE,
    "BSD-3-Clause": _BSD3_TEMPLATE,
}


def _normalize_license_id(label: str):
    """
    Map a human/metadata license label onto a canonical SPDX id we can template.

    Returns the SPDX id (a key of _SPDX_TEMPLATES) or None when the label is
    unknown or too ambiguous to reproduce safely (e.g. a bare "BSD" that could
    be 2- or 3-clause).
    """
    if not label:
        return None
    text = label.lower()
    if "mit" in text:
        return "MIT"
    if "isc" in text:
        return "ISC"
    if "bsd" in text:
        if "2" in text or "simplified" in text or "freebsd" in text:
            return "BSD-2-Clause"
        if "3" in text or "new" in text or "revised" in text:
            return "BSD-3-Clause"
        return None  # bare "BSD" — don't guess the clause count
    return None


def _looks_like_license(filename: str) -> bool:
    """Return True if *filename* looks like a license/notice/copying file."""
    lower = filename.lower()
    # Match "LICENSE", "LICENSE.txt", "LICENSE-MIT", "COPYING.rst", "NOTICE", ...
    for stem in _LICENSE_STEMS:
        if lower == stem or lower.startswith(stem + ".") or lower.startswith(stem + "-"):
            return True
    # ... and trailing forms like "MIT.LICENSE".
    return lower.endswith(".license") or lower.endswith(".licence")


def _read_text(path: Path) -> str:
    """Read a text file tolerantly (license files are occasionally latin-1)."""
    data = path.read_bytes()
    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_metadata(dist_info: Path):
    """
    Parse a dist-info's METADATA/PKG-INFO into a dict of the fields we need.

    Returns a dict with: name, version, label (human-readable license label),
    and license_files (list of relative paths from License-File: headers).
    """
    meta_path = dist_info / "METADATA"
    if not meta_path.exists():
        meta_path = dist_info / "PKG-INFO"

    name = dist_info.name
    version = ""
    label = ""
    license_files = []

    if meta_path.exists():
        # RFC 822-style headers; email.parser handles the multi-value fields.
        msg = Parser().parsestr(_read_text(meta_path))
        name = msg.get("Name", name) or name
        version = msg.get("Version", "") or ""
        license_files = [v.strip() for v in msg.get_all("License-File", []) if v.strip()]

        expression = (msg.get("License-Expression") or "").strip()
        license_field = (msg.get("License") or "").strip()
        classifiers = [c for c in msg.get_all("Classifier", []) if c.startswith("License ::")]

        if expression:
            label = expression
        elif license_field and "\n" not in license_field and len(license_field) <= 64:
            # A short value like "MIT" or "Apache-2.0"; long values are the full
            # license text dumped into the field, which we skip here.
            label = license_field
        elif classifiers:
            # "License :: OSI Approved :: MIT License" -> "MIT License"
            labels = [c.split("::")[-1].strip() for c in classifiers]
            label = ", ".join(dict.fromkeys(labels))  # de-dupe, preserve order

    if not version and "-" in dist_info.stem:
        # Fall back to the "<name>-<version>.dist-info" convention.
        version = dist_info.stem.rsplit("-", 1)[-1]

    return {
        "name": name,
        "version": version,
        "label": label or "See license text",
        "license_files": license_files,
    }


def _collect_license_text(dist_info: Path, license_files) -> str:
    """
    Gather and concatenate all license text for a dist-info.

    Honors explicit License-File pointers first (checking both the dist-info
    root and its PEP 639 ``licenses/`` subdirectory), then falls back to a scan
    for license-looking files. Returns "" if nothing is found.
    """
    licenses_dir = dist_info / "licenses"
    seen = set()
    chunks = []

    def add(path: Path):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen or not path.is_file():
            return
        seen.add(resolved)
        text = _read_text(path).strip("\n")
        if text.strip():
            chunks.append(text)

    # 1. Explicit License-File pointers (relative paths, possibly with subdirs).
    for rel in license_files:
        rel = rel.lstrip("/\\")
        for base in (dist_info, licenses_dir):
            candidate = base / rel
            if candidate.is_file():
                add(candidate)
                break

    # 2. Fallback scan: dist-info root + licenses/ subtree.
    if not chunks:
        for base in (dist_info, licenses_dir):
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*") if base is licenses_dir else base.iterdir()):
                if path.is_file() and _looks_like_license(path.name):
                    add(path)

    return "\n\n".join(chunks)


def _parse_extra(spec: str):
    """Parse a ``NAME=PATH`` --extra spec into (name, Path)."""
    if "=" not in spec:
        raise argparse.ArgumentTypeError(
            f"--extra must be NAME=PATH, got: {spec!r}"
        )
    name, _, path = spec.partition("=")
    name = name.strip()
    path = path.strip()
    if not name or not path:
        raise argparse.ArgumentTypeError(f"--extra needs both a name and a path: {spec!r}")
    return name, Path(path)


def _family(name: str):
    """
    Distribution-family key: the part of a name before the first ``-``/``_``.

    Used to let a distribution that ships no license text borrow the real text
    from a sibling in the same family (e.g. every ``pyobjc-framework-*`` and
    ``pyobjc-core`` reuse the actual MIT text shipped by ``pyobjc-framework-
    Cocoa``). Returns None for very short keys to avoid accidental cross-family
    reuse.
    """
    key = re.split(r"[-_]", name.strip().lower(), maxsplit=1)[0]
    return key if len(key) >= 4 else None


def gather_components(scan_dirs, extras):
    """
    Build the component list from scanned dist-info dirs and explicit extras.

    Returns (components, missing). Each component is a dict with name, version,
    label and text. For distributions that declare a license but ship no text,
    the text is recovered from a sibling in the same family, or (failing that)
    from an SPDX template; only components that remain without any text land in
    *missing*.
    """
    raw = []          # every scanned distribution, text may be None
    components = []    # resolved, always has text
    missing = []

    # Pass 1: scan Python distributions (defer fallback until all are known).
    seen_dist = set()
    for scan_dir in scan_dirs:
        scan_dir = Path(scan_dir)
        if not scan_dir.is_dir():
            print(f"[WARNING] --scan directory not found: {scan_dir}", file=sys.stderr)
            continue
        for dist_info in sorted(scan_dir.glob("*.dist-info")):
            key = dist_info.name.lower()
            if key in seen_dist:
                continue
            seen_dist.add(key)
            meta = _parse_metadata(dist_info)
            text = _collect_license_text(dist_info, meta["license_files"]) or None
            raw.append({
                "name": meta["name"],
                "version": meta["version"],
                "label": meta["label"],
                "text": text,
                "family": _family(meta["name"]),
            })

    # Map each family to the real license text one of its members shipped.
    family_text = {}
    for entry in raw:
        if entry["text"] and entry["family"]:
            family_text.setdefault(entry["family"], entry["text"])

    # Pass 2: resolve missing text via family reuse, then SPDX template.
    for entry in raw:
        text = entry["text"]
        if text is None and entry["family"] in family_text:
            text = family_text[entry["family"]]
        if text is None:
            spdx = _normalize_license_id(entry["label"])
            if spdx:
                text = (
                    f'[This distribution declares the "{spdx}" license but did '
                    f"not include a copy of the license text. The standard {spdx} "
                    f"license text is reproduced below.]\n\n{_SPDX_TEMPLATES[spdx]}"
                )
        if text is None:
            missing.append(f"{entry['name']} {entry['version']}".strip())
            continue
        components.append({
            "name": entry["name"],
            "version": entry["version"],
            "label": entry["label"],
            "text": text,
        })

    # Explicit extras (non-Python components: fonts, interpreter, copied-in source).
    for name, path in extras:
        if not path.is_file():
            missing.append(f"{name} (missing file: {path})")
            continue
        components.append({
            "name": name,
            "version": "",
            "label": "",  # the name already carries the license summary
            "text": _read_text(path).strip("\n"),
        })

    return components, missing


def render(title, components):
    """Render the final notices document as a single string (deterministic)."""
    rule = "=" * 80
    thin = "-" * 80

    # De-duplicate identical license texts, grouping the components that share
    # them. Preserve first-seen order of unique texts; components are supplied
    # already sorted by the caller.
    groups = {}      # text -> list of "display" strings
    order = []        # unique texts in first-seen order
    for comp in components:
        display = comp["name"]
        if comp["version"]:
            display += f" {comp['version']}"
        if comp["label"]:
            display += f" — {comp['label']}"
        if comp["text"] not in groups:
            groups[comp["text"]] = []
            order.append(comp["text"])
        groups[comp["text"]].append(display)

    lines = [
        rule,
        "THIRD-PARTY SOFTWARE NOTICES",
        rule,
        "",
        f"{title} bundles the third-party components listed below. Each is the",
        "property of its respective copyright holders and is provided under the",
        "license reproduced with it.",
        "",
        "This file is generated by tools/generate_third_party_notices.py from the",
        "components actually bundled at build time. Do not edit it by hand.",
        "",
        "Bundled components",
        thin,
    ]

    # Table of contents: every component, sorted for a stable overview.
    toc = sorted(
        (d for displays in groups.values() for d in displays),
        key=str.lower,
    )
    lines.extend(f"  - {entry}" for entry in toc)
    lines.append("")

    # Reproduced license blocks (one per unique text).
    for text in order:
        displays = groups[text]
        lines.append(rule)
        if len(displays) == 1:
            lines.append(f"{displays[0]}")
        else:
            lines.append("The following components are provided under the license reproduced below:")
            lines.extend(f"  - {d}" for d in displays)
        lines.append(rule)
        lines.append("")
        lines.append(text)
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def main():
    parser = argparse.ArgumentParser(
        description="Generate a THIRD_PARTY_NOTICES file from bundled dist-info dirs and extras."
    )
    parser.add_argument("--title", default="This application",
                        help="Product name used in the notices header.")
    parser.add_argument("--scan", action="append", default=[], metavar="DIR",
                        help="Directory to scan for *.dist-info (repeatable).")
    parser.add_argument("--extra", action="append", default=[], type=_parse_extra,
                        metavar="NAME=PATH",
                        help="Non-Python component: display name = path to its license file (repeatable).")
    parser.add_argument("--output", required=True, metavar="FILE",
                        help="Path to write the generated notices file.")
    parser.add_argument("--allow-missing", action="store_true",
                        help="Warn instead of failing when a component has no license text.")
    args = parser.parse_args()

    if not args.scan and not args.extra:
        parser.error("nothing to do: provide at least one --scan or --extra")

    components, missing = gather_components(args.scan, args.extra)

    if missing:
        header = "[WARNING]" if args.allow_missing else "[ERROR]"
        print(f"{header} No license text found for {len(missing)} bundled component(s):",
              file=sys.stderr)
        for entry in missing:
            print(f"  - {entry}", file=sys.stderr)
        if not args.allow_missing:
            print("[ERROR] Refusing to write an incomplete notices file. Add an --extra "
                  "for the component or fix its packaging, or pass --allow-missing.",
                  file=sys.stderr)
            return 1

    if not components:
        print("[ERROR] No components with license text were found.", file=sys.stderr)
        return 1

    # Sort components by name (case-insensitive) for a stable, reproducible file.
    components.sort(key=lambda c: c["name"].lower())

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(args.title, components), encoding="utf-8")

    unique = len({c["text"] for c in components})
    print(f"[SUCCESS] Wrote {output} "
          f"({len(components)} components, {unique} distinct license texts).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
