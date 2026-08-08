VENV := .venv

# Same Windows detection as puikit's Makefile: Git Bash/MSYS2/Cygwin report
# MINGW/MSYS/CYGWIN via `uname -s`; native GNU Make ports without `uname`
# fall back to $(OS).
UNAME_S := $(shell uname -s 2>/dev/null)
ifneq (,$(findstring MINGW,$(UNAME_S))$(findstring MSYS,$(UNAME_S))$(findstring CYGWIN,$(UNAME_S)))
    IS_WINDOWS := 1
else ifeq ($(OS),Windows_NT)
    IS_WINDOWS := 1
endif

# Keyhac 2 targets Python 3.13 (the version embedded in the shipped app).
ifeq ($(IS_WINDOWS),1)
    PY_ON_PATH := $(shell command -v python3.13 2>/dev/null)
    ifneq ($(strip $(PY_ON_PATH)),)
        PYTHON := python3.13
    else
        PYTHON := py
    endif
    VENV_PYTHON := $(VENV)/Scripts/python.exe
    VENV_PIP := $(VENV)/Scripts/pip.exe
else
    PYTHON := python3.13
    VENV_PYTHON := $(VENV)/bin/python
    VENV_PIP := $(VENV)/bin/pip
endif

EXTRAS := dev

# --- PuiKit source: PyPI by default, local editable checkout on opt-in --------
# PuiKit is released on PyPI, so `make venv` installs it from there by default.
# To develop against a local PuiKit checkout, set PUIKIT_DIR to its path — PuiKit
# is then installed *editable* from there (live edits, no reinstall). Declare it
# once, persistently, without editing this file, in either way:
#   * Makefile.local (gitignored):   PUIKIT_DIR = ../puikit
#   * or your environment:           export PUIKIT_DIR=../puikit
# venv / install then honour it. On demand, `make install-puikit` (re)installs
# PuiKit into an existing $(VENV) from whichever source PUIKIT_DIR selects right
# now — set it for editable, unset for the released PyPI build.
-include Makefile.local
PUIKIT_DIR ?=

# Extra arguments for run targets, e.g. `make run ARGS="-d"`.
ARGS :=

# Sandbox config for testing without touching ~/.keyhac/config.py (which may
# still belong to keyhac-mac / keyhac-win). Created from the template on
# first run; git-ignored.
SANDBOX_CONFIG := .sandbox/config.py

# File-based stamp (same pattern as puikit): run/test targets auto-create the
# venv and install keyhac the first time, and re-install only when
# pyproject.toml changes.
VENV_STAMP := $(VENV)/.installed

.PHONY: help venv install install-puikit check-venv test run run-debug run-sandbox echo icons icons-check \
        api-reference api-reference-check skill-bundle \
        clean clean-venv clean-macos clean-windows clean-windows-cache \
        tag release-github release-whl release-skill release-status build publish-testpypi \
        macos-app macos-dmg install-macos-dmg uninstall-macos-dmg release-macos-dmg \
        windows-app windows-zip install-windows-zip uninstall-windows-zip release-windows-zip \
        windows-msix install-windows-msix uninstall-windows-msix release-windows-msix

help:
	@echo "Keyhac 2 utility commands:"
	@echo "  make venv        - create the virtualenv and install keyhac ($(VENV)/, $(PYTHON))"
	@echo "  make install     - (re)install keyhac into the venv (editable, with dev deps)"
	@echo "  make install-puikit - (re)install PuiKit into the venv: editable from"
	@echo "                     PUIKIT_DIR if set, else the released build from PyPI"
	@echo "  make test        - run the engine test suite (no permissions needed)"
	@echo "  make run         - run Keyhac with ~/.keyhac/config.py"
	@echo "  make run-debug   - run Keyhac with debug logging (key events, dispatch)"
	@echo "  make run-sandbox - run Keyhac with a sandbox config ($(SANDBOX_CONFIG)),"
	@echo "                     leaving ~/.keyhac/config.py alone"
	@echo "  make echo        - run the hook echo tool (platform layer only, never"
	@echo "                     consumes keys; use this first on a new machine/OS)"
	@echo "  make icons       - regenerate the committed icon assets from art/*.svg"
	@echo "  make icons-check - verify the committed icon assets match the SVG masters"
	@echo "  make skill-bundle        - package the authoring skill for Claude Desktop upload"
	@echo "  make api-reference       - regenerate doc/api-reference.md from the docstrings"
	@echo "  make api-reference-check - verify doc/api-reference.md matches the docstrings"
	@echo "  make clean       - remove build artifacts and caches (keeps $(VENV)/)"
	@echo "  make clean-venv  - remove the virtualenv"
	@echo ""
	@echo "  Run targets accept ARGS, e.g. make run-sandbox ARGS=-d"
	@echo "  PuiKit installs from PyPI by default; to develop against a local"
	@echo "  checkout, set PUIKIT_DIR (Makefile.local / env), e.g. PUIKIT_DIR=../puikit"
	@echo "  macOS: the terminal app needs the Accessibility permission."
	@echo "  NOTE: quit Keyhac 1.x (keyhac-mac / keyhac-win) first - two keyboard"
	@echo "  hooks processing the same keys will conflict."
	@echo ""
	@echo "  App bundles:"
	@echo "  make macos-app / macos-dmg           - (on macOS)   build Keyhac.app / its DMG"
	@echo "  make windows-app / windows-zip       - (on Windows) build the bundle / its zip"
	@echo "  make windows-msix                    - (on Windows) pack the bundle as an unsigned"
	@echo "                                         MSIX (Store submission form; SIGN=1 to self-sign)"
	@echo "  make install-macos-dmg / install-windows-zip     - install the built artifact"
	@echo "  make install-windows-msix   - pack + self-sign, trust cert (elevates), install per-user"
	@echo "  make uninstall-macos-dmg / uninstall-windows-zip - remove that install"
	@echo "  make uninstall-windows-msix - remove the MSIX package + throwaway signing cert"
	@echo "  make clean-macos / clean-windows / clean-windows-cache"
	@echo ""
	@echo "  Release (one target per artifact; run in this order):"
	@echo "  make tag VERSION=x.y.z - bump __version__, commit, tag, push (no publishing)"
	@echo "  make release-github    - open the GitHub Release at that tag"
	@echo "  make release-whl       - upload sdist + wheel to PyPI, and to the Release"
	@echo "  make release-skill     - attach the AI authoring skill bundle to the Release"
	@echo "  make release-macos-dmg   - (on macOS)   attach Keyhac-<ver>-macos.dmg"
	@echo "  make release-windows-zip - (on Windows) attach Keyhac-<ver>-win64.zip"
	@echo "  make release-windows-msix - (on Windows) submit the MSIX to the Microsoft Store"
	@echo "  make release-status    - show which artifacts have landed so far"
	@echo "  Supporting: make build (sdist + wheel into dist/, also a tag gate),"
	@echo "  make publish-testpypi (rehearsal upload to TestPyPI)."

