param(
    [string]$InstallerPath,
    [switch]$BuildIfMissing
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Net.Http

function Resolve-SafeChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$SafeRoot,
        [Parameter(Mandatory = $true)][string]$ChildName
    )

    $root = [System.IO.Path]::GetFullPath($SafeRoot).TrimEnd('\')
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $root $ChildName))
    if (-not $candidate.StartsWith("$root\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理安全根目录之外的路径：$candidate"
    }
    return $candidate
}

function Assert-NoReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "拒绝使用重解析点路径：$($item.FullName)"
    }
}

function Remove-OwnedTree {
    param([Parameter(Mandatory = $true)][string]$Path)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "拒绝清理包含重解析点的临时树：$($item.FullName)"
    }
    if ($item.PSIsContainer) {
        foreach ($child in @(Get-ChildItem -LiteralPath $item.FullName -Force)) {
            Remove-OwnedTree -Path $child.FullName
        }
    }
    Remove-Item -LiteralPath $item.FullName -Force
}

function Remove-SafeTestPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$SafeRoot
    )

    $root = [System.IO.Path]::GetFullPath($SafeRoot).TrimEnd('\')
    $candidate = [System.IO.Path]::GetFullPath($Path)
    if (-not $candidate.StartsWith("$root\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝清理安全根目录之外的路径：$candidate"
    }
    if (Test-Path -LiteralPath $candidate) {
        Assert-NoReparsePoint -Path $root
        Assert-NoReparsePoint -Path $candidate
        Remove-OwnedTree -Path $candidate
    }
}

function Find-SetupExecutable {
    param([Parameter(Mandatory = $true)][string]$OutputDirectory)

    $setups = @(Get-ChildItem -LiteralPath $OutputDirectory -File -Filter '*Setup*.exe')
    if ($setups.Count -ne 1) {
        throw "未找到唯一的 NSIS Setup EXE：$OutputDirectory"
    }
    return $setups[0].FullName
}

function Stop-TestProcessTree {
    param([System.Diagnostics.Process]$Process)

    if ($null -eq $Process) { return }
    $Process.Refresh()
    if ($Process.HasExited) { return }

    & taskkill /pid $Process.Id /t /f | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "无法结束本次 Electron 进程树（PID $($Process.Id)，taskkill 退出码 $LASTEXITCODE）。"
    }
    if (-not $Process.WaitForExit(10000)) {
        throw "无法确认本次 Electron 根进程已退出（PID $($Process.Id)）。"
    }
    $Process.Refresh()
    if (-not $Process.HasExited) {
        throw "本次 Electron 根进程仍在运行（PID $($Process.Id)）。"
    }
}

function Assert-InstalledProgramResources {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    $resourcesRoot = Join-Path $InstallRoot 'resources'
    if (-not (Test-Path -LiteralPath $resourcesRoot -PathType Container)) {
        throw "安装目录缺少程序资源目录：$resourcesRoot"
    }
    $allowedResourcePaths = @(
        (Join-Path $InstallRoot 'resources\app.asar'),
        (Join-Path $InstallRoot 'resources\app-update.yml'),
        (Join-Path $InstallRoot 'resources\frontend'),
        (Join-Path $InstallRoot 'resources\backend'),
        (Join-Path $InstallRoot 'resources\elevate.exe')
    )
    foreach ($entry in @(Get-ChildItem -LiteralPath $resourcesRoot -Force)) {
        if ($allowedResourcePaths -notcontains $entry.FullName) {
            throw "安装资源白名单之外的项目：$($entry.FullName)"
        }
    }

    $forbiddenDirectoryNames = @('storage', 'logs', 'backups', 'backup', 'migration', 'migrations', 'fixture', 'fixtures', 'import', 'imports', 'user')
    $forbiddenFilePatterns = @('*.sqlite', '*.sqlite3', '*.db', '*-wal', '*-shm', '*.docx', '~$*.docx')
    $allowedProgramDocx = @(
        (Join-Path $InstallRoot 'resources\backend\_internal\docx\templates\default.docx'),
        (Join-Path $InstallRoot 'resources\backend\_internal\templates\report\2023-2025.12.08\runtime_template.docx')
    )
    foreach ($entry in @(Get-ChildItem -LiteralPath $InstallRoot -Force -Recurse)) {
        if ($entry.PSIsContainer -and $forbiddenDirectoryNames -contains $entry.Name.ToLowerInvariant()) {
            throw "安装目录包含禁止的用户或测试目录：$($entry.FullName)"
        }
        if (-not $entry.PSIsContainer) {
            if (@($allowedProgramDocx | Where-Object { $entry.FullName -ieq $_ }).Count -gt 0) { continue }
            foreach ($pattern in $forbiddenFilePatterns) {
                if ($entry.Name -like $pattern) {
                    throw "安装目录包含禁止的用户数据或文档：$($entry.FullName)"
                }
            }
        }
    }
}

