<#
.SYNOPSIS
    Build the self-contained Keyhac Windows application bundle.

.DESCRIPTION
    Windows counterpart of macos_app/build.sh. Assembles build\Keyhac\
    containing a compiled C launcher (Keyhac.exe), an embedded CPython (from
    the python.org "embeddable" package matching the .venv's version), Keyhac's
    own code, PuiKit, and all third-party dependencies.
    Structure ported from XeFM's windows_app/build.ps1.

.PARAMETER Version
    Version string embedded in Keyhac.exe (e.g. 2.0.0). Defaults to
    keyhac/__init__.py's __version__.

.PARAMETER PythonEmbedUrl
    Override the embeddable-package download URL (default: python.org, matching
    the venv's exact version).

.PARAMETER Zip
    Also produce build\Keyhac-<version>-win64.zip for distribution.

.PARAMETER Clean
    Remove the build directory and exit.

.PARAMETER Install
    Install the already-built bundle to -InstallDir (default C:\Program Files\Keyhac),
    self-elevating via UAC. Does not build; run the default build first.

.PARAMETER InstallDir
    Target directory for -Install (default C:\Program Files\Keyhac).

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File windows_app\build.ps1
    powershell -ExecutionPolicy Bypass -File windows_app\build.ps1 -Version 2.0.0 -Zip
    powershell -ExecutionPolicy Bypass -File windows_app\build.ps1 -Install
#>
[CmdletBinding()]
param(
    [string]$Version,
    [string]$PythonEmbedUrl,
    [switch]$Zip,
    [switch]$Clean,
    [switch]$Install,
    [string]$InstallDir = 'C:\Program Files\Keyhac'
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
$ScriptDir   = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $ScriptDir
$SrcDir      = Join-Path $ScriptDir 'src'
$ResDir      = Join-Path $ScriptDir 'resources'
$BuildDir    = Join-Path $ScriptDir 'build'
$AppRoot     = Join-Path $BuildDir 'Keyhac'    # the distributable folder
$ObjDir      = Join-Path $BuildDir 'obj'      # launcher intermediates
$CacheDir    = Join-Path $ScriptDir '.cache'  # downloaded embeddable zips

$AppName = 'Keyhac'

function Info    ($m) { Write-Host "[INFO] $m" }
function Success ($m) { Write-Host "[SUCCESS] $m" -ForegroundColor Green }
function Warn    ($m) { Write-Host "[WARNING] $m" -ForegroundColor Yellow }
function Fail    ($m) { Write-Host "[ERROR] $m" -ForegroundColor Red; exit 1 }

if ($Clean) {
    if (Test-Path $BuildDir) { Remove-Item -Recurse -Force $BuildDir; Info "Removed $BuildDir" }
    else { Info "Nothing to clean." }
    return
}

# ---------------------------------------------------------------------------
# Install action: copy the built bundle to $InstallDir (default Program Files).
# Writing under Program Files needs elevation, so re-launch elevated via UAC if
# we are not already an administrator; the (unelevated) parent then verifies the
# result so the outcome is reported in the visible shell.
# ---------------------------------------------------------------------------
function Invoke-Install {
    if (-not (Test-Path (Join-Path $AppRoot 'Keyhac.exe'))) {
        Fail "No built bundle at $AppRoot. Run 'make windows-app' first."
    }

    $isAdmin = ([Security.Principal.WindowsPrincipal] `
                [Security.Principal.WindowsIdentity]::GetCurrent()
               ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)

    if (-not $isAdmin) {
        Info "Administrator rights are required to write to '$InstallDir'."
        Info 'Requesting elevation (accept the UAC prompt)...'
        $psExe = (Get-Process -Id $PID).Path
        $argLine = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Install -InstallDir `"$InstallDir`""
        try {
            Start-Process -FilePath $psExe -Verb RunAs -Wait -ArgumentList $argLine
        } catch {
            Fail "Elevation was declined or failed: $($_.Exception.Message)"
        }
        # Verify from the (unelevated) parent so success/failure is visible here.
        if (Test-Path (Join-Path $InstallDir 'Keyhac.exe')) {
            Success "Installed to $InstallDir"
        } else {
            Fail "Install did not complete (the elevated step failed or was cancelled)."
        }
        return
    }

    # --- Elevated: do the copy -------------------------------------------------
    Info "Installing Keyhac to $InstallDir ..."
    if (Test-Path $InstallDir) {
        Info "Removing existing $InstallDir"
        Remove-Item -Recurse -Force $InstallDir
    }
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    robocopy $AppRoot $InstallDir /E /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "robocopy failed copying the bundle (code $LASTEXITCODE)" }
    $global:LASTEXITCODE = 0

    # Start Menu shortcut (all users) for discoverability; non-fatal if it fails.
    try {
        $startMenu = [Environment]::GetFolderPath('CommonPrograms')
        $lnkPath = Join-Path $startMenu 'Keyhac.lnk'
        $exePath = Join-Path $InstallDir 'Keyhac.exe'
        $shell = New-Object -ComObject WScript.Shell
        $sc = $shell.CreateShortcut($lnkPath)
        $sc.TargetPath = $exePath
        $sc.WorkingDirectory = $InstallDir
        $sc.IconLocation = "$exePath,0"
        $sc.Description = 'Keyhac - Python-scriptable keyboard customization'
        $sc.Save()
        Info "Created Start Menu shortcut: $lnkPath"
    } catch {
        Warn "Could not create Start Menu shortcut: $($_.Exception.Message)"
    }

    Success "Installed to $InstallDir"
    Info "Launch it from the Start Menu (Keyhac) or run: `"$($exePath)`""
}

if ($Install) {
    Invoke-Install
    return
}

# ---------------------------------------------------------------------------
# Step 1: Locate the build virtual environment and derive Python facts
# ---------------------------------------------------------------------------
Info 'Step 1: Inspecting the build virtual environment...'

$VenvPy = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPy)) {
    Fail ".venv not found at $VenvPy. Run 'make venv' first."
}

# One round-trip that prints the facts we need, tab-separated.
$probe = & $VenvPy -c @"
import sys, sysconfig, platform
print('\t'.join([
    platform.python_version(),                                   # 3.14.6
    '%d.%d' % sys.version_info[:2],                              # 3.14
    '%d%d' % sys.version_info[:2],                               # 314
    sys.base_prefix,                                             # full-CPython root
    sysconfig.get_paths()['purelib'],                           # venv site-packages
]))
"@
if ($LASTEXITCODE -ne 0) { Fail 'Failed to probe the venv interpreter.' }
$parts       = $probe.Trim() -split "`t"
$PyFull      = $parts[0]
$PyXY        = $parts[1]
$PyNoDot     = $parts[2]
$BasePrefix  = $parts[3]
$SitePkgs    = $parts[4]

$PyInclude = Join-Path $BasePrefix 'include'
$PyLibs    = Join-Path $BasePrefix 'libs'

Info "Python:        $PyFull (ABI cp$PyNoDot)"
Info "base_prefix:   $BasePrefix"
Info "site-packages: $SitePkgs"

if (-not (Test-Path (Join-Path $PyInclude 'Python.h'))) {
    Fail "Python.h not found under $PyInclude. The .venv must be backed by a full CPython install (headers + libs)."
}
if (-not (Test-Path (Join-Path $PyLibs "python$PyNoDot.lib"))) {
    Fail "python$PyNoDot.lib not found under $PyLibs."
}

# Resolve the version string to embed.
if (-not $Version) {
    $keyhacInit = Join-Path $ProjectRoot 'keyhac/__init__.py'
    $m = Select-String -Path $keyhacInit -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if ($m) { $Version = $m.Matches[0].Groups[1].Value } else { $Version = '0.0.0' }
}
Info "Bundle version: $Version"

# ---------------------------------------------------------------------------
# Step 2: Locate the C toolchain (cl.exe + rc.exe), importing VS env if needed
# ---------------------------------------------------------------------------
Info 'Step 2: Locating the MSVC toolchain...'

function Import-VsDevEnv {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) { return $false }
    $vsPath = & $vswhere -latest -products * `
        -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
        -property installationPath
    if (-not $vsPath) { return $false }
    $devCmd = Join-Path $vsPath 'Common7\Tools\VsDevCmd.bat'
    if (-not (Test-Path $devCmd)) { return $false }
    Info "Importing environment from $devCmd"
    cmd /c "`"$devCmd`" -arch=amd64 -host_arch=amd64 && set" | ForEach-Object {
        if ($_ -match '^([^=]+)=(.*)$') {
            [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2])
        }
    }
    return $true
}