$(VENV_STAMP): pyproject.toml $(if $(PUIKIT_DIR),$(PUIKIT_DIR)/pyproject.toml)
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m pip install --upgrade pip
	@$(MAKE) install-puikit
	$(VENV_PIP) install -e ".[$(EXTRAS)]"
	@touch $(VENV_STAMP)

venv: $(VENV_STAMP)

install: $(VENV_STAMP)

# Guard: fail with a clear message instead of half-running against a missing venv.
check-venv:
	@if [ ! -x $(VENV_PYTHON) ]; then \
		echo "Error: $(VENV) not found. Run 'make venv' to create it first."; \
		exit 1; \
	fi

# Install PuiKit into $(VENV) from the source chosen by PUIKIT_DIR: a local
# editable checkout when PUIKIT_DIR is set (live edits, no reinstall), otherwise
# the released build from PyPI. Idempotent — skips the install when PuiKit is
# already present from the selected source. Run standalone to switch an existing
# venv between the two; also run automatically by venv / install.
install-puikit: check-venv
	@info=$$($(VENV_PIP) show puikit 2>/dev/null); \
	editloc=$$(echo "$$info" | sed -n 's/^Editable project location: //p'); \
	if [ -n "$(PUIKIT_DIR)" ]; then \
		if [ ! -d "$(PUIKIT_DIR)" ]; then \
			echo "Error: PuiKit not found at '$(PUIKIT_DIR)'. Set PUIKIT_DIR to your checkout."; \
			exit 1; \
		fi; \
		want=$$(cd "$(PUIKIT_DIR)" && pwd -P); \
		have=""; \
		[ -n "$$editloc" ] && [ -d "$$editloc" ] && have=$$(cd "$$editloc" && pwd -P); \
		if [ -n "$$have" ] && [ "$$have" = "$$want" ]; then \
			echo "PuiKit already editable from $$want; skipping."; \
		else \
			echo "Installing PuiKit (editable) from $(PUIKIT_DIR)..."; \
			$(VENV_PIP) install -e "$(PUIKIT_DIR)"; \
		fi; \
	else \
		if [ -n "$$info" ] && [ -z "$$editloc" ]; then \
			echo "PuiKit already installed from PyPI; skipping."; \
		else \
			echo "Installing PuiKit from PyPI..."; \
			$(VENV_PIP) install --force-reinstall --no-deps "puikit>=1.0.6"; \
		fi; \
	fi

test: $(VENV_STAMP)
	$(VENV_PYTHON) -m pytest

run: $(VENV_STAMP)
	$(VENV_PYTHON) -m keyhac $(ARGS)

run-debug: $(VENV_STAMP)
	$(VENV_PYTHON) -m keyhac -d $(ARGS)

run-sandbox: $(VENV_STAMP)
	@mkdir -p $(dir $(SANDBOX_CONFIG))
	$(VENV_PYTHON) -m keyhac --config $(SANDBOX_CONFIG) $(ARGS)

echo: $(VENV_STAMP)
	$(VENV_PYTHON) tools/hook_echo.py

# The generated icon assets are committed; `icons` regenerates them from the
# SVG masters, `icons-check` verifies the two have not drifted.
icons: $(VENV_STAMP)
	$(VENV_PYTHON) tools/make_icons.py

icons-check: $(VENV_STAMP)
	$(VENV_PYTHON) tools/make_icons.py --check

# doc/api-reference.md is generated from the docstrings and committed, on the
# same terms as the icon assets above: `api-reference` regenerates it,
# `api-reference-check` fails if the committed file and the source have drifted.
#
# lazydocs is documentation tooling, not needed to run or develop Keyhac, so it
# is installed on demand here rather than sitting in the dev extras - the same
# call the `build` target makes for build/twine.
# The authoring skill has to be a zip before Claude Desktop will take it
# (Settings -> Skills -> Add -> Upload skill).
skill-bundle: $(VENV_STAMP)
	$(VENV_PYTHON) tools/build_skill_bundle.py

