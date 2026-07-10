param(
    [ValidateSet('win-unpacked')]
    [string]$Target = 'win-unpacked'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Root = Split-Path -Parent $PSScriptRoot
$BackendPython = Join-Path $Root 'backend\.venv\Scripts\python.exe'
$ArtifactsRoot = Join-Path $Root 'artifacts\desktop'
$BackendDist = Join-Path $ArtifactsRoot 'backend'
$BackendWork = Join-Path $ArtifactsRoot 'pyinstaller-work'
$BackendSpec = Join-Path $Root 'backend\packaging\fulua_backend.spec'
$DesktopExecutable = Join-Path $ArtifactsRoot 'electron\win-unpacked\FuLuA.exe'

if (-not (Test-Path $BackendPython)) {
    throw 'backend/.venv was not found. Install backend packaging requirements first.'
}

Push-Location $Root
try {
    npm --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }

    & $BackendPython -m PyInstaller --clean --noconfirm --distpath $BackendDist --workpath $BackendWork $BackendSpec
    if ($LASTEXITCODE -ne 0) { throw 'Python backend packaging failed.' }

    npm --prefix desktop run typecheck
    if ($LASTEXITCODE -ne 0) { throw 'Desktop TypeScript typecheck failed.' }

    if ($Target -eq 'win-unpacked') {
        npm --prefix desktop run package:win-unpacked
        if ($LASTEXITCODE -ne 0) { throw 'Electron win-unpacked packaging failed.' }
    }

    if (-not (Test-Path $DesktopExecutable)) {
        throw "Desktop executable was not generated: $DesktopExecutable"
    }

    Write-Host "Desktop build completed: $DesktopExecutable"
}
finally {
    Pop-Location
}
