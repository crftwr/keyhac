<#
.SYNOPSIS
    Install (or uninstall) Keyhac on this machine from the portable distribution zip.

.DESCRIPTION
    The Windows counterpart of `make install-macos-dmg`: it installs the exact
    artifact end users download (windows_app\build\Keyhac-<version>-win64.zip,
    produced by build.ps1 -Zip) rather than copying windows_app\build\Keyhac\
    directly. Expanding the real zip is what catches a truncated or incomplete
    archive here, instead of leaving it for the first person to download it.

      (default)   expand the zip into -InstallDir, replacing any existing install
      -Uninstall  remove -InstallDir; needs no zip at all

    Per-user by default (%LOCALAPPDATA%\Programs\Keyhac), so no UAC prompt and
    no elevated shell is needed. Pass -InstallDir 'C:\Program Files\Keyhac' for
    a machine-wide install, which does require an elevated shell.

    Invoked by `make install-windows-zip` (which builds the zip first if it is
    missing) and `make uninstall-windows-zip`, both passing WINDOWS_INSTALL_DIR
    through as -InstallDir. Install and uninstall share this one script so they
    cannot disagree about where the default install lives.

.NOTES
    The zip holds a single Keyhac\ folder at its root (build.ps1 compresses the
    bundle directory itself), so it expands into the PARENT of the destination
    to land exactly at the destination.

    Any existing install is REMOVED first rather than expanded over the top of:
    Expand-Archive -Force overwrites files it has replacements for, but leaves
    behind ones a later build stopped shipping, which is how a stale .pyd
    survives an "upgrade" and produces failures no clean install can reproduce.
#>
[CmdletBinding()]
param(
    # Defaults (in the body, not here) to windows_app\build\Keyhac-<version>-win64.zip.
    # $PSScriptRoot is not reliably populated in a param default under
    # 'powershell -File' - the form the Makefile uses - so it is resolved below.
    [string]$Zip,
    # Defaults (in the body) to %LOCALAPPDATA%\Programs\Keyhac.
    [string]$InstallDir,
    [switch]$Uninstall                                    # remove the install, no zip needed
)

$ErrorActionPreference = "Stop"

function Info    ($m) { Write-Host "[INFO] $m" }
function Success ($m) { Write-Host "[SUCCESS] $m" -ForegroundColor Green }
function Fail    ($m) { Write-Host "[ERROR] $m" -ForegroundColor Red; exit 1 }

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$projectRoot = Split-Path -Parent $ScriptDir

# Resolved before anything else so -Uninstall never needs a zip: deleting an
# install must work long after the build directory is gone.
#
# Asked of Windows rather than read from $env:LOCALAPPDATA, because the variable
# is not there when this runs the way it is meant to: Git Bash/MSYS make hands a
# PowerShell child a stripped environment - 17 variables, with LOCALAPPDATA,
# APPDATA, TEMP and USERPROFILE all missing - so `make install-windows-zip` died
# on "Cannot bind argument to parameter 'Path' because it is null" while running
# the script by hand worked. build.ps1 and build_msix.ps1 meet the same mangling
# in PATHEXT and TEMP; this is the third face of it.
if (-not $InstallDir) {
    $localAppData = $env:LOCALAPPDATA
    if (-not $localAppData) {
        $localAppData = [Environment]::GetFolderPath('LocalApplicationData')
    }
    if (-not $localAppData) {
        Fail "Could not locate the local application data folder. Pass -InstallDir explicitly (or set WINDOWS_INSTALL_DIR for make)."
    }
    $InstallDir = Join-Path $localAppData 'Programs\Keyhac'
}

function Remove-Install {
    try {
        Remove-Item -Recurse -Force $InstallDir
    } catch {
        Fail ("Could not remove $InstallDir : $($_.Exception.Message)`n" +
              "Quit Keyhac if it is running (tray icon > Quit), or re-run from an elevated shell for a machine-wide install.")
    }
}

if ($Uninstall) {
    # An absent install is reported, not an error: re-running an uninstall should
    # converge on "not installed" rather than fail the second time.
    if (-not (Test-Path $InstallDir)) {
        Info "Not installed: $InstallDir"
        exit 0
    }
    Remove-Install
    Success "Removed $InstallDir"
    exit 0
}

# Resolve the zip from keyhac/__init__.py's __version__ - the same single source
# of truth build.ps1 names the zip after - so the default cannot drift from what
# the build just produced.
if (-not $Zip) {
    $init = Join-Path $projectRoot 'keyhac\__init__.py'
    $m = Select-String -Path $init -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $m) { Fail "Could not read __version__ from $init" }
    $version = $m.Matches[0].Groups[1].Value
    $Zip = Join-Path $ScriptDir "build\Keyhac-$version-win64.zip"
}

if (-not (Test-Path $Zip)) {
    Fail "Zip not found: $Zip`nBuild it first with 'make windows-zip'."
}

$parent = Split-Path $InstallDir -Parent

if (Test-Path $InstallDir) {
    Info "Removing existing $InstallDir"
    Remove-Install
}

New-Item -ItemType Directory -Force -Path $parent | Out-Null

Info "Expanding $(Split-Path $Zip -Leaf) into $parent ..."
Expand-Archive -Path $Zip -DestinationPath $parent -Force

# The zip is the deliverable, so verify what came out of it rather than trusting
# that Expand-Archive succeeding means the payload is complete.
$exe = Join-Path $InstallDir 'Keyhac.exe'
if (-not (Test-Path $exe)) {
    Fail "Keyhac.exe not found at $exe after expanding the zip - the archive looks incomplete."
}

Success "Installed to $InstallDir"
Info "Run it:  & '$exe'"