api-reference: $(VENV_STAMP)
	@$(VENV_PIP) install --quiet lazydocs
	$(VENV_PYTHON) tools/generate_api_reference.py

api-reference-check: $(VENV_STAMP)
	@$(VENV_PIP) install --quiet lazydocs
	$(VENV_PYTHON) tools/generate_api_reference.py --check

# `clean` sweeps everything a plain `make` rebuilds from the checkout — the
# Python build tree plus both app-bundle build dirs. Kept out on purpose
# (restoring them needs the network): the venv and the CPython-embeddable
# download cache; `clean` names both so "everything" is one command away.
clean: clean-macos clean-windows
	rm -rf build dist *.egg-info
	rm -f README.pypi.md
	find . -name __pycache__ -type d -not -path "./$(VENV)/*" -exec rm -rf {} +
	rm -rf .pytest_cache
	@echo ""
	@echo "Cleaned. Kept (each needs the network to restore — remove explicitly):"
	@echo "  $(VENV)/               -> make clean-venv"
	@echo "  windows_app/.cache/   -> make clean-windows-cache"

# Kept out of `clean`: restoring the venv needs the network.
clean-venv:
	rm -rf $(VENV)

# ============================================================================
# Packaging / Release Targets  (structure ported from XeFM's Makefile)
# ============================================================================
# Releasing is one target per artifact, each independently runnable:
#
#   make tag VERSION=x.y.z     any machine  bump __version__, commit, tag, push
#   make release-github        any machine  open the GitHub Release at that tag
#   make release-whl           any machine  sdist + wheel -> PyPI (+ the Release)
#   make release-skill         any machine  the AI authoring skill -> the Release
#   make release-status        any machine  what has landed so far
#
#   make release-macos-dmg     macOS        Keyhac-<ver>-macos.dmg -> the Release
#   make release-windows-zip   Windows      Keyhac-<ver>-win64.zip -> the Release
#   make release-windows-msix  Windows      Keyhac-<ver>.0-x64.msix -> the Microsoft Store
#
# Order matters only twice: `tag` first (everything else names the tag it
# creates), then `release-github` (the release-<artifact> targets upload into
# the Release it opens). The release-<artifact> targets are peers — they run in
# any order, on their own machine, because the artifacts build on different
# platforms. Each one builds its artifact if it is missing, re-checks the
# preconditions, and uploads with --clobber, so re-running any of them is safe.
#
# release-windows-msix is the odd one out: it publishes to the Microsoft Store,
# not the GitHub Release, so it needs no `release-github` — and it always
# repacks rather than reusing an existing .msix, since the Store rejects a
# resubmitted package version anyway (each submission must be strictly higher).
#
# The version's single source of truth is keyhac/__init__.py's __version__;
# pyproject.toml derives it (dynamic version = attr) and the M5 bundle
# builders will extract that same literal. KEYHAC_VERSION below reads it the
# same way, so every release-* target acts on the release the checkout is
# actually on — only `tag` takes a VERSION=. Override it on the others to
# target a different release (e.g. re-uploading an asset for an older tag).
KEYHAC_VERSION := $(if $(VERSION),$(VERSION),$(shell sed -nE 's/^__version__[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' keyhac/__init__.py 2>/dev/null | head -1))

# Guards shared by the release-* targets, kept in one place so they cannot
# drift into checking different things. Used as $(call ...) inside a recipe;
# each expands to a single multi-line shell test.
#
# check_gh:             a resolvable version and a usable `gh`.
# check_release_exists: the above, plus the GitHub Release to upload into.
define check_gh
test -n "$(KEYHAC_VERSION)" || { echo "ERROR: could not determine version; pass VERSION=x.y.z"; exit 1; }; \
command -v gh >/dev/null 2>&1 || { echo "ERROR: 'gh' not found. Install the GitHub CLI first."; exit 1; }; \
gh auth status >/dev/null 2>&1 || { echo "ERROR: 'gh' is not authenticated. Run 'gh auth login'."; exit 1; }
endef

define check_release_exists
$(check_gh); \
gh release view v$(KEYHAC_VERSION) >/dev/null 2>&1 || { \
	echo "ERROR: GitHub Release v$(KEYHAC_VERSION) does not exist."; \
	echo "       Open it first with 'make release-github'."; \
	exit 1; \
}
endef

