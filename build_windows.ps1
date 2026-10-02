[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $ProjectRoot
try {
    if (-not $SkipInstall) {
        python -m pip install -e ".[build]"
        if ($LASTEXITCODE -ne 0) { throw "Build dependency installation failed." }
    }
    python -m PyInstaller --noconfirm --clean ".\packaging\F1TelemetryCollector.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
    $taskVersion = python -c "from collector import DISPLAY_VERSION; print(DISPLAY_VERSION)"
    if ($LASTEXITCODE -ne 0) { throw "Version detection failed." }
    Write-Host "Build complete: $ProjectRoot\dist\F1TelemetryLab-$taskVersion\F1TelemetryLab.exe"
} finally {
    Pop-Location
}
