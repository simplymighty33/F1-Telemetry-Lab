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
    Write-Host "Build complete: $ProjectRoot\dist\F1TelemetryLab-1.0\F1TelemetryLab.exe"
} finally {
    Pop-Location
}
