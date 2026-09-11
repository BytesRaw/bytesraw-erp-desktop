<#
.SYNOPSIS
    Builds the Bytesraw ERP one-folder bundle, the installer, and the portable
    zip. Roadmap M7.1 and M7.2.

.DESCRIPTION
    Run from anywhere; paths resolve from the script's own location.

        .\packaging\build.ps1

    Outputs, all in dist\:
        BytesrawERP\                            the one-folder app
        BytesrawERP-<version>-setup.exe         the installer
        BytesrawERP-<version>-windows-x64.zip   the portable build
        SHA256SUMS.txt                          checksums for both archives

    The version is read from constants.py, so it is never typed twice.

.PARAMETER SkipApp
    Reuse the existing dist\BytesrawERP and only rebuild the installer and zip.
    PyInstaller takes minutes; iterating on the .iss should not.

.PARAMETER SkipInstaller
    Build the app and the zip but not the installer, for a machine with no
    Inno Setup.

.PARAMETER SkipZip
    Skip the portable zip.

.PARAMETER SkipSums
    Do not write SHA256SUMS.txt. For a pipeline that signs the binaries after
    this script runs: checksums taken before signing describe files that no
    longer exist byte for byte.

.PARAMETER SignCommand
    A signtool command line handed to Inno Setup, with $f where the file name
    goes. Passing it also signs the embedded uninstaller. Leave empty for an
    unsigned build.
#>
[CmdletBinding()]
param(
    [switch] $SkipApp,
    [switch] $SkipInstaller,
    [switch] $SkipZip,
    [switch] $SkipSums,
    [string] $SignCommand = ''
)

$ErrorActionPreference = 'Stop'

function Invoke-Native {
    <#
        Runs a native executable and throws only on a non-zero exit code.

        Not simply "& $exe; if ($LASTEXITCODE)...": under Windows PowerShell
        5.1 a native command's stderr arrives as NativeCommandError records,
        and with $ErrorActionPreference = 'Stop' those are terminating. That
        makes PyInstaller - which writes its whole INFO log to stderr - "fail"
        on its first line whenever the caller pipes or redirects the stream.
        The exit code is the only part worth believing.
    #>
    param(
        [Parameter(Mandatory)] [string] $FilePath,
        [string[]] $Arguments = @(),
        [Parameter(Mandatory)] [string] $What
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $FilePath @Arguments } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)" }
}

$PackagingDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root = Split-Path -Parent $PackagingDir
$Dist = Join-Path $Root 'dist'
$AppDir = Join-Path $Dist 'BytesrawERP'

# Prefer the project venv, so the build uses the pinned PySide6 rather than
# whatever python happens to be first on PATH.
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { $Python = 'python' }

# --- version, from the one place that defines it ---------------------------
$previous = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
$Version = & $Python -c "import sys; sys.path.insert(0, r'$Root\src'); from bytesraw_erp.constants import APP_VERSION; print(APP_VERSION)"
$ErrorActionPreference = $previous
if ($LASTEXITCODE -ne 0) { throw 'Could not read APP_VERSION from constants.py' }
$Version = "$Version".Trim()
Write-Host "Building Bytesraw ERP $Version" -ForegroundColor Cyan

if (-not (Test-Path $Dist)) { New-Item -ItemType Directory -Path $Dist | Out-Null }

# --- the app ---------------------------------------------------------------
if (-not $SkipApp) {
    # A stale dist\BytesrawERP is the classic way to ship a file that is no
    # longer produced, so the tree is removed rather than written over.
    if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force }

    Invoke-Native -FilePath $Python -What 'PyInstaller' -Arguments @(
        '-m', 'PyInstaller',
        (Join-Path $PackagingDir 'bytesraw_erp.spec'),
        '--noconfirm',
        '--distpath', $Dist,
        '--workpath', (Join-Path $Root 'build')
    )
}

$Exe = Join-Path $AppDir 'BytesrawERP.exe'
if (-not (Test-Path $Exe)) { throw "No app at $Exe - run without -SkipApp" }

$AppSize = (Get-ChildItem $AppDir -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Host ("  app folder:   {0,6:N0} MB" -f ($AppSize / 1MB))

# --- portable zip ----------------------------------------------------------
$Zip = Join-Path $Dist "BytesrawERP-$Version-windows-x64.zip"
if (-not $SkipZip) {
    if (Test-Path $Zip) { Remove-Item $Zip -Force }
    # Not Compress-Archive: it opens every file with FileShare.None, so it
    # fails outright if anything else holds a handle - and something always
    # does, because Defender scans each DLL as PyInstaller writes it. Measured
    # here as a PermissionDenied on MSVCP140.dll. ZipFile opens FileShare.Read,
    # and is several times faster on a tree this size besides.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $AppDir, $Zip, [System.IO.Compression.CompressionLevel]::Optimal, $true)
    Write-Host ("  portable zip: {0,6:N0} MB" -f ((Get-Item $Zip).Length / 1MB))
}

# --- installer -------------------------------------------------------------
$Setup = Join-Path $Dist "BytesrawERP-$Version-setup.exe"
if (-not $SkipInstaller) {
    $Iscc = $null
    foreach ($candidate in @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )) {
        if (Test-Path $candidate) { $Iscc = $candidate; break }
    }
    if (-not $Iscc) {
        $onPath = Get-Command iscc -ErrorAction SilentlyContinue
        if ($onPath) { $Iscc = $onPath.Source }
    }
    if (-not $Iscc) {
        throw 'Inno Setup 6 not found. Install it (choco install innosetup) or pass -SkipInstaller.'
    }

    $isccArgs = @("/DMyAppVersion=$Version")
    if ($SignCommand) {
        # The .iss turns SignTool on only when this define is present, so an
        # unsigned build needs no signing configuration on the machine at all.
        $isccArgs += "/DSignToolName=bytesraw"
        $isccArgs += "/Sbytesraw=$SignCommand"
    }
    $isccArgs += (Join-Path $PackagingDir 'bytesraw-erp.iss')

    Invoke-Native -FilePath $Iscc -What 'Inno Setup' -Arguments $isccArgs
    Write-Host ("  installer:    {0,6:N0} MB" -f ((Get-Item $Setup).Length / 1MB))
}

# --- checksums -------------------------------------------------------------
# Unsigned binaries especially: a client should be able to check what they got.
if (-not $SkipSums) {
    $lines = @()
    foreach ($artefact in @($Setup, $Zip)) {
        if (Test-Path $artefact) {
            $hash = (Get-FileHash $artefact -Algorithm SHA256).Hash.ToLower()
            $lines += "$hash  $(Split-Path $artefact -Leaf)"
        }
    }
    # Neither Out-File nor Set-Content is right here. -Encoding utf8 writes a
    # BOM under Windows PowerShell 5.1, which lands on the first hash, and both
    # write CRLF - "sha256sum -c" then reads the trailing CR as part of the
    # file name and reports every file missing. Verified both ways.
    $content = ($lines -join "`n") + "`n"
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText((Join-Path $Dist 'SHA256SUMS.txt'), $content, $utf8NoBom)
    $lines | ForEach-Object { Write-Host "  $_" }
}

Write-Host "Done. Artefacts in $Dist" -ForegroundColor Green
