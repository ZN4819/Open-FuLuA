param([int]$TimeoutSeconds = 45)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot

function Get-FreeTcpPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    try { $listener.Start(); return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port }
    finally { $listener.Stop() }
}

function Stop-OwnedProcesses {
    param([string]$InfoPath)
    if (-not (Test-Path -LiteralPath $InfoPath -PathType Leaf)) { return }

    $info = Get-Content -LiteralPath $InfoPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($entry in @($info.processes)) {
        $processId = [int]$entry.pid
        $expectedStartTicks = [long]$entry.start_ticks
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -eq $process) { continue }

        $actualStartTicks = $process.StartTime.ToUniversalTime().Ticks
        if ($actualStartTicks -ne $expectedStartTicks) {
            throw "拒绝清理 PID $processId：进程启动时间与本轮记录不匹配。"
        }

        & taskkill /pid $processId /t /f | Out-Null
        if ($LASTEXITCODE -ne 0 -and $null -ne (Get-Process -Id $processId -ErrorAction SilentlyContinue)) {
            throw "无法清理本轮开发冒烟进程树：$processId"
        }
    }
}

$frontendPort = Get-FreeTcpPort
do { $backendPort = Get-FreeTcpPort } while ($backendPort -eq $frontendPort)
$processInfoPath = Join-Path ([System.IO.Path]::GetTempPath()) ("fulua-dev-smoke-{0}.json" -f [guid]::NewGuid().ToString('N'))
$result = [ordered]@{ status = 'failed'; frontend_status = 0; backend_status = ''; runtime_mode = ''; frontend_port = $frontendPort; backend_port = $backendPort }
try {
    & (Join-Path $PSScriptRoot 'start_dev.ps1') -FrontendPort $frontendPort -BackendPort $backendPort -RequireNew -ProcessInfoPath $processInfoPath
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $front = $null; $health = $null
    while ((Get-Date) -lt $deadline -and ($null -eq $front -or $null -eq $health)) {
        try { $front = Invoke-WebRequest -Uri "http://127.0.0.1:$frontendPort/" -UseBasicParsing -TimeoutSec 2 } catch {}
        try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:$backendPort/api/health" -TimeoutSec 2 } catch {}
        if ($null -eq $front -or $null -eq $health) { Start-Sleep -Milliseconds 300 }
    }
    if ($null -eq $front -or $front.StatusCode -ne 200) { throw '开发前端未就绪。' }
    if ($null -eq $health -or $health.status -ne 'ok' -or $health.runtime_mode -ne 'development') { throw '开发后端健康检查未通过。' }
    $result.frontend_status = [int]$front.StatusCode
    $result.backend_status = [string]$health.status
    $result.runtime_mode = [string]$health.runtime_mode
    $result.status = 'passed'
}
finally {
    try {
        Stop-OwnedProcesses -InfoPath $processInfoPath
    }
    finally {
        Remove-Item -LiteralPath $processInfoPath -Force -ErrorAction SilentlyContinue
        [pscustomobject]$result | ConvertTo-Json -Compress
    }
}
