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

.PHONY: help venv install install-puikit check-venv test run run-debug run-sandbox echo clean

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
	@echo "  make clean       - remove build artifacts and caches"
	@echo ""
	@echo "  Run targets accept ARGS, e.g. make run-sandbox ARGS=-d"
	@echo "  PuiKit installs from PyPI by default; to develop against a local"
	@echo "  checkout, set PUIKIT_DIR (Makefile.local / env), e.g. PUIKIT_DIR=../puikit"
	@echo "  macOS: the terminal app needs the Accessibility permission."
	@echo "  NOTE: quit Keyhac 1.x (keyhac-mac / keyhac-win) first - two keyboard"
	@echo "  hooks processing the same keys will conflict."
	@echo ""
	@echo "  (Packaging / release targets will be added in M5.)"

$(VENV_STAMP): pyproject.toml $(if $(PUIKIT_DIR),$(PUIKIT_DIR)/pyproject.toml)
	$(PYTHON) -m venv $(VENV)
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

clean:
	rm -rf build dist *.egg-info
	find . -name __pycache__ -type d -not -path "./$(VENV)/*" -exec rm -rf {} +
	rm -rf .pytest_cache