function Assert-PackagedAsarContents {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    $asarFile = Join-Path $InstallRoot 'resources\app.asar'
    $asarCli = Join-Path $Root 'desktop\node_modules\@electron\asar\bin\asar.js'
    if (-not (Test-Path -LiteralPath $asarFile -PathType Leaf)) {
        throw "安装目录缺少 app.asar：$asarFile"
    }
    if (-not (Test-Path -LiteralPath $asarCli -PathType Leaf)) {
        throw "缺少 asar 验收工具：$asarCli"
    }
    $nodeExecutable = (Get-Command node -ErrorAction Stop).Source
    $asarEntries = @(& $nodeExecutable $asarCli list $asarFile)
    if ($LASTEXITCODE -ne 0) {
        throw "无法列出 app.asar 内容（退出码 $LASTEXITCODE）。"
    }
    $normalizedEntries = @($asarEntries | ForEach-Object { $_.Replace('\', '/').TrimStart('/') })
    foreach ($entry in $normalizedEntries) {
        if ($entry -like '*.test.js' -or $entry -like '*.test.js.map' -or $entry -like '*.map' -or $entry -match '(^|/)(test|tests)(/|$)') {
            throw "app.asar 包含禁止的测试或开发资源：$entry"
        }
    }
    foreach ($requiredModule in @('dist/main.js', 'dist/preload.js', 'dist/runtimeApi.js')) {
        if ($normalizedEntries -notcontains $requiredModule) {
            throw "app.asar 缺少生产运行模块：$requiredModule"
        }
    }
}

function Wait-DesktopHealth {
    param(
        [Parameter(Mandatory = $true)][string]$ExpectedDataRoot,
        [int]$TimeoutSeconds = 45
    )

    $expected = [System.IO.Path]::GetFullPath($ExpectedDataRoot)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $connections = @(Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -ErrorAction SilentlyContinue)
        foreach ($connection in $connections) {
            $baseUri = "http://127.0.0.1:$($connection.LocalPort)"
            try {
                $client = New-Object System.Net.Http.HttpClient
                $client.Timeout = [TimeSpan]::FromSeconds(2)
                try {
                    $healthBytes = $client.GetByteArrayAsync("$baseUri/api/health").GetAwaiter().GetResult()
                    $healthJson = [System.Text.Encoding]::UTF8.GetString($healthBytes)
                    $health = $healthJson | ConvertFrom-Json
                }
                finally {
                    $client.Dispose()
                }
                if ($health.status -eq 'ok' -and [System.IO.Path]::GetFullPath([string]$health.data_root) -eq $expected) {
                    return [pscustomobject]@{ BaseUri = $baseUri; Health = $health }
                }
            }
            catch {
                # 同一台机器上的其他回环服务不是本次验收对象。
            }
        }
        Start-Sleep -Milliseconds 300
    }
    throw "未在 $TimeoutSeconds 秒内发现使用临时数据目录的桌面侧车。"
}

function Assert-DesktopPageAndCreateProject {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUri,
        [Parameter(Mandatory = $true)][string]$ProjectName,
        [Parameter(Mandatory = $true)][string]$FullProjectName,
        [Parameter(Mandatory = $true)][string]$UpgradeProjectName
    )

    $page = Invoke-WebRequest -Uri "${BaseUri}/" -UseBasicParsing -TimeoutSec 10
    if ($page.StatusCode -ne 200) { throw "根页面返回异常状态：$($page.StatusCode)" }
    $created = Invoke-RestMethod -Method Post -Uri "${BaseUri}/api/projects" -ContentType 'application/json; charset=utf-8' -Body (@{ name = $ProjectName } | ConvertTo-Json -Compress) -TimeoutSec 10
    if ($created.name -ne $ProjectName) { throw '测试项目创建后名称不一致。' }
    if ($created.project_type -ne 'appendix_a' -or $created.workflow_status -ne 'draft') {
        throw '兼容创建未得到 appendix_a/draft 项目。'
    }
    try { [void][guid]::Parse([string]$created.project_uuid) } catch { throw '兼容创建项目缺少合法 UUID。' }
    if ($null -ne $created.template_package_id -or $null -ne $created.template_asset_set_hash) {
        throw '附录 A 项目错误绑定了完整报告模板。'
    }

    $template = @{
        template_package_id = 'report-2023-2025.12.08'
        template_edition = '2023'
        template_revision = '2025-12-08'
    }
    $fullBody = @{ name = $FullProjectName; project_type = 'full_report' } + $template
    $full = Invoke-RestMethod -Method Post -Uri "${BaseUri}/api/projects" -ContentType 'application/json; charset=utf-8' -Body ($fullBody | ConvertTo-Json -Compress) -TimeoutSec 20
    if ($full.project_type -ne 'full_report' -or $full.workflow_status -ne 'draft') {
        throw '完整报告创建未得到 full_report/draft 项目。'
    }
    if ($full.template_package_id -ne $template.template_package_id -or $full.template_edition -ne $template.template_edition -or $full.template_revision -ne $template.template_revision) {
        throw '完整报告项目未绑定固定 R0 母版身份。'
    }
    if ([string]$full.template_asset_set_hash -notmatch '^[0-9a-f]{64}$') {
        throw '完整报告项目缺少合法模板资产集指纹。'
    }

    $idempotencyKey = [guid]::NewGuid().ToString()
    $upgradeBody = @{ name = $UpgradeProjectName; idempotency_key = $idempotencyKey } + $template
    $upgradeUri = "${BaseUri}/api/projects/$($created.project_uuid)/upgrade-copy"
    $upgraded = Invoke-RestMethod -Method Post -Uri $upgradeUri -ContentType 'application/json; charset=utf-8' -Body ($upgradeBody | ConvertTo-Json -Compress) -TimeoutSec 30
    $repeated = Invoke-RestMethod -Method Post -Uri $upgradeUri -ContentType 'application/json; charset=utf-8' -Body ($upgradeBody | ConvertTo-Json -Compress) -TimeoutSec 30
    if ($upgraded.project_type -ne 'full_report' -or $upgraded.source_project_uuid -ne $created.project_uuid) {
        throw '复制升级项目的类型或来源标识不正确。'
    }
    if ($upgraded.template_asset_set_hash -ne $full.template_asset_set_hash) {
        throw '直接创建与复制升级项目的冻结模板指纹不一致。'
    }
    if ($repeated.id -ne $upgraded.id -or $repeated.project_uuid -ne $upgraded.project_uuid) {
        throw '复制升级的同幂等键重试生成了不同项目。'
    }
    return [pscustomobject]@{ Appendix = $created; Full = $full; Upgraded = $upgraded }
}

function New-SchemaThreeFixture {
    param(
        [Parameter(Mandatory = $true)][string]$DataRoot,
        [Parameter(Mandatory = $true)][string]$ProjectName
    )

    $python = Join-Path $Root 'backend\.venv\Scripts\python.exe'
    $fixtureScript = Join-Path $Root 'scripts\create_schema3_fixture.py'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "缺少 schema 3 fixture Python：$python"
    }
    & $python $fixtureScript --data-root $DataRoot --project-name $ProjectName
    if ($LASTEXITCODE -ne 0) {
        throw "创建 schema 3 安装验收数据失败（退出码 $LASTEXITCODE）。"
    }
}

