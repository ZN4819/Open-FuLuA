$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$BackendPython = Join-Path $Root 'backend\.venv\Scripts\python.exe'

if (-not (Test-Path $BackendPython)) {
    throw 'Backend virtual environment was not found. Create backend/.venv and install requirements.txt first.'
}

Push-Location $Root
try {
    $env:PYTHONPATH = Join-Path $Root 'backend'
    & $BackendPython -m unittest discover -s tests -v
    & $BackendPython -m compileall backend\app

    Push-Location (Join-Path $Root 'frontend')
    try {
        npm run build
        npm audit --audit-level=high
    }
    finally {
        Pop-Location
    }
}
finally {
    Pop-Location
}
