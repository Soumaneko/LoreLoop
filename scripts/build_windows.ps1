[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipWebView2Bootstrapper,
    [string]$PythonExecutable = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($env:OS -ne "Windows_NT") {
    throw "LoreLoop Windows artifacts must be built on Windows."
}

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BuildVenv = Join-Path $RepoRoot ".venv-build-windows"
$BuildDir = Join-Path $RepoRoot "build"
$DistDir = Join-Path $RepoRoot "dist"
$ReleaseDir = Join-Path $RepoRoot "release"
$PackageDir = Join-Path $DistDir "LoreLoop"
$MetadataScript = Join-Path $RepoRoot "scripts\generate_build_metadata.py"
$BootstrapperUrl = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
$BootstrapperSha256 = $null

$BasePythonArguments = @()
if ($PythonExecutable.Trim()) {
    $BasePythonCommand = Get-Command $PythonExecutable -ErrorAction SilentlyContinue
    if ($null -eq $BasePythonCommand) {
        throw "The requested Python executable was not found: $PythonExecutable"
    }
    $BasePython = $BasePythonCommand.Source
}
else {
    $PyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($null -eq $PyLauncher) {
        throw "Python Launcher (py.exe) is required. Install 64-bit Python 3.11 first."
    }
    $BasePython = $PyLauncher.Source
    $BasePythonArguments = @("-3.11")
}

$BaseRuntime = & $BasePython @BasePythonArguments -c "import platform, sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{platform.architecture()[0]}')"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to inspect the requested build interpreter."
}
if ($BaseRuntime.Trim() -ne "3.11|64bit") {
    throw "The build interpreter must be 64-bit Python 3.11, got $($BaseRuntime.Trim())."
}

$Version = & $BasePython @BasePythonArguments $MetadataScript --print-version
if ($LASTEXITCODE -ne 0 -or -not $Version.Trim()) {
    throw "Failed to read the application version."
}
$Version = $Version.Trim()

if (Test-Path $BuildVenv) {
    Remove-Item -Recurse -Force $BuildVenv
}
& $BasePython @BasePythonArguments -m venv $BuildVenv
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create the Python 3.11 build environment."
}
$Python = Join-Path $BuildVenv "Scripts\python.exe"

$Runtime = & $Python -c "import platform, sys; print(f'{sys.version_info.major}.{sys.version_info.minor}|{platform.architecture()[0]}')"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to inspect the build interpreter."
}
if ($Runtime.Trim() -ne "3.11|64bit") {
    throw "The virtual environment must use 64-bit Python 3.11."
}

& $Python -m pip install --upgrade "pip==26.1.2"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the pinned pip version."
}
& $Python -m pip install --require-hashes -r (Join-Path $RepoRoot "requirements-build-windows.lock")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the hash-locked Windows build requirements."
}

