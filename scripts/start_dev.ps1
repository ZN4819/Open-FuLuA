param(
    [int] $FrontendPort = 5174,
    [int] $BackendPort = 8000,
    [switch] $RequireNew,
    [string] $ProcessInfoPath
)

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $Root 'backend'
$FrontendDir = Join-Path $Root 'frontend'
$StorageDir = Join-Path $Root 'storage'
$BackendPython = Join-Path $BackendDir '.venv\Scripts\python.exe'
$BackendOut = Join-Path $StorageDir 'backend-dev.out.log'
$BackendErr = Join-Path $StorageDir 'backend-dev.err.log'
$FrontendOut = Join-Path $StorageDir 'frontend-dev.out.log'
$FrontendErr = Join-Path $StorageDir 'frontend-dev.err.log'

function Normalize-ProcessPathEnvironment {
    $environment = [Environment]::GetEnvironmentVariables('Process')
    if ($environment.Contains('Path') -and $environment.Contains('PATH')) {
        $pathValue = [Environment]::GetEnvironmentVariable('Path', 'Process')
        if ([string]::IsNullOrWhiteSpace($pathValue)) {
            $pathValue = [Environment]::GetEnvironmentVariable('PATH', 'Process')
        }

        [Environment]::SetEnvironmentVariable('PATH', $null, 'Process')
        [Environment]::SetEnvironmentVariable('Path', $pathValue, 'Process')
    }
}

function Test-HttpReady {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Url
    )

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    }
    catch {
        return $false
    }
}

function Start-Backend {
    if (-not (Test-Path $BackendPython)) {
        throw 'Backend virtual environment was not found. Create backend/.venv and install requirements.txt first.'
    }

    if (Test-HttpReady -Url "http://127.0.0.1:$BackendPort/api/health") {
        if ($RequireNew) { throw "Backend port $BackendPort is already in use; smoke tests must not reuse another service." }
        Write-Host "Backend already responds on http://127.0.0.1:$BackendPort."
        return $null
    }

    $process = Start-Process `
        -FilePath $BackendPython `
        -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $BackendPort) `
        -WorkingDirectory $BackendDir `
        -RedirectStandardOutput $BackendOut `
        -RedirectStandardError $BackendErr `
        -WindowStyle Hidden `
        -PassThru

    Write-Host "Backend started on http://127.0.0.1:$BackendPort (PID: $($process.Id))."
    return $process
}

function Start-Frontend {
    $npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($null -eq $npm) {
        throw 'npm.cmd was not found. Install Node.js and npm first.'
    }

    if (Test-HttpReady -Url "http://127.0.0.1:$FrontendPort/") {
        if ($RequireNew) { throw "Frontend port $FrontendPort is already in use; smoke tests must not reuse another service." }
        Write-Host "Frontend already responds on http://127.0.0.1:$FrontendPort."
        return $null
    }

    $process = Start-Process `
        -FilePath $npm.Source `
        -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1', '--port', $FrontendPort) `
        -WorkingDirectory $FrontendDir `
        -RedirectStandardOutput $FrontendOut `
        -RedirectStandardError $FrontendErr `
        -WindowStyle Hidden `
        -PassThru

    Write-Host "Frontend started on http://127.0.0.1:$FrontendPort (PID: $($process.Id))."
    return $process
}

function Write-ProcessInfo {
    param([System.Diagnostics.Process]$Backend, [System.Diagnostics.Process]$Frontend)
    if ([string]::IsNullOrWhiteSpace($ProcessInfoPath)) { return }
    $items = @()
    foreach ($entry in @(@{ kind = 'backend'; process = $Backend }, @{ kind = 'frontend'; process = $Frontend })) {
        if ($null -ne $entry.process) { $items += [ordered]@{ kind = $entry.kind; pid = $entry.process.Id; start_ticks = $entry.process.StartTime.ToUniversalTime().Ticks } }
    }
    [System.IO.File]::WriteAllText([System.IO.Path]::GetFullPath($ProcessInfoPath), (@{ processes = $items } | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))
}

New-Item -ItemType Directory -Force -Path $StorageDir | Out-Null
Normalize-ProcessPathEnvironment

$backendProcess = Start-Backend
Write-ProcessInfo -Backend $backendProcess -Frontend $null
$frontendProcess = Start-Frontend
Write-ProcessInfo -Backend $backendProcess -Frontend $frontendProcess

Write-Host ''
Write-Host 'Local development services are ready:'
Write-Host "  Frontend: http://127.0.0.1:$FrontendPort"
Write-Host "  Backend:  http://127.0.0.1:$BackendPort"
Write-Host ''
Write-Host 'Logs:'
Write-Host "  Backend stdout:  $BackendOut"
Write-Host "  Backend stderr:  $BackendErr"
Write-Host "  Frontend stdout: $FrontendOut"
Write-Host "  Frontend stderr: $FrontendErr"