if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    if (-not (Import-VsDevEnv)) {
        Fail @"
MSVC toolchain (cl.exe) not found.

Install the 'Build Tools for Visual Studio' with the 'Desktop development with
C++' workload (includes cl.exe, rc.exe, and the Windows SDK):
  https://visualstudio.microsoft.com/downloads/  (scroll to 'Tools for Visual Studio')

This is the Windows analog of the macOS build's Xcode Command Line Tools.
Once installed, re-run this script from a normal PowerShell prompt (it will
auto-import the VS environment) or from a 'x64 Native Tools Command Prompt'.
"@
    }
}
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) { Fail 'cl.exe still not on PATH after importing the VS environment.' }
if (-not (Get-Command rc.exe -ErrorAction SilentlyContinue)) { Fail 'rc.exe (Windows SDK) not found. Install the Windows 10/11 SDK component.' }
Info "cl.exe: $((Get-Command cl.exe).Source)"
Info "rc.exe: $((Get-Command rc.exe).Source)"

# ---------------------------------------------------------------------------
# Step 3: Fetch + extract the embeddable CPython (version-locked to the venv)
# ---------------------------------------------------------------------------
Info 'Step 3: Preparing the embedded CPython runtime...'

if (Test-Path $AppRoot) { Remove-Item -Recurse -Force $AppRoot }
New-Item -ItemType Directory -Force -Path $AppRoot | Out-Null
New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null

