#!/bin/bash
#
# Keyhac DMG Installer Creation Script
#
# This script creates a distributable DMG installer for Keyhac.app
#

set -e  # Exit on error

# ============================================================================
# Configuration
# ============================================================================

# Determine project root (parent of macos_app directory)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Build paths
BUILD_DIR="${SCRIPT_DIR}/build"
APP_NAME="Keyhac"
APP_BUNDLE="${BUILD_DIR}/${APP_NAME}.app"

# DMG configuration
DMG_TEMP_DIR="${BUILD_DIR}/dmg_temp"
DMG_NAME="Keyhac"
VOLUME_NAME="Keyhac Installer"

# Version number (can be overridden by environment variable). Used only as the
# fallback when the built Info.plist can't be read -- see
# extract_version_from_plist below. Defaults to the single source of truth:
# keyhac/__init__.py's __version__ literal (same extraction build.sh uses), so a
# version bump never needs an edit here.
if [ -z "${VERSION:-}" ]; then
    VERSION="$(sed -nE 's/^__version__[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' "${PROJECT_ROOT}/keyhac/__init__.py" | head -1)"
    VERSION="${VERSION:-0.0.0}"
fi

# Code signing / notarization (optional; same variables as build.sh).
# CODESIGN_IDENTITY signs the finished DMG; NOTARY_PROFILE (a notarytool
# keychain profile) additionally notarizes and staples it. Leave both unset for
# an unsigned development DMG.
#
# Both are loaded from the gitignored macos_app/signing.env if present (same
# file build.sh uses), so you don't have to re-export them. Environment values
# take precedence over the file.
SIGNING_ENV_FILE="${SCRIPT_DIR}/signing.env"
if [ -f "${SIGNING_ENV_FILE}" ]; then
    # log_info is defined further down; use echo (same format) here.
    echo "[INFO] Loading signing config from ${SIGNING_ENV_FILE}"
    _env_codesign="${CODESIGN_IDENTITY:-}"
    _env_notary="${NOTARY_PROFILE:-}"
    # shellcheck source=/dev/null
    . "${SIGNING_ENV_FILE}"
    [ -n "${_env_codesign}" ] && CODESIGN_IDENTITY="${_env_codesign}"
    [ -n "${_env_notary}" ] && NOTARY_PROFILE="${_env_notary}"
fi

CODESIGN_IDENTITY="${CODESIGN_IDENTITY:-}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"

# ============================================================================
# Helper Functions
# ============================================================================

log_info() {
    echo "[INFO] $1"
}

log_error() {
    echo "[ERROR] $1" >&2
}

check_app_exists() {
    if [ ! -d "${APP_BUNDLE}" ]; then
        log_error "Keyhac.app not found at ${APP_BUNDLE}"
        log_error "Please run build.sh first to create the app bundle"
        exit 1
    fi
}

extract_version_from_plist() {
    # Try to extract version from built Info.plist
    local plist="${APP_BUNDLE}/Contents/Info.plist"
    if [ -f "${plist}" ]; then
        local version=$(defaults read "${plist}" CFBundleShortVersionString 2>/dev/null || echo "")
        if [ -n "${version}" ]; then
            echo "${version}"
            return 0
        fi
    fi
    
    # Fallback to VERSION environment variable or default
    echo "${VERSION}"
}

create_install_doc() {
    local install_md="${DMG_TEMP_DIR}/INSTALL.md"
    
    # Check if INSTALL.md exists in project root
    if [ -f "${PROJECT_ROOT}/INSTALL.md" ]; then
        log_info "Copying existing INSTALL.md"
        cp "${PROJECT_ROOT}/INSTALL.md" "${install_md}"
    else
        log_info "Creating INSTALL.md"
        cat > "${install_md}" << 'EOF'
# Keyhac Installation Instructions

## Installation

1. Drag **Keyhac.app** to your **Applications** folder
2. Double-click Keyhac.app to launch
3. If you see a security warning, go to System Settings > Privacy & Security
   and click "Open Anyway"
4. Grant the **Accessibility** permission when prompted (System Settings >
   Privacy & Security > Accessibility) — Keyhac's keyboard hook cannot work
   without it — then relaunch Keyhac

## Usage

- Keyhac lives in the **menu bar** (no Dock icon); use its menu-bar icon to
  open the console window, reload the config, or quit
- Behavior is scripted in `~/.keyhac/config.py` (created from a template on
  first launch)
- **Quit Keyhac 1.x first** — two keyboard hooks processing the same keys
  will conflict

## Requirements

- macOS 11 (Big Sur) or later
- No additional software installation required

## Troubleshooting

If Keyhac fails to launch:

1. Check Console.app for error messages
2. Re-check the Accessibility permission (remove and re-add Keyhac.app after
   an update if keys stop being processed)
3. Try reinstalling by dragging Keyhac.app to Trash and reinstalling

For more information, visit: https://github.com/crftwr/keyhac2

EOF
    fi
}

