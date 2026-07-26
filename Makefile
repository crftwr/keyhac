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

.PHONY: help venv install test run run-debug run-sandbox echo clean

help:
	@echo "Keyhac 2 utility commands:"
	@echo "  make venv        - create the virtualenv and install keyhac ($(VENV)/, $(PYTHON))"
	@echo "  make install     - (re)install keyhac into the venv (editable, with dev deps)"
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
	@echo "  macOS: the terminal app needs the Accessibility permission."
	@echo "  NOTE: quit Keyhac 1.x (keyhac-mac / keyhac-win) first - two keyboard"
	@echo "  hooks processing the same keys will conflict."
	@echo ""
	@echo "  (Packaging / release targets will be added in M5.)"

$(VENV_STAMP): pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(VENV_PIP) install -e ".[$(EXTRAS)]"
	@touch $(VENV_STAMP)

venv: $(VENV_STAMP)

install: $(VENV_STAMP)

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