function Invoke-InstalledOfflineRecovery {
    param(
        [Parameter(Mandatory = $true)][string]$BackendExecutable,
        [Parameter(Mandatory = $true)][string]$DataRoot,
        [Parameter(Mandatory = $true)][ValidateSet('integrity', 'list', 'restore', 'prepare-schema-upgrade')][string]$Action,
        [string]$BackupId
    )

    $arguments = @('--data-root', $DataRoot, '--offline-recovery', $Action)
    if (-not [string]::IsNullOrWhiteSpace($BackupId)) {
        $arguments += @('--backup-id', $BackupId)
    }
    $lines = @(& $BackendExecutable @arguments)
    if ($LASTEXITCODE -ne 0) {
        throw "安装版离线操作 $Action 失败（退出码 $LASTEXITCODE）。"
    }
    if ($lines.Count -lt 1) {
        throw "安装版离线操作 $Action 未返回协议事件。"
    }
    return ($lines[-1] | ConvertFrom-Json)
}

function Assert-ProjectRetained {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUri,
        [Parameter(Mandatory = $true)]$CreatedProjects,
        [Parameter(Mandatory = $true)][string]$LegacyProjectName
    )

    $projects = Invoke-RestMethod -Uri "${BaseUri}/api/projects" -TimeoutSec 10
    foreach ($expected in @($CreatedProjects.Appendix, $CreatedProjects.Full, $CreatedProjects.Upgraded)) {
        $matchingProjects = @($projects | Where-Object { $_.project_uuid -eq $expected.project_uuid })
        if ($matchingProjects.Count -ne 1) {
            throw '重新安装后未按 UUID 唯一找到此前创建的 R1 测试项目。'
        }
        $actual = $matchingProjects[0]
        foreach ($field in @('project_type', 'workflow_status', 'template_package_id', 'template_edition', 'template_revision', 'template_asset_set_hash', 'source_project_uuid', 'created_by_operation')) {
            if ($actual.$field -ne $expected.$field) {
                throw "重新安装后项目字段 $field 发生漂移。"
            }
        }
    }
    $legacy = @($projects | Where-Object { $_.name -eq $LegacyProjectName })
    if ($legacy.Count -ne 1 -or $legacy[0].project_type -ne 'appendix_a' -or $legacy[0].workflow_status -ne 'draft') {
        throw 'schema 3 旧项目未在重新安装后保持唯一且兼容。'
    }
}