$EmbedZipName = "python-$PyFull-embed-amd64.zip"
if (-not $PythonEmbedUrl) { $PythonEmbedUrl = "https://www.python.org/ftp/python/$PyFull/$EmbedZipName" }
$CachedZip = Join-Path $CacheDir $EmbedZipName

if (-not (Test-Path $CachedZip)) {
    Info "Downloading $PythonEmbedUrl"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    try {
        Invoke-WebRequest -Uri $PythonEmbedUrl -OutFile $CachedZip -UseBasicParsing
    } catch {
        Fail "Failed to download the embeddable package for Python $PyFull.`n$($_.Exception.Message)`nProvide it manually via -PythonEmbedUrl or place '$EmbedZipName' in $CacheDir."
    }
} else {
    Info "Using cached $EmbedZipName"
}

# The whole embedded CPython goes under runtime\ (keeps the bundle root tidy).
# Keyhac.exe delay-loads python3XX.dll from here after adding runtime\ to the
# DLL search path - see launcher.c - so nothing from the runtime needs to sit
# at the root next to the exe.
$RuntimeDir = Join-Path $AppRoot 'runtime'
New-Item -ItemType Directory -Force -Path $RuntimeDir | Out-Null
Info "Extracting embedded CPython into $RuntimeDir"
Expand-Archive -Path $CachedZip -DestinationPath $RuntimeDir -Force

# The embeddable ships a python3XX._pth that would force an isolated sys.path if
# someone ran the bundled python.exe directly. Our launcher configures paths
# explicitly via PyConfig; remove it so the two mechanisms can't disagree.
Get-ChildItem -Path $RuntimeDir -Filter '*._pth' -File | Remove-Item -Force -ErrorAction SilentlyContinue

if (-not (Test-Path (Join-Path $RuntimeDir "python$PyNoDot.dll"))) {
    Fail "python$PyNoDot.dll missing after extraction - the embeddable package may not match Python $PyFull."
}

# ---------------------------------------------------------------------------
# Step 4: Assemble Keyhac's own code under app\
# ---------------------------------------------------------------------------
Info 'Step 4: Copying Keyhac source, PuiKit, and LICENSE...'

$AppDir = Join-Path $AppRoot 'app'
New-Item -ItemType Directory -Force -Path $AppDir | Out-Null

function Copy-Tree ($src, $dst) {
    # robocopy exit codes 0-7 indicate success; >=8 is a real failure.
    robocopy $src $dst /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { Fail "robocopy failed copying $src -> $dst (code $LASTEXITCODE)" }
    $global:LASTEXITCODE = 0
}

Copy-Tree (Join-Path $ProjectRoot 'keyhac') (Join-Path $AppDir 'keyhac')

