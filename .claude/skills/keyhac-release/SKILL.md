---
name: keyhac-release
description: Release a new version of Keyhac — confirm the version bump, run the pre-tag checks and account for the standing interactive pass, cut the tag, publish the GitHub Release with a hand-written note, and attach the macOS DMG, Windows zip, skill bundles, and PyPI wheel. Use when the user says "release Keyhac", "release a patch/minor/major version of Keyhac", or "ship keyhac X.Y.Z" — and also on the Windows machine afterwards, when they say "finish the Keyhac release on Windows" or "attach the Windows artifacts" (step 6: no new tag, just the Windows targets at the existing one).
---

# Releasing Keyhac

Run every command from the root of the keyhac checkout. One version
(`__version__` in `keyhac/__init__.py`) covers both OSes, but the artifacts
come from two machines — the DMG builds and notarizes on macOS, the win64 zip
and the Store MSIX on Windows — so a release usually finishes across two
sessions. Run each OS's targets on that OS and report the other side as
remaining; never mark the release done with artifacts missing. The pipeline is
documented in `doc/dev/packaging.md`; the note-writing step (4) is the one
part no Makefile target does.

## 1. Decide the version and confirm it

The single source of truth is `__version__` in `keyhac/__init__.py`
(pyproject derives from it via `dynamic = ["version"]`). Map the request:

- "patch" → bump Z (2.2.3 → 2.2.4)
- "minor" → bump Y, reset Z (2.2.3 → 2.3.0)
- "major" → bump X, reset Y and Z
- An explicit version wins; preflight also accepts pre-releases (`2.3.0a1`).

**Confirm the number with the user before proceeding.** State the mapping
explicitly — "current is X.Y.Z, this releases X.Y.Z′" — and wait for a yes
(AskUserQuestion in interactive sessions) before the step-2 checks, which cost
minutes, and long before `make tag`, which publishes: a pushed tag, and later
a PyPI version that can never be re-uploaded. A misread request must be caught
here, not discovered on PyPI.

Do not edit `__version__` yourself — `make tag` bumps, commits, and tags it.

## 2. Judgment checks before tagging

`tools/release_preflight.py` (run by `make tag`) enforces the mechanics: a
well-formed version strictly ahead of the current one, pyproject still
dynamic, on `main`, clean tree, tag free, not behind upstream. It also warns —
keyhac-specifically — when PuiKit is installed **editable**: the wheel this
release builds depends on the *published* PuiKit named by the `puikit>=`
pin, so if the code has come to rely on unreleased PuiKit changes, release
PuiKit first (its own skill: `puikit-release`) and raise the pin. Before
invoking any of that, do the checks preflight cannot:

- `git log v<current>..origin/main --oneline` — everything meant for this
  release is merged, nothing unexpected rode along. This list is also the raw
  material for the release note.
- `make api-reference-check` and `make icons-check` — the committed generated
  artifacts (`doc/config-api.md`, the rendered icons) must match their
  sources before they ship.
- **The standing interactive pass** — `doc/dev/testing.md`, "The interactive
  pass before a release". These are live checks no automated harness covers
  (tray menu and console interactions, IME driving the chooser, mouse feel,
  `tools/bundle_pass.py` against a bundle built from the PyPI PuiKit wheel,
  JIS layout when the keyboard exists), and Claude cannot run them — the
  agent shell never holds window-server key focus. Ask the user which passes
  have been done for this release; anything not repeated is *skipped and
  recorded* in the conversation, never silently assumed.
- Draft the release note now (style in step 4). Writing it forces a review of
  what is actually shipping while the tag is still retractable.

## 3. The Makefile pipeline

