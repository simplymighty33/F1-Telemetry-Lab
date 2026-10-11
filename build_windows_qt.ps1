param(
    [string]$Python = 'python',
    [string]$Output = (Join-Path $PSScriptRoot 'outputs/qt-package')
)
$ErrorActionPreference = 'Stop'
& $Python (Join-Path $PSScriptRoot 'packaging/build_qt.py') --output $Output
if ($LASTEXITCODE -ne 0) { throw 'Qt build failed. See build diagnostics.' }
Write-Host 'RaceTrace Lab 2.0: keep the EXE, _internal and licenses together. Explicit Tk source fallback remains available.'