if (-not $SkipTests) {
    $env:LORELOOP_DATA_DIR = Join-Path $BuildDir "test-user-data"
    $env:PYTHONPATH = $RepoRoot
    Push-Location $RepoRoot
    try {
        & $Python -m pytest -q
        if ($LASTEXITCODE -ne 0) {
            throw "Test suite failed."
        }
    }
    finally {
        Pop-Location
        Remove-Item Env:LORELOOP_DATA_DIR -ErrorAction SilentlyContinue
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
}

foreach ($Path in @($BuildDir, $DistDir, $ReleaseDir)) {
    if (Test-Path $Path) {
        Remove-Item -Recurse -Force $Path
    }
}

$BuildMetadataDir = Join-Path $BuildDir "metadata"
$PyInstallerWorkDir = Join-Path $BuildDir "pyinstaller"
$VersionInfo = Join-Path $BuildMetadataDir "version_info.txt"
$BuildInfo = Join-Path $BuildMetadataDir "build_info.json"
New-Item -ItemType Directory -Force $BuildMetadataDir | Out-Null
& $Python $MetadataScript --version-info $VersionInfo --build-info $BuildInfo
if ($LASTEXITCODE -ne 0) {
    throw "Failed to generate build metadata."
}

Push-Location $RepoRoot
try {
    & $Python -m PyInstaller --clean --noconfirm `
        --workpath $PyInstallerWorkDir `
        --distpath $DistDir `
        "packaging\loreloop.spec"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }
}
finally {
    Pop-Location
}

$Executable = Join-Path $PackageDir "LoreLoop.exe"
$ExecutableConfig = Join-Path $PackageDir "LoreLoop.exe.config"
$PythonRuntimeDll = Join-Path $PackageDir "_internal\pythonnet\runtime\Python.Runtime.dll"
if (-not (Test-Path $Executable)) {
    throw "Expected executable was not produced: $Executable"
}
if (-not (Test-Path $PythonRuntimeDll)) {
    throw "Python.Runtime.dll is missing from the frozen bundle."
}
if (-not (Test-Path (Join-Path $PackageDir "_internal\app\prompts"))) {
    throw "Prompt resources are missing from the frozen bundle."
}
if (-not (Test-Path (Join-Path $PackageDir "_internal\app\player_static"))) {
    throw "Player frontend resources are missing from the frozen bundle."
}
if (-not (Test-Path (Join-Path $PackageDir "_internal\app\runtime\models_dev_snapshot.json"))) {
    throw "models.dev snapshot is missing from the frozen bundle."
}
if (-not (Test-Path (Join-Path $PackageDir "_internal\app\runtime\models_dev_LICENSE.txt"))) {
    throw "models.dev license is missing from the frozen bundle."
}
if (Test-Path (Join-Path $PackageDir "_internal\app\debug_static")) {
    throw "Debug frontend resources must not be present in the desktop bundle."
}
if (-not (Test-Path (Join-Path $PackageDir "THIRD_PARTY_NOTICES.txt"))) {
    throw "Third-party notices are missing from the package."
}

Copy-Item (Join-Path $RepoRoot "LICENSE") $PackageDir
Copy-Item (Join-Path $RepoRoot "packaging\windows\README-Windows.txt") $PackageDir
Copy-Item (Join-Path $RepoRoot "packaging\windows\Install-WebView2.cmd") $PackageDir
Copy-Item (Join-Path $RepoRoot "packaging\windows\LoreLoop.exe.config") $ExecutableConfig
Copy-Item $BuildInfo (Join-Path $PackageDir "build_info.json")
if (-not (Test-Path (Join-Path $PackageDir "build_info.json"))) {
    throw "build_info.json is missing from the package."
}

# Files extracted from an Internet-downloaded ZIP can inherit Mark of the Web.
# Reproduce that state and prove the frozen WinForms/pythonnet backend still
# loads with the application configuration shipped beside LoreLoop.exe.
Set-Content -LiteralPath $PythonRuntimeDll -Stream "Zone.Identifier" -Value @(
    "[ZoneTransfer]"
    "ZoneId=3"
)
try {
    & $Executable --check-winforms-runtime
    if ($LASTEXITCODE -ne 0) {
        throw "Frozen WinForms runtime self-test failed with Mark of the Web."
    }
}
finally {
    Unblock-File -LiteralPath $PythonRuntimeDll
}

if (-not $SkipWebView2Bootstrapper) {
    $Bootstrapper = Join-Path $PackageDir "MicrosoftEdgeWebview2Setup.exe"
    Invoke-WebRequest -UseBasicParsing -Uri $BootstrapperUrl -OutFile $Bootstrapper
    if ((Get-Item $Bootstrapper).Length -lt 100000) {
        throw "Downloaded WebView2 bootstrapper is unexpectedly small."
    }
    $Signature = Get-AuthenticodeSignature -LiteralPath $Bootstrapper
    if ("$($Signature.Status)" -ne "Valid") {
        throw "WebView2 bootstrapper Authenticode signature is not valid: $($Signature.Status)"
    }
    if (
        $null -eq $Signature.SignerCertificate -or
        $Signature.SignerCertificate.Subject -notmatch "(^|,\s*)O=Microsoft Corporation(,|$)"
    ) {
        throw "WebView2 bootstrapper is not signed by Microsoft Corporation."
    }
    $BootstrapperSha256 = (Get-FileHash -Algorithm SHA256 $Bootstrapper).Hash
}

New-Item -ItemType Directory -Force $ReleaseDir | Out-Null
$FreezePath = Join-Path $ReleaseDir "build-requirements-freeze.txt"
$Freeze = & $Python -m pip freeze
if ($LASTEXITCODE -ne 0) {
    throw "Failed to record the resolved build environment."
}
$Freeze | Out-File -Encoding utf8 $FreezePath

$Archive = Join-Path $ReleaseDir "LoreLoop-$Version-win-x64-test.zip"
Compress-Archive -Path $PackageDir -DestinationPath $Archive -CompressionLevel Optimal
$Hash = Get-FileHash -Algorithm SHA256 $Archive
$SizeMiB = [Math]::Round((Get-Item $Archive).Length / 1MB, 2)
$Checksums = @(
    "SHA256  $($Hash.Hash)  $([IO.Path]::GetFileName($Archive))"
)
if ($null -ne $BootstrapperSha256) {
    $Checksums += "SHA256  $BootstrapperSha256  MicrosoftEdgeWebview2Setup.exe"
}
$Checksums | Out-File -Encoding ascii -FilePath (Join-Path $ReleaseDir "SHA256SUMS.txt")

Write-Host ""
Write-Host "Build complete"
Write-Host "Archive: $Archive"
Write-Host "Size:    $SizeMiB MiB"
Write-Host "SHA256:  $($Hash.Hash)"
