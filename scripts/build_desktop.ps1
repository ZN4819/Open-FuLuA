param(
    [ValidateSet('win-unpacked', 'nsis')]
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
$ElectronOutput = Join-Path $ArtifactsRoot 'electron'
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
    elseif ($Target -eq 'nsis') {
        npm --prefix desktop run package:nsis
        if ($LASTEXITCODE -ne 0) { throw 'Electron NSIS packaging failed.' }
    }

    if (-not (Test-Path $DesktopExecutable)) {
        throw "Desktop executable was not generated: $DesktopExecutable"
    }

    if ($Target -eq 'nsis') {
        $SetupExecutables = @(Get-ChildItem -LiteralPath $ElectronOutput -File -Filter '*Setup*.exe')
        if ($SetupExecutables.Count -ne 1) {
            throw "Setup executable was not generated exactly once under: $ElectronOutput"
        }
        $LatestMetadata = Join-Path $ElectronOutput 'latest.yml'
        if (-not (Test-Path -LiteralPath $LatestMetadata -PathType Leaf)) {
            throw "Update metadata was not generated: $LatestMetadata"
        }
        $Blockmaps = @(Get-ChildItem -LiteralPath $ElectronOutput -File -Filter '*.blockmap')
        if ($Blockmaps.Count -lt 1) {
            throw "Update blockmap was not generated under: $ElectronOutput"
        }
        & $BackendPython (Join-Path $Root 'scripts\verify_release_manifest.py') $LatestMetadata $ElectronOutput
        if ($LASTEXITCODE -ne 0) { throw 'Update metadata SHA-512 verification failed.' }
        Write-Host "Desktop build completed: $DesktopExecutable"
        Write-Host "NSIS setup completed: $($SetupExecutables[0].FullName)"
    }
    else {
        Write-Host "Desktop build completed: $DesktopExecutable"
    }
}
finally {
    Pop-Location
}
