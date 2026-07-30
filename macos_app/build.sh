#!/bin/bash
#
# Keyhac macOS App Bundle Build Script
#
# This script compiles the Objective-C launcher and creates a complete
# macOS application bundle for Keyhac with embedded Python interpreter.
#

set -e  # Exit on error

# ============================================================================
# Helper Functions
# ============================================================================

log_info() {
    echo "[INFO] $1"
}

log_warning() {
    echo "[WARNING] $1"
}

log_error() {
    echo "[ERROR] $1" >&2
}

log_success() {
    echo "[SUCCESS] $1"
}

# ============================================================================
# Build Configuration
# ============================================================================

# Determine project root (parent of macos_app directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Build paths
BUILD_DIR="${SCRIPT_DIR}/build"
APP_NAME="Keyhac"
APP_BUNDLE="${BUILD_DIR}/${APP_NAME}.app"

# Source paths
SRC_DIR="${SCRIPT_DIR}/src"
RESOURCES_DIR="${SCRIPT_DIR}/resources"

# Python configuration - detect from .venv
VENV_PYTHON="${PROJECT_ROOT}/.venv/bin/python3"
if [ ! -f "${VENV_PYTHON}" ]; then
    log_error "Virtual environment not found at ${PROJECT_ROOT}/.venv"
    log_error "Please create a virtual environment: python3 -m venv .venv"
    exit 1
fi

