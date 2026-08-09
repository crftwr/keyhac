<#
.SYNOPSIS
    MSIX packager + local installer for Keyhac (Microsoft Store / winget
    distribution). Ported from XeFM's windows_app/build_msix.ps1.

.DESCRIPTION
    Wraps the existing windows_app\build\Keyhac bundle (produced by build.ps1)
    into an installable .msix:

      (default)   stage payload + tiles + AppxManifest -> makeappx pack
      -Sign       also self-sign the package for LOCAL testing
      -Install    trust the self-signed cert (elevates) + Add-AppxPackage (per-user)
      -Uninstall  Remove-AppxPackage + delete the throwaway signing certs (elevates)
      -TrustCert  internal: import the cert into LocalMachine\TrustedPeople (elevated child)
      -CleanCert  internal: remove the cert from LocalMachine\TrustedPeople (elevated child)

    This does NOT submit to the Store ('make release-windows-msix' does, via the
    msstore CLI). For a real submission the identity values come from Partner
    Center and Microsoft re-signs the (unsigned) package; the self-signed path
    here is purely to exercise the package on the dev box.

    The Store tile assets are NOT generated here: they are committed under
    windows_app\resources\Assets, rendered from art/icon.svg by
    tools/make_icons.py alongside the other raster icon targets.

.NOTES
    Identity comes from the gitignored windows_app\store.env (copy
    store.env.example and fill in Partner Center's "Product identity" values),
    or from an explicit -IdentityName / -Publisher / -PublisherDisplayName,
    which wins over the file. Without either the build falls back to a
    Keyhac.Prototype identity that sideloads fine but cannot be submitted, and
    warns that it did so.

    The package version defaults to keyhac/__init__.py's __version__ plus the
    Store-reserved ".0" revision (2.0.0 -> 2.0.0.0); override with -Version.
#>
[CmdletBinding()]
param(
    # PayloadSource / OutDir default to paths under the script dir, but are filled
    # in below — NOT here — because $PSScriptRoot is not reliably populated in a
    # param default when the script is launched via 'powershell -File' (as the
    # Makefile does). Evaluated here it comes back empty, yielding '\build\Keyhac'.
    [string]$PayloadSource,
    [string]$OutDir,
    # Defaults (below, in the body) to keyhac/__init__.py's __version__ + ".0" --
    # the Store wants major != 0 and revision = 0.
    [string]$Version,
    # Partner Center "Product identity" values. All three default (below, in the
    # body) to windows_app\store.env if present, else to prototype placeholders.
    [string]$IdentityName,                                # Package/Identity/Name
    [string]$Publisher,                                   # Publisher (CN=...)
    [string]$PublisherDisplayName,                        # Package/Properties/PublisherDisplayName
    [string]$Arch                 = "x64",
    [switch]$Sign,                                        # self-sign for local install test
    [switch]$Install,                                     # trust cert + install locally
    [switch]$Uninstall,                                   # remove the package + throwaway certs
    [switch]$TrustCert,                                   # internal: elevated cert-import step
    [switch]$CleanCert                                    # internal: elevated cert-removal step
)

$ErrorActionPreference = "Stop"

# Git Bash/MSYS make can launch this script with a mangled environment:
# - PATHEXT without ".EXE" (observed: just ".CPL"), which makes PowerShell
#   refuse to execute ANY .exe ("cannot run a document");
# - TEMP/TMP empty, which makes signtool fail on an .msix with the misleading
#   "This file format cannot be signed because it is not recognized" (it needs
#   a temp dir to re-emit the package).
# Restore both so native tools (robocopy, makeappx, signtool) run.
if ($env:PATHEXT -notmatch '(?i)\.EXE') { $env:PATHEXT = '.COM;.EXE;.BAT;.CMD' }
if (-not $env:TEMP) {
    $env:TEMP = if ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA 'Temp' } else { "$env:SystemRoot\Temp" }
}
if (-not $env:TMP) { $env:TMP = $env:TEMP }

