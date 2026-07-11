$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$BackendPython = Join-Path $Root 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path $BackendPython)) {
    throw 'Backend virtual environment was not found. Create backend/.venv and install requirements.txt first.'
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Command,
        [Parameter(Mandatory = $true)][string]$Description
    )

    & $Command
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode."
    }
}

Push-Location $Root
try {
    $env:PYTHONPATH = Join-Path $Root 'backend'
    Invoke-CheckedCommand -Description 'Backend test suite' -Command { & $BackendPython -m unittest discover -s tests -v }
    Invoke-CheckedCommand -Description 'Backend compile check' -Command { & $BackendPython -m compileall backend\app }

    Push-Location (Join-Path $Root 'frontend')
    try {
        Invoke-CheckedCommand -Description 'Frontend build' -Command { npm run build }
        Invoke-CheckedCommand -Description 'Frontend dependency audit' -Command { npm audit --audit-level=high }
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}