```
make tag VERSION=x.y.z    # preflight → pytest → bump __version__ → commit
                          # "Releasing x.y.z" → tag vx.y.z → build gate
                          # (sdist+wheel+twine check) → push commit and tag
make release-github       # create the Release (auto body — replaced in step 4)
make release-macos-dmg    # macOS: build.sh (sign/notarize) + create_dmg.sh,
                          # then attach the DMG to the Release
make release-whl          # HEAD must sit on the tag; sdist + wheel → PyPI,
                          # both attached to the Release
make release-skill        # build + attach the skill bundles. NOT optional:
                          # the bundles are the only way a user obtains the
                          # skills — tools/ and the Makefile never ship
make release-status       # read-only: which artifacts have landed
```

On the Windows machine, from the same checkout at the tag:
`make release-windows-zip` attaches the portable zip;
`make release-windows-msix` is the odd one out — it submits to the Microsoft
Store, not the GitHub Release, and certification proceeds asynchronously
(poll with `msstore submission status`).

- `make tag` commits directly to `main` — sanctioned for release commits only.
- Each target is independently re-runnable; `release-github` is idempotent.
  The DMG/zip file targets do **not** rebuild an existing artifact (so a
  re-upload never re-runs notarization); `make macos-dmg` / `make windows-zip`
  force a fresh build.
- Prereqs: `[pypi]` token in `~/.pypirc`, authenticated `gh`,
  `macos_app/signing.env` for sign/notarize, `windows_app/store.env` + the
  `msstore` CLI for the Store submission.

## 4. The hand-written release note (not in the Makefile)

`make release-github` creates the Release with GitHub's `--generate-notes` PR
list. That body is a placeholder — replace it with a hand-written note. The
model is v2.2.2 (v2.2.3 shipped with only the auto list; don't repeat that).

Style:

- Body starts `## Keyhac X.Y.Z`. Leave the Release title as `vX.Y.Z`.
- **Anything that changes behavior under an existing user goes first**, as a
  `>` blockquote addressed to them in second person ("If you turned X on, two
  things change under you"), saying what breaks, how it fails, and what to do.
- One bullet per user-visible change: a **bold headline in user terms**, then
  prose — the symptom as a user saw it, the cause in a clause, and what now
  happens. When a bullet is about an attached asset, name the file. Written
  for Keyhac users, not repo archaeologists.
- Minor items in one trailing "Also:" sentence.
- End with
  `**Full Changelog**: https://github.com/crftwr/keyhac/compare/vPREV...vNEW`.

Write the note to a scratch location outside the repo (it is never committed),
then:

```
gh release edit vX.Y.Z --notes-file <scratch>/release-note-x.y.z.md
```

## 5. After the release

- `make release-status` must show all of it: the DMG, the win64 zip, every
  skill bundle ("N of N attached" — its absence is the one that fails
  quietly, and a user connecting the MCP endpoint without the skill gets
  sleep-and-coordinates actions instead of an error), the sdist + wheel, and
  "PyPI: published".
- List what this machine could not produce (the other OS's artifacts, the
  Store submission) as explicitly remaining, with the commands the other
  session must run.

## 6. Finishing on Windows

The macOS session ends with the Windows artifacts listed as remaining; a later
session on the Windows machine enters here ("finish the Keyhac release on
Windows"). There is nothing to bump, tag, or confirm a version for — that all
happened — but there is still a publish gate: state which release is being
finished and get a yes before uploading.

1. Find the release being finished: `git fetch`, then `make release-status`
   on the latest tag (`git describe --tags --abbrev=0 origin/main`) — its
   missing Windows assets are the work. Confirm with the user.
2. Put the checkout on that tag's commit: `git pull` when `main` still sits
   on the release commit, `git checkout vX.Y.Z` when it has moved on. The
   artifacts must be built from the tagged code, not from whatever `main`
   has become.
3. `make release-windows-zip` — builds the bundle and the portable zip, and
   attaches the zip to the GitHub Release.
4. Ask whether to also submit to the Microsoft Store this time:
   `make release-windows-msix`. It is a separate publication with its own
   asynchronous certification (`msstore submission status`), not a Release
   asset, so it is a deliberate choice, not a default.
5. `make release-status` — the release is finished when everything shows:
   DMG, win64 zip, every skill bundle, sdist + wheel, PyPI published.