# Get Python version and paths from venv
PYTHON_VERSION=$("${VENV_PYTHON}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_BASE_PREFIX=$("${VENV_PYTHON}" -c "import sys; print(sys.base_prefix)")
PYTHON_SITE_PACKAGES="${PROJECT_ROOT}/.venv/lib/python${PYTHON_VERSION}/site-packages"

log_info "Detected Python ${PYTHON_VERSION} from venv"
log_info "Python base: ${PYTHON_BASE_PREFIX}"
log_info "Site packages: ${PYTHON_SITE_PACKAGES}"

# Check if using Python.framework or standard Python installation
# Python.framework can be in /Library/Frameworks (python.org) or /opt/homebrew (Homebrew)
if [[ "${PYTHON_BASE_PREFIX}" == *"/Python.framework/Versions/"* ]]; then
    # Python.framework installation (python.org or Homebrew)
    # Extract framework root by removing /Versions/X.Y suffix
    PYTHON_FRAMEWORK="${PYTHON_BASE_PREFIX%/Versions/*}"
    USE_FRAMEWORK=true
    log_info "Using Python.framework installation"
    log_info "Framework root: ${PYTHON_FRAMEWORK}"
else
    # Standard Python installation (mise, pyenv, system Python)
    PYTHON_FRAMEWORK="${PYTHON_BASE_PREFIX}"
    USE_FRAMEWORK=false
    log_info "Using standard Python installation"
fi

# Compiler settings
CC="clang"
CFLAGS="-framework Cocoa"

# Configure compiler flags based on Python installation type
if [ "$USE_FRAMEWORK" = true ]; then
    # Python.framework style
    CFLAGS="${CFLAGS} -F${PYTHON_FRAMEWORK}/Versions/${PYTHON_VERSION}"
    CFLAGS="${CFLAGS} -I${PYTHON_FRAMEWORK}/Versions/${PYTHON_VERSION}/include/python${PYTHON_VERSION}"
    CFLAGS="${CFLAGS} -L${PYTHON_FRAMEWORK}/Versions/${PYTHON_VERSION}/lib"
else
    # Standard Python style
    CFLAGS="${CFLAGS} -I${PYTHON_BASE_PREFIX}/include/python${PYTHON_VERSION}"
    CFLAGS="${CFLAGS} -L${PYTHON_BASE_PREFIX}/lib"
fi

CFLAGS="${CFLAGS} -lpython${PYTHON_VERSION}"
LDFLAGS="-rpath @executable_path/../Frameworks"

# Local signing configuration (optional, gitignored). CODESIGN_IDENTITY and
# NOTARY_PROFILE are both non-secret (the certificate's private key and the
# notary app-specific password live in the macOS keychain), so they can be kept
# in macos_app/signing.env to avoid re-exporting them every session. Copy
# signing.env.example to signing.env to start. Values already set in the
# environment take precedence over the file.
SIGNING_ENV_FILE="${SCRIPT_DIR}/signing.env"
if [ -f "${SIGNING_ENV_FILE}" ]; then
    log_info "Loading signing config from ${SIGNING_ENV_FILE}"
    _env_codesign="${CODESIGN_IDENTITY:-}"
    _env_notary="${NOTARY_PROFILE:-}"
    # shellcheck source=/dev/null
    . "${SIGNING_ENV_FILE}"
    [ -n "${_env_codesign}" ] && CODESIGN_IDENTITY="${_env_codesign}"
    [ -n "${_env_notary}" ] && NOTARY_PROFILE="${_env_notary}"
fi

# Code signing (optional). Set to a "Developer ID Application" identity to sign
# the bundle for distribution, e.g.
#   CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
# List the identities in your keychain with: security find-identity -v -p codesigning
CODESIGN_IDENTITY="${CODESIGN_IDENTITY:-}"

# Notarization (optional; requires CODESIGN_IDENTITY). Set to the name of a
# notarytool keychain profile to submit the signed .app to Apple's notary
# service and staple the ticket. Create the profile once with:
#   xcrun notarytool store-credentials "Keyhac-Notary" \
#       --apple-id you@example.com --team-id TEAMID --password <app-specific-pw>
# then build with: NOTARY_PROFILE="Keyhac-Notary"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"

# Version number (can be overridden by environment variable). Defaults to the
# single source of truth: keyhac/__init__.py's __version__ literal (same string
# the Windows build reads), so the bundle version never drifts from --version.
if [ -z "${VERSION:-}" ]; then
    VERSION="$(sed -nE 's/^__version__[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "${PROJECT_ROOT}/keyhac/__init__.py" | head -1)"
    VERSION="${VERSION:-0.0.0}"
fi

# ============================================================================
# Build Script Entry Point
# ============================================================================

log_info "Starting Keyhac macOS app bundle build..."
log_info "Project root: ${PROJECT_ROOT}"
log_info "Build directory: ${BUILD_DIR}"
log_info "Python version: ${PYTHON_VERSION}"

# ============================================================================
# Step 1: Compile Objective-C Source Files
# ============================================================================

log_info "Step 1: Compiling Objective-C source files..."

# Create build directory if it doesn't exist
mkdir -p "${BUILD_DIR}"

# Compile main.m and KeyhacAppDelegate.m
SOURCES="${SRC_DIR}/main.m ${SRC_DIR}/KeyhacAppDelegate.m"
OUTPUT_EXECUTABLE="${BUILD_DIR}/${APP_NAME}"

log_info "Compiling: ${SOURCES}"
log_info "Output: ${OUTPUT_EXECUTABLE}"

if ! ${CC} ${CFLAGS} ${LDFLAGS} -o "${OUTPUT_EXECUTABLE}" ${SOURCES}; then
    log_error "Compilation failed"
    exit 1
fi

log_success "Compilation completed successfully"

# ============================================================================
# Step 2: Create Bundle Structure
# ============================================================================

log_info "Step 2: Creating bundle structure..."

# Create bundle directories
CONTENTS_DIR="${APP_BUNDLE}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR_BUNDLE="${CONTENTS_DIR}/Resources"
FRAMEWORKS_DIR="${CONTENTS_DIR}/Frameworks"

mkdir -p "${MACOS_DIR}"
mkdir -p "${RESOURCES_DIR_BUNDLE}"
mkdir -p "${FRAMEWORKS_DIR}"

log_success "Bundle directories created"

# Copy executable to MacOS directory
log_info "Copying executable to bundle..."
cp "${OUTPUT_EXECUTABLE}" "${MACOS_DIR}/${APP_NAME}"
chmod +x "${MACOS_DIR}/${APP_NAME}"

log_success "Executable copied and permissions set"

# ============================================================================
# Step 3: Copy Resources
# ============================================================================

log_info "Step 3: Copying resources..."

# Copy Keyhac Python source.
# Keyhac is a single package (keyhac/), so the whole directory lands at the
# Resources root and Resources/ goes on sys.path — the launcher then imports
# "keyhac.main" directly.
log_info "Copying Keyhac Python source"
KEYHAC_PKG_DEST="${RESOURCES_DIR_BUNDLE}/keyhac"
rm -rf "${KEYHAC_PKG_DEST}"
cp -R "${PROJECT_ROOT}/keyhac" "${KEYHAC_PKG_DEST}"

# Pre-compile Keyhac Python files
log_info "Pre-compiling Keyhac Python files..."
if "${VENV_PYTHON}" -m compileall -q "${KEYHAC_PKG_DEST}"; then
    log_info "  Compiled Keyhac Python files"
else
    log_info "  Warning: Compilation failed"
fi

# Copy PuiKit library.
# PuiKit is a pure-Python package (its macOS
# GUI backend renders through PyObjC/Quartz - there is no compiled extension to
# bundle) installed editable into the venv from a sibling checkout. Resolve its
# real source directory from the venv interpreter so this works regardless of
# where the checkout lives (honours any PUIKIT_DIR override used at install).
log_info "Copying PuiKit library..."
PUIKIT_DEST="${RESOURCES_DIR_BUNDLE}/puikit"
PUIKIT_SRC=$("${VENV_PYTHON}" -c "import puikit, os; print(os.path.dirname(os.path.abspath(puikit.__file__)))" 2>/dev/null)

if [ -z "${PUIKIT_SRC}" ] || [ ! -d "${PUIKIT_SRC}" ]; then
    log_error "PuiKit not importable from the venv (resolved source: '${PUIKIT_SRC}')."
    log_error "Install it first: make install-puikit"
    exit 1
fi

# The GUI backend defaults to a bundled Noto Sans + Noto Sans Mono pair
# (puikit/fonts/), registered with Core Text at runtime so the app renders the
# same on any Mac without the fonts installed. Those files are large,
# OFL-licensed binaries kept out of git and fetched on demand, so an editable
# PuiKit checkout may not have them yet. Fetch them into the source tree before
# copying so the bundle ships them; the fetch is idempotent (skips files already
# present) and stdlib-only. Fall back to the OS fonts is only acceptable as a
# runtime safety net, not for a distributed build, so a missing font is a hard
# error below rather than a silent OS-font substitution in the DMG.
FETCH_FONTS="$(dirname "${PUIKIT_SRC}")/scripts/fetch_fonts.py"
if [ -f "${FETCH_FONTS}" ]; then
    log_info "Ensuring bundled Noto fonts are present..."
    if ! "${VENV_PYTHON}" "${FETCH_FONTS}"; then
        log_warning "Font fetch failed (offline?); relying on any fonts already in the checkout"
    fi
else
    log_info "  PuiKit font-fetch script not found; relying on installed fonts"
fi

# Copy the whole package, then drop caches (compiled fresh below).
rm -rf "${PUIKIT_DEST}"
cp -R "${PUIKIT_SRC}" "${PUIKIT_DEST}"
find "${PUIKIT_DEST}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
log_info "  Copied PuiKit from ${PUIKIT_SRC}"

# Verify the bundled default fonts made it into the app. Shipping a DMG that
# silently falls back to the OS UI font is exactly the regression this guards
# against, so fail the build (loudly, with the fix) rather than ship it.
FONTS_DEST="${PUIKIT_DEST}/fonts"
MISSING_FONTS=""
# The CJK pair is required too: Keyhac's console/chooser must render Japanese.
for f in NotoSans-Regular.ttf NotoSans-Bold.ttf NotoSansMono-Regular.ttf NotoSansMono-Bold.ttf \
         NotoSansCJKjp-Regular.otf NotoSansMonoCJKjp-Regular.otf; do
    [ -f "${FONTS_DEST}/${f}" ] || MISSING_FONTS="${MISSING_FONTS} ${f}"
done
if [ -n "${MISSING_FONTS}" ]; then
    log_error "Bundled Noto fonts missing from ${FONTS_DEST}:${MISSING_FONTS}"
    log_error "The app would fall back to OS fonts. Fetch them and rebuild:"
    log_error "  ${VENV_PYTHON} ${FETCH_FONTS}"
    exit 1
fi
log_info "  Bundled Noto fonts present in ${FONTS_DEST}"

# Pre-compile PuiKit Python files
log_info "Pre-compiling PuiKit Python files..."
if "${VENV_PYTHON}" -m compileall -q "${PUIKIT_DEST}"; then
    log_info "  Compiled PuiKit Python files"
else
    log_info "  Warning: Compilation failed"
fi

# Collect Python dependencies
log_info "Collecting Python dependencies..."
PACKAGES_DEST="${RESOURCES_DIR_BUNDLE}/python_packages"
mkdir -p "${PACKAGES_DEST}"

# Shared, platform-agnostic collector (also used by windows_app/build.ps1).
# Seeds come from pyproject.toml's [project] dependencies — on macOS that
# pulls the PyObjC framework closure the platform layer needs.
COLLECT_SCRIPT="${PROJECT_ROOT}/tools/collect_dependencies.py"

if [ -f "${COLLECT_SCRIPT}" ]; then
    # Use venv's Python to run the collection script
    if "${VENV_PYTHON}" "${COLLECT_SCRIPT}" --pyproject "${PROJECT_ROOT}/pyproject.toml" --dest "${PACKAGES_DEST}"; then
        log_success "Dependencies collected successfully"
    else
        log_error "Failed to collect dependencies"
        log_error "Please ensure all dependencies are installed: make install"
        exit 1
    fi
else
    log_warning "Dependency collection script not found at ${COLLECT_SCRIPT}"
    log_warning "Python packages will need to be added manually"
fi

# Copy application icon if it exists
# The committed app icon (regenerated from art/icon.svg by `make icons`),
# renamed to what Info.plist's CFBundleIconFile expects.
ICON_SOURCE="${PROJECT_ROOT}/keyhac/ui/assets/keyhac.icns"
if [ -f "${ICON_SOURCE}" ]; then
    log_info "Copying application icon..."
    cp "${ICON_SOURCE}" "${RESOURCES_DIR_BUNDLE}/Keyhac.icns"
else
    log_error "Application icon not found at ${ICON_SOURCE}; run 'make icons'"
    exit 1
fi

# Copy LICENSE file
LICENSE_SOURCE="${PROJECT_ROOT}/LICENSE"
if [ -f "${LICENSE_SOURCE}" ]; then
    log_info "Copying LICENSE file..."
    cp "${LICENSE_SOURCE}" "${RESOURCES_DIR_BUNDLE}/"
else
    log_warning "LICENSE file not found at ${LICENSE_SOURCE}"
fi

log_success "Resources copied successfully"

# ============================================================================
# Step 4: Embed Python
# ============================================================================

log_info "Step 4: Embedding Python..."

# Clean up old Python versions from previous builds
if [ -d "${FRAMEWORKS_DIR}/Python.framework/Versions" ]; then
    log_info "Cleaning up old Python versions..."
    for old_version in "${FRAMEWORKS_DIR}/Python.framework/Versions"/*; do
        if [ -d "${old_version}" ] && [ "$(basename "${old_version}")" != "Current" ] && [ "$(basename "${old_version}")" != "${PYTHON_VERSION}" ]; then
            log_info "  Removing old version: $(basename "${old_version}")"
            rm -rf "${old_version}"
        fi
    done
fi

# Determine Python source based on installation type
if [ "$USE_FRAMEWORK" = true ]; then
    # Python.framework installation
    PYTHON_SOURCE="${PYTHON_FRAMEWORK}/Versions/${PYTHON_VERSION}"
    if [ ! -d "${PYTHON_SOURCE}" ]; then
        log_error "Python.framework not found at ${PYTHON_SOURCE}"
        exit 1
    fi
    
    # Copy Python.framework to bundle
    log_info "Copying Python.framework from ${PYTHON_SOURCE}..."
    PYTHON_DEST="${FRAMEWORKS_DIR}/Python.framework/Versions/${PYTHON_VERSION}"
    mkdir -p "${PYTHON_DEST}"
    
    # Copy essential framework components
    if command -v rsync > /dev/null 2>&1; then
        rsync -a --no-perms --chmod=u+rw "${PYTHON_SOURCE}/Python" "${PYTHON_DEST}/"
        rsync -a --no-perms --chmod=u+rw "${PYTHON_SOURCE}/Resources" "${PYTHON_DEST}/" 2>/dev/null || true
        rsync -a --no-perms --chmod=u+rw "${PYTHON_SOURCE}/lib" "${PYTHON_DEST}/"
        rsync -a --no-perms --chmod=u+rw "${PYTHON_SOURCE}/bin" "${PYTHON_DEST}/"
    else
        cp -R "${PYTHON_SOURCE}/Python" "${PYTHON_DEST}/" 2>/dev/null || true
        cp -R "${PYTHON_SOURCE}/Resources" "${PYTHON_DEST}/" 2>/dev/null || true
        cp -R "${PYTHON_SOURCE}/lib" "${PYTHON_DEST}/"
        cp -R "${PYTHON_SOURCE}/bin" "${PYTHON_DEST}/"
        chmod -R u+rw "${PYTHON_DEST}" 2>/dev/null || true
    fi
    
    # Create version symlinks
    cd "${FRAMEWORKS_DIR}/Python.framework/Versions"
    ln -sf "${PYTHON_VERSION}" Current
    cd "${FRAMEWORKS_DIR}/Python.framework"
    ln -sf "Versions/Current/Python" Python
    ln -sf "Versions/Current/bin" bin
    
    # Create python3 symlink in bin directory
    log_info "Creating python3 symlink in bin directory..."
    cd "${PYTHON_DEST}/bin"
    if [ -f "python${PYTHON_VERSION}" ]; then
        ln -sf "python${PYTHON_VERSION}" python3
        log_info "  Created python3 -> python${PYTHON_VERSION}"
    fi
    
    # Add sitecustomize.py to disable user site-packages
    log_info "Adding sitecustomize.py to disable user site-packages..."
    SITECUSTOMIZE_SOURCE="${SCRIPT_DIR}/resources/sitecustomize.py"
    SITECUSTOMIZE_DEST="${PYTHON_DEST}/lib/python${PYTHON_VERSION}/sitecustomize.py"
    if [ -f "${SITECUSTOMIZE_SOURCE}" ]; then
        cp "${SITECUSTOMIZE_SOURCE}" "${SITECUSTOMIZE_DEST}"
        log_info "  Installed sitecustomize.py"
    else
        log_error "sitecustomize.py not found at ${SITECUSTOMIZE_SOURCE}"
        exit 1
    fi
    
    log_success "Python.framework embedded successfully"
    
    # Remove unnecessary files from embedded Python
    log_info "Removing unnecessary files from embedded Python..."
    
    # Remove Python.app (GUI launcher)
    if [ -d "${PYTHON_DEST}/Resources/Python.app" ]; then
        rm -rf "${PYTHON_DEST}/Resources/Python.app"
        log_info "  Removed Python.app"
    fi
    
    # Remove Resources directory entirely (Info.plist not needed for embedded use)
    if [ -d "${PYTHON_DEST}/Resources" ]; then
        rm -rf "${PYTHON_DEST}/Resources"
        log_info "  Removed Resources/"
    fi
    
    # Remove development tools from bin/
    for tool in "idle${PYTHON_VERSION}" pip3 "pip${PYTHON_VERSION}" "pydoc${PYTHON_VERSION}" "python${PYTHON_VERSION}-config"; do
        if [ -f "${PYTHON_DEST}/bin/${tool}" ]; then
            rm -f "${PYTHON_DEST}/bin/${tool}"
            log_info "  Removed bin/${tool}"
        fi
    done
    
    # Remove pkg-config files
    if [ -d "${PYTHON_DEST}/lib/pkgconfig" ]; then
        rm -rf "${PYTHON_DEST}/lib/pkgconfig"
        log_info "  Removed lib/pkgconfig/"
    fi
    
    # Remove Python test suite (saves ~68MB)
    if [ -d "${PYTHON_DEST}/lib/python${PYTHON_VERSION}/test" ]; then
        rm -rf "${PYTHON_DEST}/lib/python${PYTHON_VERSION}/test"
        log_info "  Removed lib/python${PYTHON_VERSION}/test/"
    fi

    # Remove the static-linking config dir. It holds link-time artifacts only
    # (python.o, Makefile, makesetup) that are never loaded at runtime, and the
    # unsigned python.o object file fails notarization ("The binary is not
    # signed").
    for config_dir in "${PYTHON_DEST}/lib/python${PYTHON_VERSION}"/config-*; do
        if [ -d "${config_dir}" ]; then
            rm -rf "${config_dir}"
            log_info "  Removed lib/python${PYTHON_VERSION}/$(basename "${config_dir}")/"
        fi
    done

    log_success "Unnecessary files removed"

    # Pre-compile Python standard library
    log_info "Pre-compiling Python standard library..."
    STDLIB_PATH="${PYTHON_DEST}/lib/python${PYTHON_VERSION}"
    if [ -d "${STDLIB_PATH}" ]; then
        # Use the bundled Python to compile the standard library
        # This ensures compatibility and uses the correct Python version
        BUNDLE_PYTHON="${PYTHON_DEST}/bin/python3"
        if [ -f "${BUNDLE_PYTHON}" ]; then
            # Compile all .py files in the standard library
            # -q: quiet mode (only show errors)
            # -f: force recompilation even if .pyc files exist
            if "${BUNDLE_PYTHON}" -m compileall -q -f "${STDLIB_PATH}"; then
                log_info "  Compiled Python standard library"

                # Count compiled files for verification
                PYC_COUNT=$(find "${STDLIB_PATH}" -name "*.pyc" | wc -l | tr -d ' ')
                PY_COUNT=$(find "${STDLIB_PATH}" -name "*.py" | wc -l | tr -d ' ')
                log_info "  Created ${PYC_COUNT} .pyc files from ${PY_COUNT} .py files"
            else
                log_warning "Standard library compilation had errors (non-critical)"
            fi
        else
            log_warning "Bundled Python not found, skipping standard library pre-compilation"
        fi
    else
        log_warning "Standard library path not found: ${STDLIB_PATH}"
    fi

    # Update install names to use embedded framework
    log_info "Updating install names to use embedded framework..."
    install_name_tool -change \
        "${PYTHON_BASE_PREFIX}/Python" \
        "@executable_path/../Frameworks/Python.framework/Versions/${PYTHON_VERSION}/Python" \
        "${MACOS_DIR}/${APP_NAME}"
else
    # Standard Python installation (mise, pyenv, homebrew, etc.)
    log_info "Copying Python from ${PYTHON_BASE_PREFIX}..."
    PYTHON_DEST="${FRAMEWORKS_DIR}/Python.framework/Versions/${PYTHON_VERSION}"
    mkdir -p "${PYTHON_DEST}"
    
    # Copy Python components (excluding problematic symlinks from source)
    if command -v rsync > /dev/null 2>&1; then
        rsync -a --no-perms --chmod=u+rw "${PYTHON_BASE_PREFIX}/lib" "${PYTHON_DEST}/"
        rsync -a --no-perms --chmod=u+rw "${PYTHON_BASE_PREFIX}/bin" "${PYTHON_DEST}/"
        rsync -a --no-perms --chmod=u+rw "${PYTHON_BASE_PREFIX}/include" "${PYTHON_DEST}/" 2>/dev/null || true
    else
        cp -R "${PYTHON_BASE_PREFIX}/lib" "${PYTHON_DEST}/"
        cp -R "${PYTHON_BASE_PREFIX}/bin" "${PYTHON_DEST}/"
        cp -R "${PYTHON_BASE_PREFIX}/include" "${PYTHON_DEST}/" 2>/dev/null || true
        chmod -R u+rw "${PYTHON_DEST}" 2>/dev/null || true
    fi
    
    # Remove problematic symlinks that were copied from source Python
    # These are framework-level symlinks that don't belong in version-specific directory
    log_info "Removing broken symlinks from copied Python..."
    if [ -L "${PYTHON_DEST}/bin/bin" ]; then
        rm -f "${PYTHON_DEST}/bin/bin"
        log_info "  Removed ${PYTHON_DEST}/bin/bin"
    fi
    if [ -L "${PYTHON_DEST}/lib/lib" ]; then
        rm -f "${PYTHON_DEST}/lib/lib"
        log_info "  Removed ${PYTHON_DEST}/lib/lib"
    fi
    if [ -L "${PYTHON_DEST}/${PYTHON_VERSION}" ]; then
        rm -f "${PYTHON_DEST}/${PYTHON_VERSION}"
        log_info "  Removed ${PYTHON_DEST}/${PYTHON_VERSION}"
    fi
    
    # Add sitecustomize.py to disable user site-packages
    log_info "Adding sitecustomize.py to disable user site-packages..."
    SITECUSTOMIZE_SOURCE="${SCRIPT_DIR}/resources/sitecustomize.py"
    SITECUSTOMIZE_DEST="${PYTHON_DEST}/lib/python${PYTHON_VERSION}/sitecustomize.py"
    if [ -f "${SITECUSTOMIZE_SOURCE}" ]; then
        cp "${SITECUSTOMIZE_SOURCE}" "${SITECUSTOMIZE_DEST}"
        log_info "  Installed sitecustomize.py"
    else
        log_error "sitecustomize.py not found at ${SITECUSTOMIZE_SOURCE}"
        exit 1
    fi
    
    # Create python3 symlink in bin directory
    log_info "Creating python3 symlink in bin directory..."
    if [ -f "${PYTHON_DEST}/bin/python${PYTHON_VERSION}" ]; then
        (cd "${PYTHON_DEST}/bin" && ln -sf "python${PYTHON_VERSION}" python3)
        log_info "  Created python3 -> python${PYTHON_VERSION}"
    fi
    
    # Create version symlinks
    log_info "Creating framework-level symlinks..."
    VERSIONS_DIR="${FRAMEWORKS_DIR}/Python.framework/Versions"
    FRAMEWORK_DIR="${FRAMEWORKS_DIR}/Python.framework"
    
    # Create Current -> 3.12 symlink
    (cd "${VERSIONS_DIR}" && ln -sfn "${PYTHON_VERSION}" Current)
    log_info "  Created ${VERSIONS_DIR}/Current -> ${PYTHON_VERSION}"
    
    # Create framework-level bin and lib symlinks (use -n to avoid following symlinks)
    (cd "${FRAMEWORK_DIR}" && ln -sfn "Versions/Current/bin" bin)
    (cd "${FRAMEWORK_DIR}" && ln -sfn "Versions/Current/lib" lib)
    log_info "  Created ${FRAMEWORK_DIR}/bin -> Versions/Current/bin"
    log_info "  Created ${FRAMEWORK_DIR}/lib -> Versions/Current/lib"
    
    log_success "Python embedded successfully"
    
    # Remove unnecessary files from embedded Python
    log_info "Removing unnecessary files from embedded Python..."
    
    # Remove development tools from bin/
    for tool in "idle${PYTHON_VERSION}" pip3 "pip${PYTHON_VERSION}" "pydoc${PYTHON_VERSION}" "python${PYTHON_VERSION}-config"; do
        if [ -f "${PYTHON_DEST}/bin/${tool}" ]; then
            rm -f "${PYTHON_DEST}/bin/${tool}"
            log_info "  Removed bin/${tool}"
        fi
    done
    
    # Remove pkg-config files
    if [ -d "${PYTHON_DEST}/lib/pkgconfig" ]; then
        rm -rf "${PYTHON_DEST}/lib/pkgconfig"
        log_info "  Removed lib/pkgconfig/"
    fi
    
    # Remove Python test suite (saves ~68MB)
    if [ -d "${PYTHON_DEST}/lib/python${PYTHON_VERSION}/test" ]; then
        rm -rf "${PYTHON_DEST}/lib/python${PYTHON_VERSION}/test"
        log_info "  Removed lib/python${PYTHON_VERSION}/test/"
    fi

    # Remove the static-linking config dir (link-time artifacts only; its
    # unsigned python.o fails notarization).
    for config_dir in "${PYTHON_DEST}/lib/python${PYTHON_VERSION}"/config-*; do
        if [ -d "${config_dir}" ]; then
            rm -rf "${config_dir}"
            log_info "  Removed lib/python${PYTHON_VERSION}/$(basename "${config_dir}")/"
        fi
    done

    log_success "Unnecessary files removed"
    
    # Pre-compile Python standard library
    log_info "Pre-compiling Python standard library..."
    STDLIB_PATH="${PYTHON_DEST}/lib/python${PYTHON_VERSION}"
    if [ -d "${STDLIB_PATH}" ]; then
        # Use the bundled Python to compile the standard library
        # This ensures compatibility and uses the correct Python version
        BUNDLE_PYTHON="${PYTHON_DEST}/bin/python3"
        if [ -f "${BUNDLE_PYTHON}" ]; then
            # Compile all .py files in the standard library
            # -q: quiet mode (only show errors)
            # -f: force recompilation even if .pyc files exist
            if "${BUNDLE_PYTHON}" -m compileall -q -f "${STDLIB_PATH}"; then
                log_info "  Compiled Python standard library"
                
                # Count compiled files for verification
                PYC_COUNT=$(find "${STDLIB_PATH}" -name "*.pyc" | wc -l | tr -d ' ')
                PY_COUNT=$(find "${STDLIB_PATH}" -name "*.py" | wc -l | tr -d ' ')
                log_info "  Created ${PYC_COUNT} .pyc files from ${PY_COUNT} .py files"
            else
                log_warning "Standard library compilation had errors (non-critical)"
            fi
        else
            log_warning "Bundled Python not found, skipping standard library pre-compilation"
        fi
    else
        log_warning "Standard library path not found: ${STDLIB_PATH}"
    fi
    
    # Update install names for Python shared library
    log_info "Updating install names to use embedded Python..."
    # Find the Python shared library
    PYTHON_LIB=$(find "${PYTHON_DEST}/lib" -name "libpython${PYTHON_VERSION}*.dylib" -type f | head -1)
    if [ -n "${PYTHON_LIB}" ]; then
        PYTHON_LIB_NAME=$(basename "${PYTHON_LIB}")
        
        # Update the Keyhac executable to use bundled Python library
        install_name_tool -change \
            "${PYTHON_BASE_PREFIX}/lib/${PYTHON_LIB_NAME}" \
            "@executable_path/../Frameworks/Python.framework/Versions/${PYTHON_VERSION}/lib/${PYTHON_LIB_NAME}" \
            "${MACOS_DIR}/${APP_NAME}" 2>/dev/null || true
        
        # Update the Python library's own install name (id)
        install_name_tool -id \
            "@executable_path/../Frameworks/Python.framework/Versions/${PYTHON_VERSION}/lib/${PYTHON_LIB_NAME}" \
            "${PYTHON_LIB}"
        log_info "  Updated Python library install name"
        
        # Check for and update any external library dependencies
        EXTERNAL_LIBS=$(otool -L "${PYTHON_LIB}" | grep -E "/(opt|usr/local|Users)" | grep -v "/usr/lib" | grep -v "/System" | awk '{print $1}')
        if [ -n "${EXTERNAL_LIBS}" ]; then
            log_info "  Warning: Python library has external dependencies:"
            echo "${EXTERNAL_LIBS}" | while read -r lib; do
                log_info "    - ${lib}"
            done
            log_info "  These libraries must be available on the target system"
        fi
    fi
fi

log_success "Install names updated"

# ============================================================================
# Step 4b: Make the bundle self-contained (vendor external dylibs)
# ============================================================================
#
# Python's C-extension modules (_ssl, _hashlib, _sqlite3, _lzma, _zstd,
# _decimal, ...) are dynamically linked against Homebrew libraries that live
# OUTSIDE the bundle, e.g. /opt/homebrew/opt/openssl@3/lib/libssl.3.dylib.
# On a Mac without those exact Homebrew formulae the app fails to launch with
# "Library not loaded: /opt/homebrew/...". delocate copies each such library
# into a .dylibs/ folder next to the extensions and rewrites their load
# commands to @loader_path, so the bundle runs on any Mac.

log_info "Step 4b: Vendoring external dynamic libraries (delocate)..."

# The Homebrew framework copy can leave dangling / self-referential symlinks
# (e.g. Versions/${PYTHON_VERSION}/${PYTHON_VERSION} -> ${PYTHON_VERSION}).
# They are unused by Keyhac but trip up delocate's directory walk, so remove any
# broken symlinks first.
BROKEN_LINKS=$(find "${APP_BUNDLE}" -type l ! -exec test -e {} \; -print 2>/dev/null | wc -l | tr -d ' ')
if [ "${BROKEN_LINKS}" != "0" ]; then
    log_info "  Removing ${BROKEN_LINKS} broken symlink(s) from the bundle"
    find "${APP_BUNDLE}" -type l ! -exec test -e {} \; -delete 2>/dev/null || true
fi

# Ensure delocate is available in the build venv.
DELOCATE_PATH="${PROJECT_ROOT}/.venv/bin/delocate-path"
if [ ! -x "${DELOCATE_PATH}" ]; then
    log_info "  delocate not found in venv; installing..."
    if ! "${VENV_PYTHON}" -m pip install --quiet delocate; then
        log_error "Failed to install 'delocate'. Install it and re-run the build:"
        log_error "  ${VENV_PYTHON} -m pip install delocate"
        exit 1
    fi
fi

# Delocate only the lib-dynload tree so the vendored .dylibs/ folder stays
# inside Contents/ and the main executable and Python.framework (already wired
# with @executable_path) are left untouched. This is the only part of the
# bundle with external dependencies; the pip packages under Resources ship
# self-contained wheels.
LIB_DYNLOAD="${FRAMEWORKS_DIR}/Python.framework/Versions/${PYTHON_VERSION}/lib/python${PYTHON_VERSION}/lib-dynload"
if [ -d "${LIB_DYNLOAD}" ]; then
    "${DELOCATE_PATH}" "${LIB_DYNLOAD}"
    log_info "  Vendored external libraries into ${LIB_DYNLOAD}/.dylibs"

    # Verify no C-extension still depends on a library outside the bundle.
    EXTERNAL_DEPS=$(find "${LIB_DYNLOAD}" -name "*.so" -exec otool -L {} \; 2>/dev/null \
        | grep -E "^[[:space:]]+(/opt/homebrew|/usr/local|/opt/local|/Users/)" | sort -u || true)
    if [ -n "${EXTERNAL_DEPS}" ]; then
        log_warning "  Some extensions still reference external libraries:"
        echo "${EXTERNAL_DEPS}"
        log_warning "  The app may fail to launch on machines without these libraries."
    else
        log_success "  All C-extension modules are self-contained"
    fi
else
    log_warning "  lib-dynload not found at ${LIB_DYNLOAD}; skipping delocate"
fi

# ============================================================================
# Step 4c: Generate third-party license notices
# ============================================================================
#
# Build an aggregated THIRD_PARTY_NOTICES.txt from the license text shipped with
# every bundled Python distribution (python_packages/*.dist-info), plus the
# components that are not Python distributions: the embedded CPython interpreter,
# the copied-in PuiKit source, and the bundled Noto fonts. The generator fails
# the build if any bundled distribution has no discoverable license text, so an
# incomplete notice can never ship. The script is stdlib-only and platform-
# agnostic - the Windows bundle build reuses it the same way.

log_info "Step 4c: Generating third-party license notices..."

NOTICES_SCRIPT="${PROJECT_ROOT}/tools/generate_third_party_notices.py"
NOTICES_OUT="${RESOURCES_DIR_BUNDLE}/THIRD_PARTY_NOTICES.txt"

if [ ! -f "${NOTICES_SCRIPT}" ]; then
    log_error "Notices generator not found at ${NOTICES_SCRIPT}"
    exit 1
fi

NOTICES_EXTRAS=()

# Embedded interpreter's PSF license (under lib/, survives the Resources/
# cleanup performed while embedding Python above).
PYTHON_LICENSE="${PYTHON_DEST}/lib/python${PYTHON_VERSION}/LICENSE.txt"
if [ -f "${PYTHON_LICENSE}" ]; then
    NOTICES_EXTRAS+=(--extra "Python ${PYTHON_VERSION} interpreter and standard library (Python Software Foundation License Agreement)=${PYTHON_LICENSE}")
else
    log_warning "Python LICENSE.txt not found at ${PYTHON_LICENSE}; the interpreter will not be listed in the generated notices"
fi

# PuiKit's LICENSE location depends on how PuiKit is installed:
#   * editable checkout (PUIKIT_DIR / ../puikit): LICENSE sits at the checkout
#     root, one level above the package directory resolved into PUIKIT_SRC.
#   * published wheel: LICENSE ships inside puikit-*.dist-info (.../licenses/LICENSE).
# In the wheel case the package dir's parent IS the venv site-packages, which
# holds that .dist-info; in the editable case the checkout-root LICENSE is found
# first. So a single parent path resolves both layouts.
PUIKIT_PARENT="$(dirname "${PUIKIT_SRC}")"
PUIKIT_LICENSE="${PUIKIT_PARENT}/LICENSE"
if [ ! -f "${PUIKIT_LICENSE}" ]; then
    # …/site-packages/puikit-*.dist-info/licenses/LICENSE is 3 levels down.
    PUIKIT_LICENSE="$(find "${PUIKIT_PARENT}" -maxdepth 3 -ipath '*puikit-*.dist-info*' \
                      -iname 'LICEN[SC]E*' -type f 2>/dev/null | head -n 1)"
fi
if [ -n "${PUIKIT_LICENSE}" ] && [ -f "${PUIKIT_LICENSE}" ]; then
    NOTICES_EXTRAS+=(--extra "PuiKit (MIT License)=${PUIKIT_LICENSE}")
else
    log_error "PuiKit LICENSE not found (looked for ${PUIKIT_PARENT}/LICENSE and puikit-*.dist-info under ${PUIKIT_PARENT})"
    exit 1
fi

# Bundled fonts (SIL OFL 1.1); the license files were copied alongside the
# font files. The CJK pair has its own OFL text.
FONTS_OFL="${FONTS_DEST}/OFL.txt"
if [ -f "${FONTS_OFL}" ]; then
    NOTICES_EXTRAS+=(--extra "Noto Sans & Noto Sans Mono fonts (SIL Open Font License 1.1)=${FONTS_OFL}")
else
    log_error "Font license OFL.txt not found at ${FONTS_OFL}"
    exit 1
fi
FONTS_OFL_CJK="${FONTS_DEST}/OFL-CJK.txt"
if [ -f "${FONTS_OFL_CJK}" ]; then
    NOTICES_EXTRAS+=(--extra "Noto Sans CJK JP fonts (SIL Open Font License 1.1)=${FONTS_OFL_CJK}")
fi

if "${VENV_PYTHON}" "${NOTICES_SCRIPT}" \
        --title "Keyhac" \
        --scan "${PACKAGES_DEST}" \
        "${NOTICES_EXTRAS[@]}" \
        --output "${NOTICES_OUT}"; then
    log_success "Third-party notices written to ${NOTICES_OUT}"
else
    log_error "Failed to generate third-party license notices (see errors above)"
    exit 1
fi

# ============================================================================
# Step 5: Generate Info.plist
# ============================================================================

log_info "Step 5: Generating Info.plist..."

TEMPLATE_FILE="${RESOURCES_DIR}/Info.plist.template"
PLIST_FILE="${CONTENTS_DIR}/Info.plist"

if [ ! -f "${TEMPLATE_FILE}" ]; then
    log_error "Info.plist template not found at ${TEMPLATE_FILE}"
    exit 1
fi

# Substitute version number
log_info "Substituting version: ${VERSION}"
# BUNDLE_ID default: crftwr.Keyhac2. Open decision doc/07-roadmap.md #4 —
# building with BUNDLE_ID=crftwr.Keyhac instead carries the Accessibility
# permission over from keyhac-mac 1.x (same identity), but collides with an
# installed 1.x.
BUNDLE_ID="${BUNDLE_ID:-crftwr.Keyhac2}"
log_info "Bundle identifier: ${BUNDLE_ID}"
sed -e "s/{{VERSION}}/${VERSION}/g" -e "s/{{BUNDLE_ID}}/${BUNDLE_ID}/g" \
    "${TEMPLATE_FILE}" > "${PLIST_FILE}"

# Validate Info.plist is valid XML
if ! plutil -lint "${PLIST_FILE}" > /dev/null 2>&1; then
    log_error "Generated Info.plist is not valid XML"
    exit 1
fi

log_success "Info.plist generated and validated"

# ============================================================================
# Step 6: Code Signing (always; identity optional)
# ============================================================================
#
# With CODESIGN_IDENTITY set, this is the distributable signing pass: every
# Mach-O binary inside the bundle signed with a secure timestamp and the
# Hardened Runtime, from the inside out (nested libraries first, the .app
# last) - signing the .app alone is not enough for Gatekeeper /
# notarization. The embedded CPython interpreter loads C-extension modules
# (.so) and vendored dylibs at runtime, so it also needs the entitlements in
# resources/entitlements.plist.
#
# Without an identity the same sweep runs with an ad-hoc signature
# (codesign -s -). Skipping is not an option: the embed steps above rewrite
# install names in the copied binaries (install_name_tool), which
# invalidates their original signatures, and arm64 dyld refuses to load a
# binary whose signature no longer matches its content - the app aborts at
# launch with "code signature invalid". Ad-hoc carries no identity, secure
# timestamp, Hardened Runtime, or entitlements: it makes the build run on
# this machine, not distributable.

if [ -n "${CODESIGN_IDENTITY}" ]; then
    log_info "Step 6: Code signing bundle (inside-out, Hardened Runtime)..."

    ENTITLEMENTS_FILE="${RESOURCES_DIR}/entitlements.plist"
    if [ ! -f "${ENTITLEMENTS_FILE}" ]; then
        log_error "Entitlements file not found at ${ENTITLEMENTS_FILE}"
        exit 1
    fi

    # Common codesign invocation: replace any existing signature, embed a
    # secure timestamp, opt into the Hardened Runtime, and apply the embedded
    # interpreter entitlements.
    SIGN=(codesign --force --timestamp --options runtime \
          --entitlements "${ENTITLEMENTS_FILE}" --sign "${CODESIGN_IDENTITY}")
else
    log_info "Step 6: Ad-hoc code signing (CODESIGN_IDENTITY not set; dev build)..."
    SIGN=(codesign --force --sign -)
fi

    # The Python-embedding step above strips the framework's Resources/ dir,
    # which removes the Info.plist codesign needs to seal Python.framework as a
    # nested bundle. Synthesize a minimal one so the framework signs cleanly and
    # `codesign --verify --deep` accepts it as valid nested code.
    FRAMEWORK_VERSION_DIR="${FRAMEWORKS_DIR}/Python.framework/Versions/${PYTHON_VERSION}"
    if [ -d "${FRAMEWORK_VERSION_DIR}" ] && [ ! -f "${FRAMEWORK_VERSION_DIR}/Resources/Info.plist" ]; then
        log_info "  Writing minimal Python.framework Info.plist for signing..."
        mkdir -p "${FRAMEWORK_VERSION_DIR}/Resources"
        cat > "${FRAMEWORK_VERSION_DIR}/Resources/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>org.python.python</string>
    <key>CFBundleName</key><string>Python</string>
    <key>CFBundleExecutable</key><string>Python</string>
    <key>CFBundlePackageType</key><string>FMWK</string>
    <key>CFBundleShortVersionString</key><string>${PYTHON_VERSION}</string>
    <key>CFBundleVersion</key><string>${PYTHON_VERSION}</string>
</dict>
</plist>
PLIST
    fi

    # 1. Nested Mach-O libraries: extension modules (.so) and dylibs, including
    #    the vendored .dylibs/ from delocate and any shipped inside pip packages.
    log_info "  Signing nested libraries (.so / .dylib)..."
    while IFS= read -r -d '' lib; do
        "${SIGN[@]}" "${lib}" || { log_error "Failed to sign ${lib}"; exit 1; }
    done < <(find "${APP_BUNDLE}" -type f \( -name "*.so" -o -name "*.dylib" \) -print0)

    # 1b. delocate also vendors extensionless framework binaries (e.g. Tcl/Tk
    #     out of python.org's Tcl.framework/Tk.framework) into .dylibs/, and its
    #     install_name_tool rewrite invalidates their upstream signatures —
    #     notarization rejects them ("The signature of the binary is invalid").
    #     Sweep .dylibs/ again by content for anything the extension match missed.
    log_info "  Signing extensionless vendored libraries in .dylibs/..."
    while IFS= read -r -d '' lib; do
        case "${lib}" in *.so|*.dylib) continue ;; esac
        file "${lib}" | grep -q "Mach-O" || continue
        "${SIGN[@]}" "${lib}" || { log_error "Failed to sign ${lib}"; exit 1; }
    done < <(find "${APP_BUNDLE}" -path "*/.dylibs/*" -type f -print0)

    # 2. Standalone Mach-O executables inside the framework's bin/ (e.g.
    #    pythonX.Y). Skip symlinks and non-Mach-O helper scripts.
    log_info "  Signing embedded interpreter executables..."
    FRAMEWORK_BIN_DIR="${FRAMEWORK_VERSION_DIR}/bin"
    if [ -d "${FRAMEWORK_BIN_DIR}" ]; then
        while IFS= read -r -d '' exe; do
            if file "${exe}" | grep -q "Mach-O"; then
                "${SIGN[@]}" "${exe}" || { log_error "Failed to sign ${exe}"; exit 1; }
            fi
        done < <(find "${FRAMEWORK_BIN_DIR}" -type f -print0)
    fi

    # 3. Python.framework itself (seals its versioned contents as nested code).
    #    Present only in the Python.framework build path; the standard-Python
    #    path ships libpythonX.Y.dylib instead, already signed in step 1.
    if [ -f "${FRAMEWORK_VERSION_DIR}/Python" ]; then
        log_info "  Signing Python.framework..."
        "${SIGN[@]}" "${FRAMEWORKS_DIR}/Python.framework" \
            || { log_error "Failed to sign Python.framework"; exit 1; }
    fi

    # 4. The main executable.
    log_info "  Signing main executable..."
    "${SIGN[@]}" "${MACOS_DIR}/${APP_NAME}" \
        || { log_error "Failed to sign ${APP_NAME}"; exit 1; }

    # 5. The .app bundle last: the outermost seal over everything above.
    log_info "  Signing app bundle..."
    "${SIGN[@]}" "${APP_BUNDLE}" \
        || { log_error "Failed to sign ${APP_BUNDLE}"; exit 1; }

# Verify the whole static code tree strictly (as Gatekeeper would).
log_info "  Verifying signature..."
if codesign --verify --deep --strict --verbose=2 "${APP_BUNDLE}"; then
    log_success "Code signing completed and verified"
else
    log_error "Code signing verification failed"
    exit 1
fi
if [ -n "${CODESIGN_IDENTITY}" ]; then
    log_info "  (After notarization, confirm Gatekeeper acceptance with:"
    log_info "     spctl -a -vvv --type exec \"${APP_BUNDLE}\")"
fi

# ============================================================================
# Step 7: Notarization (optional; requires signing)
# ============================================================================
#
# Submits the signed .app to Apple's notary service and staples the resulting
# ticket, so the app launches without Gatekeeper warnings even offline. Skipped
# unless NOTARY_PROFILE names a notarytool keychain profile (see the config
# section above for how to create one). create_dmg.sh notarizes the DMG too.

if [ -n "${NOTARY_PROFILE}" ]; then
    if [ -z "${CODESIGN_IDENTITY}" ]; then
        log_error "NOTARY_PROFILE is set but CODESIGN_IDENTITY is not."
        log_error "A notarized app must be signed first; set CODESIGN_IDENTITY and rebuild."
        exit 1
    fi

    log_info "Step 7: Notarizing app bundle..."

    # notarytool accepts a .zip/.dmg/.pkg, not a raw .app; ditto preserves the
    # bundle's symlinks and signatures inside the zip.
    NOTARIZE_ZIP="${BUILD_DIR}/${APP_NAME}-notarize.zip"
    rm -f "${NOTARIZE_ZIP}"
    log_info "  Packaging app for submission..."
    /usr/bin/ditto -c -k --keepParent "${APP_BUNDLE}" "${NOTARIZE_ZIP}"

    log_info "  Submitting to Apple notary service (this can take a few minutes)..."
    # notarytool submit --wait exits 0 even when the verdict is Invalid, so the
    # exit code alone is not a success signal — parse the reported status. tee
    # keeps the live progress output visible while capturing it.
    SUBMIT_OUTPUT=$(xcrun notarytool submit "${NOTARIZE_ZIP}" \
            --keychain-profile "${NOTARY_PROFILE}" --wait 2>&1 | tee /dev/stderr) || true
    SUBMISSION_ID=$(echo "${SUBMIT_OUTPUT}" | sed -n 's/^[[:space:]]*id: //p' | head -1)
    if echo "${SUBMIT_OUTPUT}" | grep -q "status: Accepted"; then
        log_info "  Stapling notarization ticket to the app..."
        xcrun stapler staple "${APP_BUNDLE}"
        xcrun stapler validate "${APP_BUNDLE}"
        rm -f "${NOTARIZE_ZIP}"
        log_success "Notarization completed and stapled"
    else
        log_error "Notarization was not accepted."
        if [ -n "${SUBMISSION_ID}" ]; then
            log_error "Notary log for submission ${SUBMISSION_ID}:"
            xcrun notarytool log "${SUBMISSION_ID}" \
                --keychain-profile "${NOTARY_PROFILE}" >&2 || true
        else
            log_error "Could not determine the submission id; list recent ones with:"
            log_error "  xcrun notarytool history --keychain-profile \"${NOTARY_PROFILE}\""
        fi
        rm -f "${NOTARIZE_ZIP}"
        exit 1
    fi
else
    log_info "Step 7: Skipping notarization (NOTARY_PROFILE not set)"
fi

# ============================================================================
# Build Complete
# ============================================================================

log_success "Build completed successfully!"
log_info "Application bundle: ${APP_BUNDLE}"
log_info ""
log_info "To run the application:"
log_info "  open ${APP_BUNDLE}"
log_info ""
log_info "To install to Applications:"
log_info "  cp -R ${APP_BUNDLE} /Applications/"