# Resolve the script directory reliably (body scope), then fill path defaults.
$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) { $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$projectRoot = Split-Path -Parent $ScriptDir
if (-not $PayloadSource) { $PayloadSource = Join-Path $ScriptDir 'build\Keyhac' }
if (-not $OutDir)        { $OutDir        = Join-Path $ScriptDir 'build' }

# Resolve the package version from keyhac/__init__.py's __version__ -- the
# single source of truth build.ps1 already reads -- plus the Store-reserved
# ".0" revision, so a source version of 2.0.0 packages as 2.0.0.0. Deriving it
# (rather than hardcoding) keeps the package version from drifting behind the
# app version it contains, which matters because every Store submission needs a
# strictly higher package version than the last.
#
# Deterministic, so the separate -Install / -Uninstall invocations that follow a
# pack all compute the same $msix path.
if (-not $Version) {
    $keyhacInit = Join-Path $projectRoot 'keyhac/__init__.py'
    $m = Select-String -Path $keyhacInit -Pattern '__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $m) { throw "Could not read __version__ from $keyhacInit; pass -Version explicitly." }
    $Version = "$($m.Matches[0].Groups[1].Value).0"
}
# Validated here because makeappx reports a malformed version as an opaque
# manifest schema error. A non-numeric source version (a '2.0.1rc1'-style
# pre-release, which cannot map onto MSIX's 4 numeric parts) needs an explicit
# -Version.
if ($Version -notmatch '^[1-9][0-9]*\.[0-9]+\.[0-9]+\.0$') {
    throw ("Invalid MSIX package version '$Version': the Store requires " +
           "major.minor.build.revision with major >= 1 and revision = 0.")
}

# Local Store identity configuration (optional, gitignored) -- same pattern as
# macos_app/signing.env, and non-secret for the same reason: all three values are
# readable from any shipped package. They are kept out of git because they are
# per-Partner-Center-account, so a fork/clone must supply its own.
#
# Precedence, highest first: an explicit -IdentityName/-Publisher/... argument,
# then store.env, then the prototype placeholder (which cannot be submitted).
$storeEnv = Join-Path $ScriptDir 'store.env'
$storeCfg = @{}
if (Test-Path $storeEnv) {
    Write-Host "[INFO] Loading Store identity from $storeEnv"
    foreach ($line in Get-Content $storeEnv) {
        # KEY=VALUE; '#' comments and blank lines do not match, quotes are trimmed.
        if ($line -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$') {
            $storeCfg[$Matches[1]] = $Matches[2].Trim('"').Trim("'")
        }
    }
}
foreach ($d in @(
    @{ Param = 'IdentityName';         Key = 'KEYHAC_MSIX_IDENTITY_NAME';          Fallback = 'Keyhac.Prototype' },
    @{ Param = 'Publisher';            Key = 'KEYHAC_MSIX_PUBLISHER';              Fallback = 'CN=Keyhac Prototype Dev' },
    @{ Param = 'PublisherDisplayName'; Key = 'KEYHAC_MSIX_PUBLISHER_DISPLAY_NAME'; Fallback = 'Keyhac Prototype' }
)) {
    if ($PSBoundParameters.ContainsKey($d.Param)) { continue }   # explicit argument wins
    $value = if ($storeCfg.ContainsKey($d.Key)) { $storeCfg[$d.Key] } else { $d.Fallback }
    Set-Variable -Name $d.Param -Value $value
}
# Loud, because a prototype-identity package packs and installs locally just fine
# and is only rejected once uploaded -- after the slow pack and a Partner Center
# round trip.
if ($IdentityName -eq 'Keyhac.Prototype') {
    Write-Host ("[WARNING] Using the PROTOTYPE identity '$IdentityName' -- fine for local " +
                "testing, but Partner Center will reject it. Copy store.env.example to " +
                "store.env and fill in your Product identity values.") -ForegroundColor Yellow
}
Write-Host "[INFO] Package identity: $IdentityName  ($Publisher)"

# Artifact paths shared by build + install actions.
$msix    = "$OutDir\Keyhac-$Version-$Arch.msix"
# Packed/signed here, then renamed to $msix only on full success. Keeps the
# .msix extension (before .building) because signtool refuses to sign a file
# whose extension it doesn't recognize as an app package.
$msixTmp = "$OutDir\Keyhac-$Version-$Arch.building.msix"
$pfx     = "$OutDir\Keyhac-proto-test.pfx"
$cer     = "$OutDir\Keyhac-proto-test.cer"
$pfxPassword = "prototest"

function Find-SdkTool([string]$name) {
    # Literal Program Files fallbacks included because 'ProgramFiles(x86)' is
    # not a legal POSIX variable name, so Git Bash/MSYS make (which launches
    # this script) does not export it and the env-var form expands empty.
    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "${env:ProgramFiles}\Windows Kits\10\bin",
        "$env:SystemDrive\Program Files (x86)\Windows Kits\10\bin",
        "$env:SystemDrive\Program Files\Windows Kits\10\bin"
    ) | Where-Object { $_ -notlike '\*' } | Select-Object -Unique
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $hits = @(Get-ChildItem -Path $root -Recurse -Filter $name -ErrorAction SilentlyContinue |
                  Where-Object { $_.FullName -match "\\x64\\$([regex]::Escape($name))$" })
        if (-not $hits.Count) { continue }
        # Prefer the newest bin\10.0.xxxxx.0\x64 copy: the top-level bin\x64
        # dir (pre-1703 SDK layout) can hold an ancient signtool that does not
        # recognize the MSIX format, and a plain descending name sort would
        # pick it ('x64' sorts after '10.0.26100.0').
        $versioned = @($hits | Where-Object { $_.FullName -match '\\10\.[0-9.]+\\x64\\' } |
                       Sort-Object { [version]([regex]::Match($_.FullName, '\\(10\.[0-9.]+)\\').Groups[1].Value) } -Descending)
        if ($versioned.Count) { return $versioned[0].FullName }
        return ($hits | Sort-Object FullName -Descending | Select-Object -First 1).FullName
    }
    throw "$name not found under any Windows Kits\10\bin. Install the Windows 10/11 SDK."
}