# Resolve PuiKit's real source dir from the venv (installed editable or from
# PyPI), like macos_app/build.sh does, so PUIKIT_DIR overrides are honoured.
$PuikitSrc = & $VenvPy -c "import puikit, os; print(os.path.dirname(os.path.abspath(puikit.__file__)))"
if ($LASTEXITCODE -ne 0 -or -not $PuikitSrc -or -not (Test-Path $PuikitSrc)) {
    Fail "PuiKit not importable from the venv (resolved: '$PuikitSrc'). Install it: make install-puikit"
}
Info "PuiKit source: $PuikitSrc"

# PuiKit's bundled fonts (Noto Sans/Mono + CJK) are large OFL-licensed binaries
# fetched on demand, so an editable checkout may not have them yet. Fetch is
# idempotent; a wheel install ships them already (no scripts/ dir - skipped).
$FetchFonts = Join-Path (Split-Path -Parent $PuikitSrc) 'scripts\fetch_fonts.py'
if (Test-Path $FetchFonts) {
    Info 'Ensuring bundled Noto fonts are present...'
    & $VenvPy $FetchFonts
    if ($LASTEXITCODE -ne 0) { Warn 'Font fetch failed (offline?); relying on fonts already in the checkout' }
}

Copy-Tree $PuikitSrc (Join-Path $AppDir 'puikit')

# Verify the bundled fonts made it into the app. Shipping a bundle that
# silently falls back to OS fonts (and breaks Japanese text) is the regression
# this guards against, so a missing font fails the build.
$FontsDest = Join-Path $AppDir 'puikit\fonts'
$RequiredFonts = @('NotoSans-Regular.ttf', 'NotoSans-Bold.ttf',
                   'NotoSansMono-Regular.ttf', 'NotoSansMono-Bold.ttf',
                   'NotoSansCJKjp-Regular.otf', 'NotoSansMonoCJKjp-Regular.otf')
$MissingFonts = $RequiredFonts | Where-Object { -not (Test-Path (Join-Path $FontsDest $_)) }
if ($MissingFonts) {
    Fail "Bundled Noto fonts missing from ${FontsDest}: $($MissingFonts -join ', ')`nFetch them and rebuild: $VenvPy $FetchFonts"
}
Info "Bundled Noto fonts present in $FontsDest"

# Keyhac's own LICENSE goes at the bundle root, alongside THIRD_PARTY_NOTICES.txt.
if (Test-Path (Join-Path $ProjectRoot 'LICENSE')) {
    Copy-Item (Join-Path $ProjectRoot 'LICENSE') (Join-Path $AppRoot 'LICENSE') -Force
}

# ---------------------------------------------------------------------------
# Step 5: Collect third-party dependencies into Lib\site-packages
# ---------------------------------------------------------------------------
# Uses the shared, platform-agnostic collector in tools/ (its PyObjC check
# self-skips off darwin). It resolves the runtime closure of pyproject.toml's
# [project] dependencies via installed metadata - honouring environment
# markers, so the pyobjc frameworks are not picked up on Windows.
# --include-deps-of puikit pulls in PuiKit's own runtime deps (numpy, which the
# win32 Direct2D backend imports) without copying PuiKit itself (its source is
# copied into app\puikit above). Each dist is copied with its .dist-info, whose
# license text the notices generator reads in Step 5b.
Info 'Step 5: Collecting third-party dependencies...'

$SitePkgsDest = Join-Path $AppRoot 'Lib\site-packages'
$SharedCollector = Join-Path $ProjectRoot 'tools\collect_dependencies.py'
$Pyproject = Join-Path $ProjectRoot 'pyproject.toml'
& $VenvPy $SharedCollector --pyproject $Pyproject --dest $SitePkgsDest --include-deps-of puikit
if ($LASTEXITCODE -ne 0) { Fail 'Dependency collection failed.' }

# ---------------------------------------------------------------------------
# Step 5b: Generate aggregated THIRD_PARTY_NOTICES.txt
# ---------------------------------------------------------------------------
# Reproduces the license text of every bundled Python distribution (scanned from
# their .dist-info under Lib\site-packages) plus the non-distribution components:
# the embedded CPython, the copied-in PuiKit source, and the bundled Noto fonts.
# The generator (shared with macos_app/build.sh) fails the build if any bundled
# distribution has no discoverable license, so an incomplete notice can't ship.
Info 'Step 5b: Generating third-party license notices...'