# --- tag: the one target that changes the version ---------------------------
# Usage: make tag VERSION=2.0.0a1
#
# Pure version + git work: bump __version__, commit, tag, push. It publishes
# nothing and needs no `gh` and no PyPI token — the release-* targets do the
# publishing, each with its own toolchain and credentials.
#
# release_preflight.py runs FIRST and aborts before any mutation if the tree
# is dirty, the version is stale, or the tag exists — so a failed precondition
# never leaves a half-cut release. The test suite must pass before anything is
# built.
#
# `make build` runs before the pushes purely as a gate: it proves the sdist
# and wheel build and pass `twine check` while the tag is still local and
# retractable. It also leaves dist/ ready for `make release-whl`.
tag: $(VENV_STAMP)
	@test -n "$(VERSION)" || { echo "ERROR: set VERSION, e.g. make tag VERSION=2.0.0a1"; exit 1; }
	$(VENV_PYTHON) tools/release_preflight.py "$(VERSION)"
	$(VENV_PYTHON) -m pytest
	$(VENV_PYTHON) tools/bump_version.py "$(VERSION)"
	git add keyhac/__init__.py
	git commit -m "Releasing $(VERSION)"
	git tag -a v$(VERSION) -m "$(VERSION)"
	$(MAKE) build
	git push
	git push origin v$(VERSION)
	@echo ""
	@echo "Tagged $(VERSION): commit + tag v$(VERSION), both pushed"
	@echo "Next:"
	@echo "  make release-github          # open the GitHub Release at v$(VERSION)"
	@echo "  make release-whl             # sdist + wheel -> PyPI"

# --- release-github: open the Release the artifacts upload into -------------
# Reads the version from the checkout, so the usual path is `make tag` then
# `make release-github` with no arguments. --verify-tag refuses to invent a
# tag GitHub does not already have, which is why `tag` pushes it first.
#
# Idempotent on purpose: an existing Release is reported and left alone rather
# than erroring, so re-running the pipeline from the top costs nothing.
release-github:
	@$(call check_gh)
	@git ls-remote --exit-code --tags origin "v$(KEYHAC_VERSION)" >/dev/null 2>&1 || { \
		echo "ERROR: tag v$(KEYHAC_VERSION) is not on origin."; \
		echo "       Push it with 'make tag VERSION=$(KEYHAC_VERSION)' (or 'git push origin v$(KEYHAC_VERSION)')."; \
		exit 1; \
	}
	@if gh release view v$(KEYHAC_VERSION) >/dev/null 2>&1; then \
		echo "GitHub Release v$(KEYHAC_VERSION) already exists; leaving it as is."; \
	else \
		gh release create v$(KEYHAC_VERSION) --title "v$(KEYHAC_VERSION)" --generate-notes --verify-tag && \
		echo "Opened GitHub Release v$(KEYHAC_VERSION)"; \
	fi