function Test-IsAdmin {
    ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

# ===========================================================================
# Action: -TrustCert (internal, runs elevated) — trust the self-signed cert.
# ===========================================================================
if ($TrustCert) {
    if (-not (Test-Path $cer)) { throw "Certificate not found: $cer (build with -Sign first)" }
    Write-Host "[INFO] Trusting $cer in LocalMachine\TrustedPeople ..."
    Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople | Out-Null
    Write-Host "[OK] Certificate trusted."
    return
}

# ===========================================================================
# Action: -CleanCert (internal, runs elevated) — untrust the self-signed cert.
# ===========================================================================
if ($CleanCert) {
    $trusted = @(Get-ChildItem Cert:\LocalMachine\TrustedPeople -ErrorAction SilentlyContinue |
                 Where-Object { $_.Subject -eq $Publisher })
    if ($trusted.Count) {
        $trusted | Remove-Item -Force
        Write-Host "[OK] Removed $($trusted.Count) cert(s) from LocalMachine\TrustedPeople."
    } else {
        Write-Host "[INFO] No trusted cert with subject '$Publisher' found."
    }
    return
}

# ===========================================================================
# Action: -Uninstall — remove the package and the throwaway signing certs.
# ===========================================================================
if ($Uninstall) {
    # 1. Remove the installed package (per-user, no admin).
    $pkg = Get-AppxPackage -Name $IdentityName -ErrorAction SilentlyContinue
    if ($pkg) {
        $pkg | Remove-AppxPackage
        Write-Host "[OK] Removed $($pkg.PackageFullName)" -ForegroundColor Green
    } else {
        Write-Host "[INFO] Package '$IdentityName' is not installed."
    }

    # 2. Remove the signing cert (private key) from the user store (no admin).
    $mine = @(Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
              Where-Object { $_.Subject -eq $Publisher })
    if ($mine.Count) {
        $mine | Remove-Item -Force
        Write-Host "[OK] Removed $($mine.Count) signing cert(s) from CurrentUser\My."
    }

    # 3. Untrust the public cert in the machine store (needs admin -> elevate).
    $trusted = @(Get-ChildItem Cert:\LocalMachine\TrustedPeople -ErrorAction SilentlyContinue |
                 Where-Object { $_.Subject -eq $Publisher })
    if ($trusted.Count) {
        if (Test-IsAdmin) {
            $trusted | Remove-Item -Force
            Write-Host "[OK] Removed $($trusted.Count) cert(s) from LocalMachine\TrustedPeople."
        } else {
            Write-Host "[INFO] Requesting elevation to untrust the certificate (accept the UAC prompt)..."
            $psExe = (Get-Process -Id $PID).Path
            $argLine = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -CleanCert -Publisher `"$Publisher`""
            $proc = Start-Process -FilePath $psExe -Verb RunAs -Wait -PassThru -ArgumentList $argLine
            if ($proc.ExitCode -ne 0) { Write-Host "[WARN] Elevated cert cleanup failed or was cancelled (exit $($proc.ExitCode))." }
        }
    }

    # 4. Delete the throwaway cert files (they also go with 'make clean-windows').
    foreach ($file in @($pfx, $cer)) {
        if (Test-Path $file) { Remove-Item -Force $file; Write-Host "[OK] Deleted $file" }
    }
    return
}

# ===========================================================================
# Action: -Install — trust the cert (elevates) then install per-user.
# ===========================================================================
if ($Install) {
    if (-not (Test-Path $msix)) { throw "Package not found: $msix. Run 'make windows-msix' first." }
    if (-not (Test-Path $cer))  { throw "Signing cert not found: $cer. Run 'make windows-msix' first." }

    # Step 1: trust the self-signed cert (machine-wide store => needs admin).
    if (Test-IsAdmin) {
        Import-Certificate -FilePath $cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople | Out-Null
        Write-Host "[OK] Certificate trusted."
    } else {
        Write-Host "[INFO] Requesting elevation to trust the signing certificate (accept the UAC prompt)..."
        $psExe = (Get-Process -Id $PID).Path
        $argLine = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -TrustCert -OutDir `"$OutDir`""
        $proc = Start-Process -FilePath $psExe -Verb RunAs -Wait -PassThru -ArgumentList $argLine
        if ($proc.ExitCode -ne 0) { throw "Elevated cert-trust step failed or was cancelled (exit $($proc.ExitCode))." }
    }

    # Step 2: install per-user (cert is now trusted; Add-AppxPackage needs no admin).
    Write-Host "[INFO] Installing package (Add-AppxPackage)..."
    Add-AppxPackage -Path $msix
    $pkg = Get-AppxPackage -Name $IdentityName -ErrorAction SilentlyContinue
    if (-not $pkg) { throw "Install did not complete (package '$IdentityName' not found afterward)." }
    Write-Host "[OK] Installed $($pkg.PackageFullName)" -ForegroundColor Green
    Write-Host "Launch 'Keyhac' from the Start menu. Remove with: make uninstall-windows-msix"
    return
}

# ===========================================================================
# Default action: build (and optionally -Sign) the package.
# ===========================================================================
$makeappx = Find-SdkTool "makeappx.exe"
Write-Host "[INFO] makeappx: $makeappx"
if ($Sign) {
    $signtool = Find-SdkTool "signtool.exe"
    Write-Host "[INFO] signtool: $signtool"
}

if (-not (Test-Path $PayloadSource))              { throw "Payload source not found: $PayloadSource. Run 'make windows-app' first." }
if (-not (Test-Path "$PayloadSource\Keyhac.exe")) { throw "Keyhac.exe not found in payload source $PayloadSource. Run 'make windows-app' first." }
# Checked here rather than left to makeappx: the manifest below declares an app
# execution alias on this file, and a missing alias target is reported as an
# opaque manifest validation error rather than as a stale bundle.
if (-not (Test-Path "$PayloadSource\keyhac-mcp-bridge.exe")) { throw "keyhac-mcp-bridge.exe not found in payload source $PayloadSource (bundle predates the MCP alias). Run 'make windows-app' first." }

# ---- 1. Check the committed Store tile assets ------------------------------
# Rendered from art/icon.svg by tools/make_icons.py and committed, like every
# other raster icon target; regenerate with 'make icons' if missing.
$assetsSrc = "$ScriptDir\resources\Assets"
foreach ($tile in @('StoreLogo.png', 'Square44x44Logo.png', 'Square44x44Logo.scale-200.png',
                    'Square150x150Logo.png', 'Square150x150Logo.scale-200.png',
                    'Wide310x150Logo.png')) {
    if (-not (Test-Path "$assetsSrc\$tile")) {
        throw "Store tile missing: $assetsSrc\$tile. Regenerate with 'make icons'."
    }
}

# ---- 2. Stage the payload -------------------------------------------------
$staging = "$OutDir\msix-staging"
if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
New-Item -ItemType Directory -Force -Path $staging | Out-Null

Write-Host "[INFO] Staging payload from $PayloadSource ..."
# Absolute path: the PATH this script inherits from Git Bash/MSYS make does not
# always resolve System32 tools by bare name.
& "$env:SystemRoot\System32\robocopy.exe" $PayloadSource $staging /E /NFL /NDL /NJH /NJS /NP | Out-Null   # 0-7 = success
if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE)" }
$global:LASTEXITCODE = 0