$NoticesScript = Join-Path $ProjectRoot 'tools\generate_third_party_notices.py'
if (-not (Test-Path $NoticesScript)) { Fail "Notices generator not found at $NoticesScript" }
$NoticesOut = Join-Path $AppRoot 'THIRD_PARTY_NOTICES.txt'
$NoticesExtras = @()

# Embedded interpreter's PSF license (ships as the embeddable's LICENSE.txt,
# now under runtime\ after the Step 3 consolidation).
$PyLicense = Join-Path $RuntimeDir 'LICENSE.txt'
if (Test-Path $PyLicense) {
    $NoticesExtras += @('--extra', "Python $PyXY interpreter and standard library (Python Software Foundation License Agreement)=$PyLicense")
} else {
    Warn "Embedded Python LICENSE.txt not found at $PyLicense; interpreter will be omitted from notices."
}

# PuiKit's LICENSE location depends on how PuiKit is installed:
#   * editable checkout (PUIKIT_DIR / ..\puikit): LICENSE sits at the checkout
#     root, one level above the package dir.
#   * published wheel: LICENSE ships inside puikit-*.dist-info (...\licenses\LICENSE).
# In the wheel case the package dir's parent IS the venv site-packages, which
# holds that .dist-info; in the editable case the checkout-root LICENSE is found
# first. So a single parent path resolves both layouts.
$PuikitParent = Split-Path -Parent $PuikitSrc
$PuikitLicense = $null
$rootLicense = Join-Path $PuikitParent 'LICENSE'
if (Test-Path $rootLicense) {
    $PuikitLicense = $rootLicense
} else {
    $distInfo = Get-ChildItem -Path $PuikitParent -Directory -Filter 'puikit-*.dist-info' -ErrorAction SilentlyContinue |
                Select-Object -First 1
    if ($distInfo) {
        $PuikitLicense = Get-ChildItem -Path $distInfo.FullName -Recurse -File -ErrorAction SilentlyContinue |
                         Where-Object { $_.Name -match '^LICEN[SC]E' } |
                         Select-Object -First 1 -ExpandProperty FullName
    }
}
if ($PuikitLicense -and (Test-Path $PuikitLicense)) {
    $NoticesExtras += @('--extra', "PuiKit (MIT License)=$PuikitLicense")
} else {
    Fail "PuiKit LICENSE not found (looked for '$rootLicense' and puikit-*.dist-info under '$PuikitParent'). Install it: make install-puikit"
}

# Bundled fonts (SIL OFL 1.1) - the license files travel inside the copied
# puikit\fonts. The CJK pair has its own OFL text.
$FontsOfl = Join-Path $FontsDest 'OFL.txt'
if (Test-Path $FontsOfl) {
    $NoticesExtras += @('--extra', "Noto Sans & Noto Sans Mono fonts (SIL Open Font License 1.1)=$FontsOfl")
} else {
    Fail "Font license OFL.txt not found at $FontsOfl"
}
$FontsOflCjk = Join-Path $FontsDest 'OFL-CJK.txt'
if (Test-Path $FontsOflCjk) {
    $NoticesExtras += @('--extra', "Noto Sans CJK JP fonts (SIL Open Font License 1.1)=$FontsOflCjk")
}

$NoticesArgs = @('--title', 'Keyhac', '--scan', $SitePkgsDest) + $NoticesExtras + @('--output', $NoticesOut)
& $VenvPy $NoticesScript @NoticesArgs
if ($LASTEXITCODE -ne 0) { Fail 'Failed to generate third-party license notices (see errors above).' }

# ---------------------------------------------------------------------------
# Step 6: Pre-compile app + deps to .pyc (launcher runs with write_bytecode=0)
# ---------------------------------------------------------------------------
Info 'Step 6: Pre-compiling Python files...'
& $VenvPy -m compileall -q $AppDir $SitePkgsDest
if ($LASTEXITCODE -ne 0) { Warn 'compileall reported problems (non-fatal).' }

# ---------------------------------------------------------------------------
# Step 7: Build resources and compile the launcher
# ---------------------------------------------------------------------------
Info 'Step 7: Compiling the launcher...'