# --- The Python distributions -----------------------------------------------
# `build` and `twine` are release-time tooling, not needed to run or develop
# Keyhac, so they are installed on demand here rather than sitting in the dev
# extras. Invoked as `python -m ...` (not the venv's console scripts) so the
# same recipe works on Windows, where those scripts live in Scripts/ and end
# in .exe.
# The PyPI long description (README.pypi.md) is generated here on the fly and
# never committed: README.md keeps repo-relative image/link targets for GitHub,
# and gen_pypi_readme.py rewrites them to version-tagged GitHub URLs so they
# render on the PyPI page. `twine check --strict` promotes twine's
# "description missing" warning to a failure, so a build that somehow skipped
# generation can never upload an empty description.
build: $(VENV_STAMP)
	@echo "Building sdist + wheel..."
	@$(VENV_PIP) install --quiet build twine
	rm -rf dist build *.egg-info
	$(VENV_PYTHON) tools/gen_pypi_readme.py
	$(VENV_PYTHON) -m build
	$(VENV_PYTHON) -m twine check --strict dist/*

# The safe rehearsal for release-whl: same build and upload path, but a bad
# TestPyPI version costs nothing. Deliberately NOT named release-* — it needs
# neither a tag nor a GitHub Release and publishes nothing permanent, so it is
# a pre-release smoke test rather than a step of the pipeline. Depends on
# `build` (not on the file target below) so it always builds fresh.
publish-testpypi: build
	$(VENV_PYTHON) -m twine upload -r testpypi dist/*

# The filenames setuptools gives the sdist + wheel, derived from the same
# version literal as KEYHAC_VERSION. Naming them explicitly (rather than
# globbing dist/*) means a stale artifact left from an earlier version can
# never be swept into an upload.
PYPI_SDIST := dist/keyhac-$(KEYHAC_VERSION).tar.gz
PYPI_WHEEL := dist/keyhac-$(KEYHAC_VERSION)-py3-none-any.whl

# File target so release-whl builds the distributions on demand when they are
# missing (e.g. after `make clean`). `make build` wipes dist/ and writes both
# files, so the sdist alone is enough of a prerequisite to trigger it; the
# recipe below then asserts the wheel landed too. Existing artifacts are NOT
# rebuilt — publishing the exact bytes that were verified is the point.
$(PYPI_SDIST):
	@echo "Python distributions for $(KEYHAC_VERSION) not found; building them first..."
	@$(MAKE) build

# --- release-whl: publish the Python distributions --------------------------
# Uploads BOTH the sdist and the wheel — the target is named for the headline
# artifact, not the whole payload.
#
# A PyPI version can never be re-uploaded, so this refuses to publish a build
# that is not the tagged one: HEAD must sit exactly on vX.Y.Z. `make tag`
# leaves the checkout there, so the usual path is `make tag` then
# `make release-whl`; publishing an older release means checking out its tag
# first.
#
# Also attaches both files to the GitHub Release, so the release page lists
# every artifact. --clobber replaces same-named assets on a re-run.
# Prereqs: a [pypi] token in ~/.pypirc and an authenticated `gh`.
release-whl: $(PYPI_SDIST)
	@$(call check_release_exists)
	@git rev-parse -q --verify "v$(KEYHAC_VERSION)^{commit}" >/dev/null || { \
		echo "ERROR: tag v$(KEYHAC_VERSION) not found locally. Cut it with 'make tag VERSION=$(KEYHAC_VERSION)' or fetch it."; \
		exit 1; \
	}
	@test "$$(git rev-parse HEAD)" = "$$(git rev-parse "v$(KEYHAC_VERSION)^{commit}")" || { \
		echo "ERROR: HEAD is not at tag v$(KEYHAC_VERSION); the upload would not match the tag."; \
		echo "       Check the tag out first: git checkout v$(KEYHAC_VERSION)"; \
		exit 1; \
	}
	@# Both files, not just the sdist that triggered the build: a VERSION= override
	@# that disagrees with __version__ builds different filenames entirely, and
	@# this is where that shows up as a clear error instead of a twine traceback.
	@for f in "$(PYPI_SDIST)" "$(PYPI_WHEEL)"; do \
		test -f "$$f" || { echo "ERROR: $$f missing; run 'make build' from a checkout at v$(KEYHAC_VERSION)."; exit 1; }; \
	done
	@echo "Uploading $(notdir $(PYPI_SDIST)) + $(notdir $(PYPI_WHEEL)) to PyPI..."
	$(VENV_PYTHON) -m twine upload "$(PYPI_SDIST)" "$(PYPI_WHEEL)"
	gh release upload v$(KEYHAC_VERSION) "$(PYPI_SDIST)" "$(PYPI_WHEEL)" --clobber
	@echo "Published $(KEYHAC_VERSION) to PyPI and attached both distributions to release v$(KEYHAC_VERSION)"

# --- release-skill: the authoring skill, where a user can reach it -----------
# `make skill-bundle` needs the Makefile and tools/, and neither ships: the
# sdist prunes tools and the wheel names its packages explicitly. So without
# this target the only people who can obtain the bundle are the people who
# already have the repository, and doc/ai-integration.md would be describing a
# developer step as if it were the user's download.
release-skill: skill-bundle
	@$(call check_release_exists)
	gh release upload v$(KEYHAC_VERSION) "dist/keyhac-action-authoring-skill.zip" --clobber
	@echo "Attached the authoring skill to release v$(KEYHAC_VERSION)"

# --- release-status: read-only progress check -------------------------------
# The one place to see which artifacts have landed for the version the
# checkout is on.
release-status:
	@test -n "$(KEYHAC_VERSION)" || { echo "ERROR: could not determine version; pass VERSION=x.y.z"; exit 1; }
	@echo "Release v$(KEYHAC_VERSION):"
	@# Asset names only: gh renders JSON numbers in Go's default float format, so
	@# {{.size}} would print sizes as 8.8917854e+07.
	@gh release view v$(KEYHAC_VERSION) --json assets \
		--template '{{range .assets}}  GitHub asset: {{.name}}{{"\n"}}{{end}}' \
		2>/dev/null || echo "  (no GitHub Release yet — run 'make release-github')"
	@# Through certifi's CA bundle rather than the interpreter's default store:
	@# a python.org build whose Install Certificates.command was never run
	@# cannot verify pypi.org, and this reported that as "unknown" - which
	@# reads as "not published" at exactly the moment you are checking whether
	@# it is. certifi is already here (twine depends on it); no certifi falls
	@# back to the default rather than failing.
	@$(VENV_PYTHON) -c "import json,ssl,urllib.request as u,importlib.util as il; \
		v='$(KEYHAC_VERSION)'; \
		ctx=ssl.create_default_context(cafile=__import__('certifi').where()) \
			if il.find_spec('certifi') else None; \
		d=json.load(u.urlopen('https://pypi.org/pypi/keyhac/json', context=ctx)); \
		print('  PyPI: ' + ('published' if v in d['releases'] else 'NOT published'))" \
		2>/dev/null || echo "  PyPI: could not be checked (no venv, or no network)"
	@# Called out by name rather than left to be spotted among the asset lines:
	@# its absence is the one that fails quietly. Without it there is no way for
	@# a user to obtain the authoring skill at all, and connecting the endpoint
	@# without the skill produces actions full of sleep and screen coordinates
	@# rather than an error anyone would notice.
	@if gh release view v$(KEYHAC_VERSION) --json assets --jq '.assets[].name' 2>/dev/null \
		| grep -q '^keyhac-action-authoring-skill\.zip$$'; then \
		echo "  Skill bundle: attached"; \
	else \
		echo "  Skill bundle: MISSING - run 'make release-skill'"; \
	fi

# ============================================================================
# macOS App Bundle Targets
# ============================================================================
# Delegate to macos_app/build.sh (launcher compile, Python.framework embed,
# signing/notarization via macos_app/signing.env) and create_dmg.sh.

macos-app:
	@echo "Building macOS application bundle..."
	@cd macos_app && ./build.sh

macos-dmg: macos-app
	@echo "Creating DMG installer..."
	@cd macos_app && ./create_dmg.sh

# Everything macos_app/build/ holds, which is more than `macos-app` wrote: the
# .app and its compiled executable, plus the DMG from macos-dmg and any mount
# point install-macos-dmg left. Hence clean-macos, not clean-macos-app.
clean-macos:
	@echo "Cleaning macOS build artifacts (.app, executable, DMG)..."
	@rm -rf macos_app/build/
	@echo "macOS build artifacts removed"

# Filename mirrors create_dmg.sh's own naming (Keyhac-<version>-macos.dmg),
# derived from the same version literal as KEYHAC_VERSION, so the two agree.
MACOS_DMG := macos_app/build/Keyhac-$(KEYHAC_VERSION)-macos.dmg

# File target so release-macos-dmg / install-macos-dmg build the DMG on demand
# when it is missing (e.g. after 'make clean-macos'). An existing DMG is NOT
# rebuilt, so re-uploading stays fast and never re-runs notarization; run
# 'make macos-dmg' to force a fresh one.
$(MACOS_DMG):
	@echo "DMG not found at $@; building it first..."
	@$(MAKE) macos-dmg

# Attach the signed/notarized DMG to the GitHub Release for this version. Kept
# separate from `macos-dmg` on purpose: building a DMG is a local operation you
# may do many times, uploading publishes it. The Release must already exist
# ('make release-github'). --clobber replaces an asset of the same name.
# Prereq: an authenticated `gh` (gh auth login).
release-macos-dmg: $(MACOS_DMG)
	@$(call check_release_exists)
	@echo "Uploading $(MACOS_DMG) to GitHub Release v$(KEYHAC_VERSION)..."
	gh release upload v$(KEYHAC_VERSION) "$(MACOS_DMG)" --clobber
	@echo "Uploaded $(notdir $(MACOS_DMG)) to release v$(KEYHAC_VERSION)"

# --- install-macos-dmg: install what we actually ship ------------------------
# Installs from the DMG rather than from macos_app/build/Keyhac.app on purpose:
# the DMG is the exact bytes a user downloads, signed and stapled as a
# container, so a packaging, signing or notarization mistake surfaces here
# instead of after the release. Builds the DMG first if it is missing.
#
# /Applications is group-writable by admin users, so no sudo is needed.
# Override the destination with MACOS_INSTALL_DIR=~/Applications.
MACOS_INSTALL_DIR ?= /Applications

# zsh does not expand the tilde in `make MACOS_INSTALL_DIR=~/Applications`
# (that needs magic_equal_subst, off by default), so it arrives here intact and
# would fail the existence check below with a confusing message. `override` is
# required: a command-line assignment otherwise wins over this one.
override MACOS_INSTALL_DIR := $(patsubst ~/%,$(HOME)/%,$(MACOS_INSTALL_DIR))

# Mounted inside macos_app/build/ (gitignored, and where create_dmg.sh already
# stages) rather than /Volumes, so a stale mount point can never collide with a
# DMG the user opened in Finder.
MACOS_DMG_MOUNT := macos_app/build/dmg_mount

install-macos-dmg: $(MACOS_DMG)
	@test -d "$(MACOS_INSTALL_DIR)" || { echo "ERROR: $(MACOS_INSTALL_DIR) does not exist."; exit 1; }
	@test -w "$(MACOS_INSTALL_DIR)" || { \
		echo "ERROR: $(MACOS_INSTALL_DIR) is not writable."; \
		echo "       Re-run under sudo, or install per-user with MACOS_INSTALL_DIR=~/Applications."; \
		exit 1; \
	}
	@rm -rf "$(MACOS_DMG_MOUNT)"
	@mkdir -p "$(MACOS_DMG_MOUNT)"
	@echo "Mounting $(notdir $(MACOS_DMG))..."
	@# One shell line so the trap that unmounts survives to the end, however the
	@# copy turns out — an orphaned mount would break every later run.
	@hdiutil attach "$(MACOS_DMG)" -nobrowse -readonly -quiet -mountpoint "$(MACOS_DMG_MOUNT)" || { \
		echo "ERROR: could not mount $(MACOS_DMG)"; rmdir "$(MACOS_DMG_MOUNT)" 2>/dev/null; exit 1; \
	}; \
	trap 'hdiutil detach "$(MACOS_DMG_MOUNT)" -quiet >/dev/null 2>&1' EXIT; \
	test -d "$(MACOS_DMG_MOUNT)/Keyhac.app" || { echo "ERROR: Keyhac.app not found inside the DMG"; exit 1; }; \
	echo "Installing Keyhac.app to $(MACOS_INSTALL_DIR)..."; \
	rm -rf "$(MACOS_INSTALL_DIR)/Keyhac.app"; \
	cp -R "$(MACOS_DMG_MOUNT)/Keyhac.app" "$(MACOS_INSTALL_DIR)/"
	@rmdir "$(MACOS_DMG_MOUNT)" 2>/dev/null || true
	@echo "Installed $(MACOS_INSTALL_DIR)/Keyhac.app"
	@echo "NOTE: grant the Accessibility permission on first launch (System"
	@echo "Settings > Privacy & Security > Accessibility)."

# Removes what install-macos-dmg put there — the app bundle only, never the
# containing directory. No DMG prerequisite on purpose: uninstalling must work
# after 'make clean-macos'. Set the same MACOS_INSTALL_DIR you installed with.
#
# An absent install is reported, not an error: re-running an uninstall should
# converge on "not installed" rather than fail the second time.
uninstall-macos-dmg:
	@if [ ! -e "$(MACOS_INSTALL_DIR)/Keyhac.app" ]; then \
		echo "Not installed: $(MACOS_INSTALL_DIR)/Keyhac.app"; \
	elif [ ! -w "$(MACOS_INSTALL_DIR)" ]; then \
		echo "ERROR: $(MACOS_INSTALL_DIR) is not writable."; \
		echo "       Re-run under sudo, or pass the MACOS_INSTALL_DIR you installed with."; \
		exit 1; \
	else \
		rm -rf "$(MACOS_INSTALL_DIR)/Keyhac.app"; \
		echo "Removed $(MACOS_INSTALL_DIR)/Keyhac.app"; \
	fi

# ============================================================================
# Windows App Bundle Targets
# ============================================================================
# Delegate to windows_app/build.ps1 (PowerShell). Only meaningful on Windows.

# The built bundle's launcher; its presence marks a complete bundle. Targets
# that only *consume* the bundle depend on this file target so it is built on
# demand if missing (e.g. after 'make clean-windows') instead of failing deep
# inside a packaging script. It also rebuilds when any bundled input is newer,
# which stops a packaging target from silently shipping a stale .exe.
# 'make windows-app' still forces an unconditional rebuild.
WINDOWS_APP_BUNDLE := windows_app/build/Keyhac/Keyhac.exe

# Inputs compiled or copied into the bundle. keyhac.ico is here because it is
# compiled into Keyhac.exe as a resource, so an icon change has to reach the
# .exe. All wildcarded so a missing optional input expands to nothing rather
# than a "no rule to make target" error.
#
# PuiKit is deliberately absent: it ships in the bundle but comes from outside
# this tree, so changes there still need an explicit 'make windows-app'.
WINDOWS_APP_SOURCES := $(wildcard windows_app/src/*.c) \
                       $(wildcard windows_app/resources/Keyhac.rc) \
                       $(wildcard windows_app/resources/Keyhac.manifest) \
                       $(wildcard keyhac/ui/assets/keyhac.ico) \
                       $(wildcard keyhac/*.py) \
                       $(wildcard keyhac/core/*.py) \
                       $(wildcard keyhac/platform/*.py) \
                       $(wildcard keyhac/platform/win/*.py) \
                       $(wildcard keyhac/ui/*.py)

$(WINDOWS_APP_BUNDLE): $(WINDOWS_APP_SOURCES)
	@echo "Windows app bundle missing or stale; building it first..."
	@powershell -ExecutionPolicy Bypass -File windows_app/build.ps1

windows-app:
	@echo "Building Windows application bundle..."
	@powershell -ExecutionPolicy Bypass -File windows_app/build.ps1

# Named for the artifact it produces, matching macos-dmg on the macOS side.
windows-zip:
	@echo "Building Windows application bundle (+ zip)..."
	@powershell -ExecutionPolicy Bypass -File windows_app/build.ps1 -Zip

# Filename mirrors build.ps1's own naming (Keyhac-<version>-win64.zip), derived
# from the same version literal as KEYHAC_VERSION, so the two cannot drift.
WINDOWS_ZIP := windows_app/build/Keyhac-$(KEYHAC_VERSION)-win64.zip

# File target so the upload/install builds the zip on demand when it is missing
# (e.g. after 'make clean-windows'). An existing zip is NOT rebuilt; run
# 'make windows-zip' to force a fresh one.
$(WINDOWS_ZIP):
	@echo "Windows zip not found at $@; building it first..."
	@$(MAKE) windows-zip

# Attach the portable zip to the GitHub Release for this version. The zip is
# unsigned, so Windows shows the usual Mark-of-the-Web / SmartScreen prompt.
# Prereq: an authenticated `gh` (gh auth login).
release-windows-zip: $(WINDOWS_ZIP)
	@$(call check_release_exists)
	@echo "Uploading $(WINDOWS_ZIP) to GitHub Release v$(KEYHAC_VERSION)..."
	gh release upload v$(KEYHAC_VERSION) "$(WINDOWS_ZIP)" --clobber
	@echo "Uploaded $(notdir $(WINDOWS_ZIP)) to release v$(KEYHAC_VERSION)"

# --- install-windows-zip: install what we actually ship ---------------------
# The counterpart of install-macos-dmg: expands the portable zip a user would
# download, rather than copying windows_app/build/Keyhac/, so a truncated or
# incomplete zip is caught here instead of by the first person to download it.
#
# Per-user by default (%LOCALAPPDATA%\Programs\Keyhac), so no UAC prompt.
# Override with WINDOWS_INSTALL_DIR='C:\Program Files\Keyhac' (needs an
# elevated shell). The expand/replace/verify logic lives in install_zip.ps1.
WINDOWS_INSTALL_DIR ?=

install-windows-zip: $(WINDOWS_ZIP)
	@echo "Installing Keyhac from $(notdir $(WINDOWS_ZIP))..."
	@powershell -ExecutionPolicy Bypass -File windows_app/install_zip.ps1 \
		-Zip "$(WINDOWS_ZIP)" $(if $(WINDOWS_INSTALL_DIR),-InstallDir "$(WINDOWS_INSTALL_DIR)")

# Removes what install-windows-zip put there. No zip prerequisite on purpose —
# deleting an install must not depend on being able to rebuild the artifact it
# came from. Set the same WINDOWS_INSTALL_DIR you used.
uninstall-windows-zip:
	@powershell -ExecutionPolicy Bypass -File windows_app/install_zip.ps1 \
		-Uninstall $(if $(WINDOWS_INSTALL_DIR),-InstallDir "$(WINDOWS_INSTALL_DIR)")

# --- MSIX (Microsoft Store / winget) packaging ------------------------------
# Wraps the built bundle into an .msix; builds the bundle first if it is missing.
#
# UNSIGNED by default, because that is the form Partner Center wants: Microsoft
# re-signs the package during certification, which is what makes Store signing
# free and SmartScreen-warning-free. Self-signing is only useful for sideloading
# on the dev box, so it is opt-in via SIGN=1 -- and 'install-windows-msix'
# below passes it for you.
#
# Identity values come from the gitignored windows_app/store.env (copy
# store.env.example); without it the pack falls back to a Keyhac.Prototype
# identity that sideloads fine but cannot be submitted.
windows-msix: $(WINDOWS_APP_BUNDLE)
	@echo "Packaging Windows app as MSIX$(if $(SIGN), (self-signed, local testing), (unsigned, Store submission))..."
	@powershell -ExecutionPolicy Bypass -File windows_app/build_msix.ps1 $(if $(SIGN),-Sign)

# Trust the self-signed cert (self-elevates via UAC) then install per-user.
#
# Always re-packs with -Sign first rather than reusing whatever .msix is on
# disk: both this and 'windows-msix' write the same
# build\Keyhac-<version>-x64.msix, so an unsigned pack may have overwritten a
# signed one. Add-AppxPackage cannot install an unsigned package, so packing
# here is what guarantees the artifact it installs is actually signed.
install-windows-msix: $(WINDOWS_APP_BUNDLE)
	@echo "Packaging + self-signing MSIX for local install..."
	@powershell -ExecutionPolicy Bypass -File windows_app/build_msix.ps1 -Sign
	@echo "Installing MSIX package locally..."
	@powershell -ExecutionPolicy Bypass -File windows_app/build_msix.ps1 -Install

# Removes the package (per-user) and the throwaway signing cert; untrusting the
# machine-store cert self-elevates via UAC.
uninstall-windows-msix:
	@echo "Removing installed MSIX package and throwaway cert..."
	@powershell -ExecutionPolicy Bypass -File windows_app/build_msix.ps1 -Uninstall

# --- release-windows-msix: submit the MSIX to the Microsoft Store -----------
# Packs first via the 'windows-msix' target, not the file: signed and unsigned
# packs write the same path, and Partner Center takes only the UNSIGNED form,
# so repacking is what guarantees the upload is not a leftover self-signed
# .msix from 'install-windows-msix'. The msstore CLI then uploads the package,
# creates a new submission carrying the listing metadata of the previous one,
# and commits it -- certification proceeds exactly as for a browser submission.
#
# One-time setup, both outside this Makefile (doc/dev/packaging.md):
#   - the msstore CLI, configured once with the Partner Center API credentials
#     ('msstore reconfigure'; they persist in Windows Credential Manager)
#   - KEYHAC_STORE_PRODUCT_ID in windows_app/store.env: the listing's 9N...
#     Store product ID (see store.env.example)
#
# Poll certification afterwards with: msstore submission status <product id>
#
# Mirrors WINDOWS_ZIP: build_msix.ps1 derives its version from the same
# __version__ literal as KEYHAC_VERSION, plus the Store-required ".0" revision,
# so this is the path the pack above just wrote.
WINDOWS_MSIX := windows_app/build/Keyhac-$(KEYHAC_VERSION).0-x64.msix

release-windows-msix: windows-msix
	@command -v msstore >/dev/null 2>&1 || { \
		echo "ERROR: msstore CLI not found on PATH."; \
		echo "       Install: winget install \"Microsoft Store Developer CLI\", then run 'msstore reconfigure'."; \
		exit 1; }
	@test -f windows_app/store.env || { \
		echo "ERROR: windows_app/store.env not found."; \
		echo "       Copy windows_app/store.env.example to store.env and fill it in."; \
		exit 1; }
	@. ./windows_app/store.env; \
	test -n "$$KEYHAC_STORE_PRODUCT_ID" || { \
		echo "ERROR: KEYHAC_STORE_PRODUCT_ID is not set in windows_app/store.env."; \
		echo "       Add the listing's 9N... product ID (see store.env.example)."; \
		exit 1; }; \
	test -f "$(WINDOWS_MSIX)" || { \
		echo "ERROR: $(WINDOWS_MSIX) missing after packing."; \
		exit 1; }; \
	echo "Submitting $(WINDOWS_MSIX) to the Microsoft Store ($$KEYHAC_STORE_PRODUCT_ID)..."; \
	msstore publish "$(WINDOWS_MSIX)" -id "$$KEYHAC_STORE_PRODUCT_ID"

# Everything the Windows build machinery generates. Plain rm rather than
# `build.ps1 -Clean` (identical effect) so this works on any OS and
# `make clean` does not fail on macOS.
clean-windows:
	@echo "Cleaning Windows build artifacts (bundle, zip, .msix, certs)..."
	@rm -rf windows_app/build
	@echo "Windows build artifacts removed"

# The CPython embeddable zips build.ps1 downloads on the first build, kept so
# later builds do not re-download ~10MB. Excluded from `clean` for that reason:
# cleaning a build should never cost you a download.
clean-windows-cache:
	@echo "Removing windows_app/.cache/ (downloaded CPython embeddable zips)..."
	@rm -rf windows_app/.cache
	@echo "Download cache removed; the next Windows build will re-download"