Copy-Item -Recurse -Force $assetsSrc "$staging\Assets"

# ---- 3. Generate AppxManifest.xml at the payload root ---------------------
# runFullTrust: Keyhac is a classic Win32 desktop app (WH_KEYBOARD_LL hook,
# SendInput, UIA) running as the user outside an AppContainer — the standard
# packaged-desktop-app category, auto-approved during Store certification.
#
# windows.startupTask: the MSIX-native replacement for the manual
# shell:startup shortcut doc/installation.md describes for the zip install.
# For a packaged desktop app it is enabled by default; the user can toggle it
# in Settings > Apps > Startup (or Task Manager's Startup tab).
#
# windows.appExecutionAlias: the ONLY way anything in this package can be
# started by a process that is not part of it. Everything under
# C:\Program Files\WindowsApps refuses CreateProcess from an ordinary process
# with "Access is denied", so the keyhac-mcp-bridge.exe sitting in the payload
# is unreachable to an MCP client - Claude Desktop's server died at startup
# with no output, which is how this was found. The alias puts a stub in
# %LOCALAPPDATA%\Microsoft\WindowsApps (on PATH) that launches the bridge with
# this package's identity, which is then allowed to run the bundled
# interpreter. It is what mcp.json publishes on a packaged install; see
# keyhac/mcp/server.py's bridge_command(). The alias name must match the file
# name, and the target must be an .exe - which is why the bridge is one.
$manifest = @"
<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:uap3="http://schemas.microsoft.com/appx/manifest/uap/windows10/3"
  xmlns:desktop="http://schemas.microsoft.com/appx/manifest/desktop/windows10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  IgnorableNamespaces="uap uap3 desktop rescap">

  <Identity
    Name="$IdentityName"
    Publisher="$Publisher"
    Version="$Version"
    ProcessorArchitecture="$Arch" />

  <Properties>
    <DisplayName>Keyhac</DisplayName>
    <PublisherDisplayName>$PublisherDisplayName</PublisherDisplayName>
    <Logo>Assets\StoreLogo.png</Logo>
  </Properties>

  <Dependencies>
    <TargetDeviceFamily Name="Windows.Desktop"
                        MinVersion="10.0.17763.0"
                        MaxVersionTested="10.0.26100.0" />
  </Dependencies>

  <Resources>
    <Resource Language="en-us" />
  </Resources>

  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>

  <Applications>
    <Application Id="Keyhac"
                 Executable="Keyhac.exe"
                 EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements
        DisplayName="Keyhac"
        Description="Python-scriptable keyboard customization tool"
        Square150x150Logo="Assets\Square150x150Logo.png"
        Square44x44Logo="Assets\Square44x44Logo.png"
        BackgroundColor="transparent">
        <uap:DefaultTile Wide310x150Logo="Assets\Wide310x150Logo.png" />
      </uap:VisualElements>
      <Extensions>
        <desktop:Extension
          Category="windows.startupTask"
          Executable="Keyhac.exe"
          EntryPoint="Windows.FullTrustApplication">
          <desktop:StartupTask
            TaskId="KeyhacStartupTask"
            Enabled="true"
            DisplayName="Keyhac" />
        </desktop:Extension>
        <uap3:Extension
          Category="windows.appExecutionAlias"
          Executable="keyhac-mcp-bridge.exe"
          EntryPoint="Windows.FullTrustApplication">
          <uap3:AppExecutionAlias>
            <desktop:ExecutionAlias Alias="keyhac-mcp-bridge.exe" />
          </uap3:AppExecutionAlias>
        </uap3:Extension>
      </Extensions>
    </Application>
  </Applications>