New-Item -ItemType Directory -Force -Path $ObjDir | Out-Null

# Stage .rc + .manifest + .ico into the obj dir so the .rc's relative includes
# resolve. The icon is the committed keyhac/ui/assets/keyhac.ico (regenerated
# from art/icon.svg by `make icons`).
Copy-Item (Join-Path $ResDir 'Keyhac.rc') $ObjDir -Force
Copy-Item (Join-Path $ResDir 'Keyhac.manifest') $ObjDir -Force
$IcoSource = Join-Path $ProjectRoot 'keyhac\ui\assets\keyhac.ico'
if (-not (Test-Path $IcoSource)) { Fail "App icon not found at $IcoSource. Run 'make icons'." }
Copy-Item $IcoSource (Join-Path $ObjDir 'Keyhac.ico') -Force
# The icon is compiled into Keyhac.exe as a resource (see Keyhac.rc); the tray
# still loads its own keyhac.ico from app\keyhac\ui\assets at runtime.

# Generate version_generated.h from $Version (major,minor,patch,build).
$vparts = @($Version -split '[.\-+]') | Where-Object { $_ -match '^\d+$' }
while ($vparts.Count -lt 4) { $vparts += '0' }
$verHeader = @"
/* Generated by build.ps1 - do not edit. */
#pragma once
#define KEYHAC_VER_MAJOR $($vparts[0])
#define KEYHAC_VER_MINOR $($vparts[1])
#define KEYHAC_VER_PATCH $($vparts[2])
#define KEYHAC_VER_BUILD $($vparts[3])
#define KEYHAC_VER_STR   "$Version"
"@
Set-Content -Path (Join-Path $ObjDir 'version_generated.h') -Value $verHeader -Encoding ASCII

# Compile the resource script -> Keyhac.res
$ResOut = Join-Path $ObjDir 'Keyhac.res'
& rc.exe /nologo /fo $ResOut (Join-Path $ObjDir 'Keyhac.rc')
if ($LASTEXITCODE -ne 0) { Fail 'rc.exe failed.' }

# Compile + link the launcher -> Keyhac.exe (GUI subsystem, no console).
# /MT (static CRT) so Keyhac.exe has no load-time DLL dependency of its own -
# the whole CPython runtime, CRT included, lives under runtime\. python3XX.dll
# is delay-loaded (delayimp.lib) so it is not resolved until the launcher has
# added runtime\ to the DLL search path.
$ExeOut = Join-Path $AppRoot "$AppName.exe"
$clArgs = @(
    '/nologo', '/O2', '/MT', '/W3',
    "/I$PyInclude",
    (Join-Path $SrcDir 'launcher.c'),
    $ResOut,
    "/Fe:$ExeOut",
    "/Fo:$ObjDir\",
    '/link',
    "/LIBPATH:$PyLibs",
    '/SUBSYSTEM:WINDOWS',
    "/DELAYLOAD:python$PyNoDot.dll",
    'delayimp.lib',
    # We embed our own application manifest via Keyhac.rc (RT_MANIFEST).
    # Suppress the linker's auto-generated manifest so the exe doesn't end up
    # with two conflicting RT_MANIFEST resources (two <assembly> roots => the
    # SxS loader reports "Invalid Xml syntax" and the app fails to start).
    '/MANIFEST:NO'
)
Info "cl.exe $($clArgs -join ' ')"
& cl.exe @clArgs
if ($LASTEXITCODE -ne 0) { Fail 'cl.exe failed to build the launcher.' }
if (-not (Test-Path $ExeOut)) { Fail "Launcher was not produced at $ExeOut." }

Success "Built $ExeOut"

# ---------------------------------------------------------------------------
# Step 8 (optional): Zip for distribution
# ---------------------------------------------------------------------------
if ($Zip) {
    $ZipOut = Join-Path $BuildDir "Keyhac-$Version-win64.zip"
    if (Test-Path $ZipOut) { Remove-Item -Force $ZipOut }
    Info "Creating $ZipOut"
    Compress-Archive -Path $AppRoot -DestinationPath $ZipOut -Force
    Success "Created $ZipOut"
}

Write-Host ''
Success 'Build complete.'
Info "Bundle: $AppRoot"
Info "Run it:  & '$ExeOut'"