# ============================================================================
# Main Build Process
# ============================================================================

main() {
    log_info "Starting DMG creation for Keyhac"
    
    # Check prerequisites
    check_app_exists
    
    # Extract version
    VERSION=$(extract_version_from_plist)
    # Platform suffix keeps release assets self-describing and matches the
    # Windows bundle's naming (Keyhac-<version>-win64.zip from build.ps1).
    DMG_FILENAME="${DMG_NAME}-${VERSION}-macos.dmg"
    DMG_PATH="${BUILD_DIR}/${DMG_FILENAME}"
    
    log_info "Creating DMG: ${DMG_FILENAME}"
    log_info "Version: ${VERSION}"
    
    # Clean up any existing DMG temp directory
    if [ -d "${DMG_TEMP_DIR}" ]; then
        log_info "Cleaning up existing DMG temp directory"
        rm -rf "${DMG_TEMP_DIR}"
    fi
    
    # Create temporary DMG directory
    log_info "Creating temporary DMG directory"
    mkdir -p "${DMG_TEMP_DIR}"
    
    # Copy Keyhac.app to DMG directory
    log_info "Copying Keyhac.app to DMG directory"
    cp -R "${APP_BUNDLE}" "${DMG_TEMP_DIR}/"
    
    # Create or copy INSTALL.md
    create_install_doc
    
    # Copy LICENSE file
    log_info "Copying LICENSE file"
    if [ -f "${PROJECT_ROOT}/LICENSE" ]; then
        cp "${PROJECT_ROOT}/LICENSE" "${DMG_TEMP_DIR}/"
    else
        log_warning "LICENSE file not found at ${PROJECT_ROOT}/LICENSE"
    fi

    # Surface the generated third-party notices at the DMG root too (it also
    # ships inside the .app bundle's Resources). Generated by build.sh via
    # tools/generate_third_party_notices.py.
    BUNDLE_NOTICES="${APP_BUNDLE}/Contents/Resources/THIRD_PARTY_NOTICES.txt"
    if [ -f "${BUNDLE_NOTICES}" ]; then
        log_info "Copying THIRD_PARTY_NOTICES.txt"
        cp "${BUNDLE_NOTICES}" "${DMG_TEMP_DIR}/"
    else
        log_warning "THIRD_PARTY_NOTICES.txt not found at ${BUNDLE_NOTICES}; run build.sh first"
    fi

    # Remove any existing DMG with the same name
    if [ -f "${DMG_PATH}" ]; then
        log_info "Removing existing DMG"
        rm -f "${DMG_PATH}"
    fi
    
    # Create DMG using hdiutil
    log_info "Creating DMG with hdiutil"
    hdiutil create \
        -volname "${VOLUME_NAME}" \
        -srcfolder "${DMG_TEMP_DIR}" \
        -ov \
        -format UDZO \
        "${DMG_PATH}"
    
    # Clean up temporary directory
    log_info "Cleaning up temporary directory"
    rm -rf "${DMG_TEMP_DIR}"
    
    # Sign and (optionally) notarize the DMG. The .app inside is already signed
    # by build.sh (and, if NOTARY_PROFILE was set then, notarized + stapled).
    # Signing the DMG lets Gatekeeper validate the container; notarizing +
    # stapling the DMG makes a downloaded installer open without warnings even
    # offline.
    if [ -n "${CODESIGN_IDENTITY}" ]; then
        log_info "Signing DMG..."
        codesign --force --timestamp --sign "${CODESIGN_IDENTITY}" "${DMG_PATH}"
        if ! codesign --verify --verbose=2 "${DMG_PATH}"; then
            log_error "DMG signature verification failed"
            exit 1
        fi
        log_info "DMG signed"

        if [ -n "${NOTARY_PROFILE}" ]; then
            log_info "Notarizing DMG (this can take a few minutes)..."
            if xcrun notarytool submit "${DMG_PATH}" \
                    --keychain-profile "${NOTARY_PROFILE}" --wait; then
                xcrun stapler staple "${DMG_PATH}"
                xcrun stapler validate "${DMG_PATH}"
                log_info "DMG notarized and stapled"
            else
                log_error "DMG notarization failed. Inspect the log with:"
                log_error "  xcrun notarytool log <submission-id> --keychain-profile \"${NOTARY_PROFILE}\""
                exit 1
            fi
        else
            log_info "NOTARY_PROFILE not set; DMG signed but not notarized"
        fi
    else
        log_info "CODESIGN_IDENTITY not set; DMG left unsigned (development build)"
    fi

    # Success
    log_info "DMG created successfully: ${DMG_PATH}"
    log_info "Size: $(du -h "${DMG_PATH}" | cut -f1)"

    echo ""
    echo "✓ DMG installer created: ${DMG_FILENAME}"
    echo "  Location: ${DMG_PATH}"
    echo ""
}

# Run main function
main "$@"