</Package>
"@
$manifestPath = "$staging\AppxManifest.xml"
# UTF-8 without BOM (makeappx dislikes a BOM on the manifest).
[System.IO.File]::WriteAllText($manifestPath, $manifest, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "[INFO] Wrote manifest: $manifestPath"

# ---- 4. Pack + optional sign (atomic) -------------------------------------
# The final $msix must only ever exist as a fully-built (and, if requested,
# fully-signed) artifact. So we pack to a temp path and promote it with a
# rename only after every step below succeeds. Any stale artifacts from a
# previously-aborted run are cleared first, so a failure here leaves NO $msix
# rather than an unsigned one masquerading as complete -- which is what the
# -Install step would otherwise trust.
foreach ($stale in @($msix, $msixTmp, $pfx, $cer)) {
    if (Test-Path $stale) { Remove-Item -Force $stale }
}

# When signing, mint the cert FIRST: it is the cheap, most-likely-to-fail step
# (cert policy, missing signtool), so failing before the slow pack is cheaper
# and never leaves a packed-but-unsigned artifact behind.
if ($Sign) {
    Write-Host "[INFO] Creating self-signed cert (Subject must equal Publisher)..."
    # Drop any prior private-key certs with the same subject so CurrentUser\My
    # does not accumulate a duplicate on every rebuild (trust lives in the
    # machine store via the .cer, so removing these here is safe).
    Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
        Where-Object { $_.Subject -eq $Publisher } | Remove-Item -Force -ErrorAction SilentlyContinue
    $cert = New-SelfSignedCertificate -Type Custom -CertStoreLocation Cert:\CurrentUser\My `
        -Subject $Publisher -KeyUsage DigitalSignature -FriendlyName "Keyhac MSIX prototype" `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3")
    $securePw = ConvertTo-SecureString -String $pfxPassword -Force -AsPlainText
    Export-PfxCertificate -Cert $cert -FilePath $pfx -Password $securePw | Out-Null
    Export-Certificate -Cert $cert -FilePath $cer | Out-Null
}

Write-Host "[INFO] Packing -> $msixTmp"
& $makeappx pack /d $staging /p $msixTmp /o
if ($LASTEXITCODE -ne 0) { throw "makeappx pack failed ($LASTEXITCODE)" }

if ($Sign) {
    Write-Host "[INFO] Signing package..."
    & $signtool sign /fd SHA256 /f $pfx /p $pfxPassword $msixTmp
    if ($LASTEXITCODE -ne 0) { throw "signtool sign failed ($LASTEXITCODE)" }
}

# Commit point: promote the temp package to its final name. From here on the
# artifact is guaranteed complete, so -Install can trust its presence.
Move-Item -Force $msixTmp $msix
Write-Host "[OK] Built $msix"
if ($Sign) {
    Write-Host "[OK] Signed $msix"
    Write-Host "Install locally with:  make install-windows-msix" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[DONE] Package: $msix" -ForegroundColor Green