$Root = Split-Path -Parent $PSScriptRoot
$ArtifactsDirectory = Join-Path $Root 'artifacts\desktop\electron'
if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
    if (-not $BuildIfMissing) {
        throw '请通过 -InstallerPath 明确提供 NSIS Setup EXE，或使用 -BuildIfMissing 受控构建。'
    }
    & (Join-Path $PSScriptRoot 'build_desktop.ps1') -Target nsis
    if ($LASTEXITCODE -ne 0) { throw '受控 NSIS 构建失败。' }
    $InstallerPath = Find-SetupExecutable -OutputDirectory $ArtifactsDirectory
}

$installer = [System.IO.Path]::GetFullPath($InstallerPath)
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) {
    throw "安装包不存在：$installer"
}

$TemporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
Assert-NoReparsePoint -Path $TemporaryRoot
$SafetyRoot = Resolve-SafeChildPath -SafeRoot $TemporaryRoot -ChildName "fulua-cd6-install-tests-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $SafetyRoot -ErrorAction Stop | Out-Null
Assert-NoReparsePoint -Path $SafetyRoot
$RunRoot = Resolve-SafeChildPath -SafeRoot $SafetyRoot -ChildName ([guid]::NewGuid().ToString('N'))
$InstallRoot = Resolve-SafeChildPath -SafeRoot $RunRoot -ChildName 'install'
$TemporaryLocalAppData = Resolve-SafeChildPath -SafeRoot $RunRoot -ChildName 'localappdata'
$ExpectedDataRoot = Resolve-SafeChildPath -SafeRoot $TemporaryLocalAppData -ChildName '附录A编写工具'
$RecoveryDataRoot = Resolve-SafeChildPath -SafeRoot $RunRoot -ChildName 'schema-recovery'
$ProjectName = "CD6-install-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$LegacyProjectName = "$ProjectName-schema3"
$FullProjectName = "$ProjectName-full"
$UpgradeProjectName = "$ProjectName-upgrade"
$OriginalLocalAppData = $env:LOCALAPPDATA
$FirstLaunch = $null
$SecondLaunch = $null
$ProcessTerminationFailed = $false
$Result = [ordered]@{
    status = 'failed'
    installer = $installer
    installation_directory = $InstallRoot
    temporary_data_root = $ExpectedDataRoot
    project_name = $ProjectName
    legacy_project_name = $LegacyProjectName
    full_project_name = $FullProjectName
    upgrade_project_name = $UpgradeProjectName
    health_checked = $false
    root_page_checked = $false
    project_created = $false
    schema3_upgrade = $false
    pre_upgrade_backup = $false
    故障恢复 = $false
    数据保留 = $false
    重新安装 = $false
}

try {
    New-Item -ItemType Directory -Force -Path $RunRoot, $TemporaryLocalAppData | Out-Null
    $installation = Start-Process -FilePath $installer -ArgumentList @('/S', "/D=$InstallRoot") -Wait -PassThru
    if ($installation.ExitCode -ne 0) { throw "静默按用户安装失败（退出码 $($installation.ExitCode)）。" }

    $installedExecutable = Join-Path $InstallRoot 'FuLuA.exe'
    if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) {
        throw "安装后缺少客户端可执行文件：$installedExecutable"
    }
    Assert-InstalledProgramResources -InstallRoot $InstallRoot
    Assert-PackagedAsarContents -InstallRoot $InstallRoot

    $installedBackend = Join-Path $InstallRoot 'resources\backend\fulua-backend.exe'
    if (-not (Test-Path -LiteralPath $installedBackend -PathType Leaf)) {
        throw "安装目录缺少后端侧车：$installedBackend"
    }
    New-SchemaThreeFixture -DataRoot $RecoveryDataRoot -ProjectName "$LegacyProjectName-recovery"
    $preparedRecovery = Invoke-InstalledOfflineRecovery -BackendExecutable $installedBackend -DataRoot $RecoveryDataRoot -Action 'prepare-schema-upgrade'
    if ($preparedRecovery.event -ne 'FULUA_OFFLINE_SCHEMA_UPGRADE' -or $preparedRecovery.prepared -ne $true -or $preparedRecovery.source_schema -ne '3') {
        throw '安装版未为 schema 3 故障恢复场景创建升级前备份。'
    }
    [System.IO.File]::WriteAllBytes((Join-Path $RecoveryDataRoot 'data\app.db'), [byte[]](1, 2, 3, 4))
    $restoredRecovery = Invoke-InstalledOfflineRecovery -BackendExecutable $installedBackend -DataRoot $RecoveryDataRoot -Action 'restore' -BackupId ([string]$preparedRecovery.backup_id)
    if ($restoredRecovery.event -ne 'FULUA_OFFLINE_RESTORE' -or $restoredRecovery.restored -ne $true) {
        throw '安装版未能从 schema 迁移前备份恢复损坏现场。'
    }
    $recoveryIntegrity = Invoke-InstalledOfflineRecovery -BackendExecutable $installedBackend -DataRoot $RecoveryDataRoot -Action 'integrity'
    if ($recoveryIntegrity.integrity -ne 'ok' -or $recoveryIntegrity.schema_version -ne '3') {
        throw 'schema 迁移前备份恢复后未保留 schema 3 完整数据。'
    }
    $Result.故障恢复 = $true

    $env:LOCALAPPDATA = $TemporaryLocalAppData
    New-SchemaThreeFixture -DataRoot $ExpectedDataRoot -ProjectName $LegacyProjectName
    $FirstLaunch = Start-Process -FilePath $installedExecutable -WindowStyle Hidden -PassThru
    $firstServer = Wait-DesktopHealth -ExpectedDataRoot $ExpectedDataRoot
    $Result.health_checked = $true
    if ([string]$firstServer.Health.schema_version -ne '6') {
        throw "安装版后端 schema 版本不是 6：$($firstServer.Health.schema_version)"
    }
    $legacyProjects = @(Invoke-RestMethod -Uri "$($firstServer.BaseUri)/api/projects" -TimeoutSec 10 | Where-Object { $_.name -eq $LegacyProjectName })
    if ($legacyProjects.Count -ne 1 -or $legacyProjects[0].project_type -ne 'appendix_a') {
        throw '首次启动未迁移并保留 schema 3 旧项目。'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ExpectedDataRoot 'storage\schema3-retained.txt') -PathType Leaf)) {
        throw '首次启动迁移后未保留 schema 3 storage 文件。'
    }
    $preUpgradeBackups = @(Get-ChildItem -LiteralPath (Join-Path $ExpectedDataRoot 'backups') -Directory -Filter 'pre_upgrade-*')
    if ($preUpgradeBackups.Count -lt 1) {
        throw '首次启动 schema 迁移前未生成 pre_upgrade 备份。'
    }
    $backupIntegrity = Invoke-InstalledOfflineRecovery -BackendExecutable $installedBackend -DataRoot $preUpgradeBackups[0].FullName -Action 'integrity'
    if ($backupIntegrity.integrity -ne 'ok' -or $backupIntegrity.schema_version -ne '3') {
        throw '首次启动生成的 pre_upgrade 备份不是完整 schema 3 快照。'
    }
    if (Test-Path -LiteralPath (Join-Path $ExpectedDataRoot 'recovery\pending-upgrade.json')) {
        throw 'schema 迁移成功后仍残留待升级恢复标记。'
    }
    $Result.schema3_upgrade = $true
    $Result.pre_upgrade_backup = $true
    $CreatedProjects = Assert-DesktopPageAndCreateProject -BaseUri $firstServer.BaseUri -ProjectName $ProjectName -FullProjectName $FullProjectName -UpgradeProjectName $UpgradeProjectName
    $Result.root_page_checked = $true
    $Result.project_created = $true
    try {
        Stop-TestProcessTree $FirstLaunch
        $FirstLaunch = $null
    }
    catch {
        $ProcessTerminationFailed = $true
        throw
    }

    $uninstallers = @(Get-ChildItem -LiteralPath $InstallRoot -File -Filter '*uninstall*.exe')
    if ($uninstallers.Count -ne 1) {
        throw '未在安装目录中找到 uninstall.exe（或带产品名称的卸载程序）。'
    }
    $uninstaller = $uninstallers[0].FullName
    $uninstallation = Start-Process -FilePath $uninstaller -ArgumentList @('/S') -Wait -PassThru
    if ($uninstallation.ExitCode -ne 0) { throw "静默卸载失败（退出码 $($uninstallation.ExitCode)）。" }
    if (-not (Test-Path -LiteralPath $ExpectedDataRoot -PathType Container)) {
        throw '卸载后未保留本次临时用户数据目录。'
    }
    $Result.数据保留 = $true

    $reinstallation = Start-Process -FilePath $installer -ArgumentList @('/S', "/D=$InstallRoot") -Wait -PassThru
    if ($reinstallation.ExitCode -ne 0) { throw "静默重新安装失败（退出码 $($reinstallation.ExitCode)）。" }
    if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) {
        throw '重新安装后缺少客户端可执行文件。'
    }
    $SecondLaunch = Start-Process -FilePath $installedExecutable -WindowStyle Hidden -PassThru
    $secondServer = Wait-DesktopHealth -ExpectedDataRoot $ExpectedDataRoot
    Assert-ProjectRetained -BaseUri $secondServer.BaseUri -CreatedProjects $CreatedProjects -LegacyProjectName $LegacyProjectName
    $Result.重新安装 = $true
    $Result.status = 'passed'
}
finally {
    $cleanupAllowed = $false
    try {
        if (-not $ProcessTerminationFailed) {
            try {
                Stop-TestProcessTree $SecondLaunch
                Stop-TestProcessTree $FirstLaunch
                $cleanupAllowed = $true
            }
            catch {
                $ProcessTerminationFailed = $true
                throw
            }
        }
    }
    finally {
        $env:LOCALAPPDATA = $OriginalLocalAppData
        if ($cleanupAllowed) {
            Remove-SafeTestPath -Path $RunRoot -SafeRoot $SafetyRoot
            Remove-SafeTestPath -Path $SafetyRoot -SafeRoot $TemporaryRoot
        }
        [pscustomobject]$Result | ConvertTo-Json -Depth 4
    }
}
