param(
    [string]$InstallerPath,
    [switch]$BuildIfMissing
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.Net.Http
Add-Type -AssemblyName System.Drawing

function Resolve-SafeChildPath {
    param(
        [Parameter(Mandatory = $true)][string]$SafeRoot,
        [Parameter(Mandatory = $true)][string]$ChildName
    )

    $root = [System.IO.Path]::GetFullPath($SafeRoot).TrimEnd('\')
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $root $ChildName))
    if (-not $candidate.StartsWith("$root\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝使用安全根目录之外的路径：$candidate"
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
        throw "无法结束本次进程树（PID $($Process.Id)，taskkill 退出码 $LASTEXITCODE）。"
    }
    if (-not $Process.WaitForExit(10000)) {
        throw "无法确认本次根进程已退出（PID $($Process.Id)）。"
    }
    $Process.Refresh()
    if (-not $Process.HasExited) {
        throw "本次根进程仍在运行（PID $($Process.Id)）。"
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
    $forbiddenFilePatterns = @('*.sqlite', '*.sqlite3', '*.db', '*-wal', '*-shm', '*.docx', '~$*.docx', '*.log')
    $allowedProgramDocx = Join-Path $InstallRoot 'resources\backend\_internal\docx\templates\default.docx'
    foreach ($entry in @(Get-ChildItem -LiteralPath $InstallRoot -Force -Recurse)) {
        if ($entry.PSIsContainer -and $forbiddenDirectoryNames -contains $entry.Name.ToLowerInvariant()) {
            throw "安装目录包含禁止的用户或测试目录：$($entry.FullName)"
        }
        if (-not $entry.PSIsContainer) {
            if ($entry.FullName -ieq $allowedProgramDocx) { continue }
            foreach ($pattern in $forbiddenFilePatterns) {
                if ($entry.Name -like $pattern) {
                    throw "安装目录包含禁止的用户数据、日志或文档：$($entry.FullName)"
                }
            }
        }
    }
}

function Assert-PackagedAsarContents {
    param([Parameter(Mandatory = $true)][string]$InstallRoot)

    $asarFile = Join-Path $InstallRoot 'resources\app.asar'
    $asarCli = Join-Path $Root 'desktop\node_modules\@electron\asar\bin\asar.js'
    if (-not (Test-Path -LiteralPath $asarFile -PathType Leaf)) { throw "安装目录缺少 app.asar：$asarFile" }
    if (-not (Test-Path -LiteralPath $asarCli -PathType Leaf)) { throw "缺少 asar 验收工具：$asarCli" }
    $nodeExecutable = (Get-Command node -ErrorAction Stop).Source
    $asarEntries = @(& $nodeExecutable $asarCli list $asarFile)
    if ($LASTEXITCODE -ne 0) { throw "无法列出 app.asar 内容（退出码 $LASTEXITCODE）。" }
    $normalizedEntries = @($asarEntries | ForEach-Object { $_.Replace('\', '/').TrimStart('/') })
    foreach ($entry in $normalizedEntries) {
        if ($entry -like '*.test.js' -or $entry -like '*.test.js.map' -or $entry -like '*.map' -or $entry -match '(^|/)(test|tests)(/|$)') {
            throw "app.asar 包含禁止的测试或开发资源：$entry"
        }
    }
    foreach ($requiredModule in @('dist/main.js', 'dist/preload.js', 'dist/runtimeApi.js')) {
        if ($normalizedEntries -notcontains $requiredModule) { throw "app.asar 缺少生产运行模块：$requiredModule" }
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
        foreach ($connection in @(Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -ErrorAction SilentlyContinue)) {
            $baseUri = "http://127.0.0.1:$($connection.LocalPort)"
            try {
                $client = New-Object System.Net.Http.HttpClient
                $client.Timeout = [TimeSpan]::FromSeconds(2)
                try { $health = ($client.GetStringAsync("$baseUri/api/health").GetAwaiter().GetResult() | ConvertFrom-Json) }
                finally { $client.Dispose() }
                if ($health.status -eq 'ok' -and [System.IO.Path]::GetFullPath([string]$health.data_root) -eq $expected) {
                    return [pscustomobject]@{ BaseUri = $baseUri; Health = $health }
                }
            }
            catch {
                # 其他回环服务不是本轮隔离数据根对应的侧车。
            }
        }
        Start-Sleep -Milliseconds 300
    }
    throw "未在 $TimeoutSeconds 秒内发现使用预期数据目录的桌面侧车。"
}

function Invoke-JsonRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Uri,
        [object]$Body,
        [hashtable]$Headers = @{}
    )

    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromSeconds(60)
    try {
        foreach ($key in $Headers.Keys) { $client.DefaultRequestHeaders.Add($key, [string]$Headers[$key]) }
        $content = $null
        if ($null -ne $Body) {
            $json = $Body | ConvertTo-Json -Depth 20 -Compress
            $content = [System.Net.Http.StringContent]::new($json, [System.Text.Encoding]::UTF8, 'application/json')
        }
        $response = switch ($Method.ToUpperInvariant()) {
            'GET' { $client.GetAsync($Uri).GetAwaiter().GetResult() }
            'POST' { $client.PostAsync($Uri, $content).GetAwaiter().GetResult() }
            'PUT' { $client.PutAsync($Uri, $content).GetAwaiter().GetResult() }
            default { throw "不支持的 JSON 请求方法：$Method" }
        }
        $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) { throw "请求失败 $Method $Uri：HTTP $([int]$response.StatusCode) $text" }
        if ([string]::IsNullOrWhiteSpace($text)) { return $null }
        return $text | ConvertFrom-Json
    }
    finally { $client.Dispose() }
}

function Send-MultipartFile {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string]$FormFileName,
        [hashtable]$Fields = @{}
    )

    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromMinutes(2)
    $multipart = [System.Net.Http.MultipartFormDataContent]::new()
    try {
        foreach ($key in $Fields.Keys) {
            $multipart.Add([System.Net.Http.StringContent]::new([string]$Fields[$key], [System.Text.Encoding]::UTF8), $key)
        }
        $bytes = [System.IO.File]::ReadAllBytes($FilePath)
        $fileContent = [System.Net.Http.ByteArrayContent]::new($bytes)
        $mediaType = switch ([System.IO.Path]::GetExtension($FilePath).ToLowerInvariant()) {
            '.png' { 'image/png' }
            '.jpg' { 'image/jpeg' }
            '.jpeg' { 'image/jpeg' }
            '.docx' { 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' }
            default { 'application/octet-stream' }
        }
        $fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::new($mediaType)
        $multipart.Add($fileContent, $FormFileName, [System.IO.Path]::GetFileName($FilePath))
        $response = $client.PostAsync($Uri, $multipart).GetAwaiter().GetResult()
        $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) { throw "文件请求失败 $Uri：HTTP $([int]$response.StatusCode) $text" }
        return $text | ConvertFrom-Json
    }
    finally {
        $multipart.Dispose()
        $client.Dispose()
    }
}

function Export-ProjectDocx {
    param([string]$BaseUri, [int]$ProjectId, [string]$Mode, [string]$Destination)
    $client = New-Object System.Net.Http.HttpClient
    $client.Timeout = [TimeSpan]::FromMinutes(3)
    try {
        $uri = "$BaseUri/api/projects/$ProjectId/exports/docx?mode=$Mode"
        $response = $client.PostAsync($uri, $null).GetAwaiter().GetResult()
        if (-not $response.IsSuccessStatusCode) {
            $text = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            throw "导出 $Mode DOCX 失败：HTTP $([int]$response.StatusCode) $text"
        }
        [System.IO.File]::WriteAllBytes($Destination, $response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult())
    }
    finally { $client.Dispose() }
    if ((Get-Item -LiteralPath $Destination).Length -lt 1024) { throw "$Mode DOCX 产物异常小。" }
}

function New-AcceptanceImage {
    param([Parameter(Mandatory = $true)][string]$Path)
    $bitmap = [System.Drawing.Bitmap]::new(640, 360)
    try {
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try { $graphics.Clear([System.Drawing.Color]::FromArgb(232, 242, 252)) }
        finally { $graphics.Dispose() }
        $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally { $bitmap.Dispose() }
}

function Assert-ProjectRetained {
    param([string]$BaseUri, [string[]]$ProjectNames)
    $projects = @(Invoke-JsonRequest -Method GET -Uri "$BaseUri/api/projects")
    foreach ($projectName in $ProjectNames) {
        if (@($projects | Where-Object { $_.name -eq $projectName }).Count -ne 1) {
            throw "未找到唯一的预期项目：$projectName"
        }
    }
}

function Start-InstalledClient {
    param([string]$Executable, [string]$LocalAppData)
    $env:LOCALAPPDATA = $LocalAppData
    return Start-Process -FilePath $Executable -PassThru
}

function Start-PackagedSidecar {
    param(
        [string]$Executable,
        [string]$DataRoot,
        [string]$WebDist,
        [string]$SessionToken,
        [string]$OutputPath,
        [string]$ErrorPath
    )
    return Start-Process -FilePath $Executable -ArgumentList @('--data-root', $DataRoot, '--web-dist', $WebDist, '--session-token', $SessionToken) -RedirectStandardOutput $OutputPath -RedirectStandardError $ErrorPath -PassThru -WindowStyle Hidden
}

function Get-SignatureEvidence {
    param([string]$Path)
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    return [ordered]@{ path = $Path; status = [string]$signature.Status; subject = if ($signature.SignerCertificate) { [string]$signature.SignerCertificate.Subject } else { '' } }
}

$Root = Split-Path -Parent $PSScriptRoot
$ArtifactsDirectory = Join-Path $Root 'artifacts\desktop\electron'
if ([string]::IsNullOrWhiteSpace($InstallerPath)) {
    if (-not $BuildIfMissing) { throw '请通过 -InstallerPath 明确提供 NSIS Setup EXE，或使用 -BuildIfMissing 受控构建。' }
    & (Join-Path $PSScriptRoot 'build_desktop.ps1') -Target nsis
    if ($LASTEXITCODE -ne 0) { throw '受控 NSIS 构建失败。' }
    $InstallerPath = Find-SetupExecutable -OutputDirectory $ArtifactsDirectory
}

$installer = [System.IO.Path]::GetFullPath($InstallerPath)
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw "安装包不存在：$installer" }
$TemporaryRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
Assert-NoReparsePoint -Path $TemporaryRoot
$SafetyRoot = Resolve-SafeChildPath -SafeRoot $TemporaryRoot -ChildName "fulua-cd8-acceptance-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $SafetyRoot -ErrorAction Stop | Out-Null
Assert-NoReparsePoint -Path $SafetyRoot
$RunRoot = Resolve-SafeChildPath -SafeRoot $SafetyRoot -ChildName ([guid]::NewGuid().ToString('N'))
$InstallRoot = Resolve-SafeChildPath -SafeRoot $RunRoot -ChildName 'install'
$AuthorLocalAppData = Resolve-SafeChildPath -SafeRoot $RunRoot -ChildName 'author-localappdata'
$MigrationLocalAppData = Resolve-SafeChildPath -SafeRoot $RunRoot -ChildName 'migration-localappdata'
$AuthorDataRoot = Resolve-SafeChildPath -SafeRoot $AuthorLocalAppData -ChildName '附录A编写工具'
$MigrationDataRoot = Resolve-SafeChildPath -SafeRoot $MigrationLocalAppData -ChildName '附录A编写工具'
$EvidencePath = Resolve-SafeChildPath -SafeRoot $RunRoot -ChildName 'acceptance-evidence.png'
$EditablePath = Resolve-SafeChildPath -SafeRoot $RunRoot -ChildName 'acceptance-editable.docx'
$FinalPath = Resolve-SafeChildPath -SafeRoot $RunRoot -ChildName 'acceptance-final.docx'
$ProjectName = "CD8-author-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$ImportedProjectName = "CD8-import-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
$OriginalLocalAppData = $env:LOCALAPPDATA
$AuthorLaunch = $null
$MigrationLaunch = $null
$SidecarLaunch = $null
$ProcessTerminationFailed = $false
$cleanupAllowed = $false
$installedExecutable = Join-Path $InstallRoot 'FuLuA.exe'
$Result = [ordered]@{
    status = 'failed'
    failure_message = ''
    installer = $installer
    installer_sha512 = (Get-FileHash -LiteralPath $installer -Algorithm SHA512).Hash.ToLowerInvariant()
    signatures = @()
    package_contents_checked = $false
    project_saved = $false
    image_uploaded = $false
    validation_checked = $false
    editable_exported = $false
    final_exported = $false
    docx_imported = $false
    close_reopen_checked = $false
    migration_preflight_checked = $false
    migration_checked = $false
    source_database_hash_before = ''
    source_database_hash_after = ''
    uninstall_data_retained = $false
    reinstall_checked = $false
    manual_items = @('图片粘贴快捷键与剪贴板交互需在可交互桌面人工确认', '从上一已发布版本在线升级需在两个真实版本发布后确认', '干净 Windows 虚拟机与代码签名信任链需由发布负责人确认')
}

try {
    New-Item -ItemType Directory -Force -Path $RunRoot, $AuthorLocalAppData, $MigrationLocalAppData | Out-Null
    $installation = Start-Process -FilePath $installer -ArgumentList @('/S', "/D=$InstallRoot") -Wait -PassThru
    if ($installation.ExitCode -ne 0) { throw "静默安装失败（退出码 $($installation.ExitCode)）。" }
    if (-not (Test-Path -LiteralPath $installedExecutable -PathType Leaf)) { throw '安装后缺少 FuLuA.exe。' }

    Assert-InstalledProgramResources -InstallRoot $InstallRoot
    Assert-PackagedAsarContents -InstallRoot $InstallRoot
    $Result.package_contents_checked = $true
    $backendExecutable = Join-Path $InstallRoot 'resources\backend\fulua-backend.exe'
    $Result.signatures = @(
        (Get-SignatureEvidence -Path $installer),
        (Get-SignatureEvidence -Path $installedExecutable),
        (Get-SignatureEvidence -Path $backendExecutable)
    )

    New-AcceptanceImage -Path $EvidencePath
    $AuthorLaunch = Start-InstalledClient -Executable $installedExecutable -LocalAppData $AuthorLocalAppData
    $authorServer = Wait-DesktopHealth -ExpectedDataRoot $AuthorDataRoot
    $rootPage = Invoke-WebRequest -Uri "$($authorServer.BaseUri)/" -UseBasicParsing -TimeoutSec 10
    if ($rootPage.StatusCode -ne 200) { throw '客户端根页面不可用。' }

    $created = Invoke-JsonRequest -Method POST -Uri "$($authorServer.BaseUri)/api/projects" -Body @{ name = $ProjectName }
    $projectId = [int]$created.id
    $uploadUri = "$($authorServer.BaseUri)/api/projects/{0}/evidence" -f $projectId
    $uploaded = Send-MultipartFile -Uri $uploadUri -FilePath $EvidencePath -FormFileName 'file' -Fields @{ section_code = 'A-1'; caption = 'CD-8 验收图片'; alt_text = 'CD-8 验收图片' }
    $imageId = [int]$uploaded.id
    $Result.image_uploaded = $true

    $sectionBody = @{
        title = '安全通用要求'
        table_title = 'A-1 安全通用要求测评结果记录'
        subsystems = @('业务系统')
        rows = @(@{
            unit = '身份鉴别'
            object_name = '验收服务器'
            subsystem = '业务系统'
            record_text = "检查登录策略，见 [[FIG:$imageId]]。"
            sort_order = 1
            metric_result = @{ d = '√'; a = '√'; k = '/'; object_score = '1.0000'; unit_score = '1.0000' }
            cross_references = @(@{ target_image_id = $imageId; token = "[[FIG:$imageId]]"; display_text = '图A-1-1' })
        })
    }
    $saved = Invoke-JsonRequest -Method PUT -Uri "$($authorServer.BaseUri)/api/projects/$projectId/sections/A-1" -Body $sectionBody
    if (@($saved.rows).Count -ne 1 -or @($saved.evidence_images).Count -ne 1) { throw '项目保存后的章节或证据图片数量不一致。' }
    $Result.project_saved = $true

    $validation = Invoke-JsonRequest -Method POST -Uri "$($authorServer.BaseUri)/api/projects/$projectId/validate" -Body @{}
    if ([int]$validation.summary.errors -ne 0) { throw "项目校验仍有 $($validation.summary.errors) 个错误。" }
    $Result.validation_checked = $true
    Export-ProjectDocx -BaseUri $authorServer.BaseUri -ProjectId $projectId -Mode editable -Destination $EditablePath
    $Result.editable_exported = $true
    Export-ProjectDocx -BaseUri $authorServer.BaseUri -ProjectId $projectId -Mode final -Destination $FinalPath
    $Result.final_exported = $true

    $importJob = Send-MultipartFile -Uri "$($authorServer.BaseUri)/api/imports/docx" -FilePath $EditablePath -FormFileName 'file'
    if (-not $importJob.can_create_project) { throw 'editable DOCX 解析后不能创建项目。' }
    $confirmUri = "$($authorServer.BaseUri)/api/imports/{0}/project" -f ([int]$importJob.id)
    $confirmed = Invoke-JsonRequest -Method POST -Uri $confirmUri -Body @{ project_name = $ImportedProjectName }
    if ($confirmed.status -ne 'succeeded' -or $null -eq $confirmed.created_project_id) { throw 'DOCX 导入创建项目失败。' }
    $Result.docx_imported = $true

    Stop-TestProcessTree $AuthorLaunch; $AuthorLaunch = $null; $AuthorLaunch = Start-InstalledClient -Executable $installedExecutable -LocalAppData $AuthorLocalAppData
    $authorServer = Wait-DesktopHealth -ExpectedDataRoot $AuthorDataRoot
    Assert-ProjectRetained -BaseUri $authorServer.BaseUri -ProjectNames @($ProjectName, $ImportedProjectName)
    $Result.close_reopen_checked = $true
    Stop-TestProcessTree $AuthorLaunch
    $AuthorLaunch = $null

    $sourceDatabase = Join-Path $AuthorDataRoot 'data\app.db'
    $Result.source_database_hash_before = (Get-FileHash -LiteralPath $sourceDatabase -Algorithm SHA256).Hash.ToLowerInvariant()
    $sessionToken = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $sidecarStdout = Resolve-SafeChildPath -SafeRoot $RunRoot -ChildName 'migration-sidecar.stdout.log'
    $sidecarStderr = Resolve-SafeChildPath -SafeRoot $RunRoot -ChildName 'migration-sidecar.stderr.log'
    $webDist = Join-Path $InstallRoot 'resources\frontend'
    $SidecarLaunch = Start-PackagedSidecar -Executable $backendExecutable -DataRoot $MigrationDataRoot -WebDist $webDist -SessionToken $sessionToken -OutputPath $sidecarStdout -ErrorPath $sidecarStderr
    $migrationServer = Wait-DesktopHealth -ExpectedDataRoot $MigrationDataRoot
    $runtimeHeaders = @{ 'x-fulua-session-token' = $sessionToken }
    $preflight = Invoke-JsonRequest -Method POST -Uri "$($migrationServer.BaseUri)/api/runtime/migration/preflight" -Body @{ source_root = $AuthorDataRoot } -Headers $runtimeHeaders
    if (-not $preflight.can_migrate -or [int]$preflight.project_count -lt 2) { throw '真实打包侧车未通过旧数据迁移预检。' }
    $Result.migration_preflight_checked = $true
    $migrated = Invoke-JsonRequest -Method POST -Uri "$($migrationServer.BaseUri)/api/runtime/migration" -Body @{ source_root = $AuthorDataRoot } -Headers $runtimeHeaders
    if (-not $migrated.migrated -or -not $migrated.restart_required) { throw '真实打包侧车迁移未返回成功重启标记。' }
    $Result.source_database_hash_after = (Get-FileHash -LiteralPath $sourceDatabase -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($Result.source_database_hash_after -ne $Result.source_database_hash_before) { throw '迁移改变了旧数据源数据库。' }
    Stop-TestProcessTree $SidecarLaunch
    $SidecarLaunch = $null

    $MigrationLaunch = Start-InstalledClient -Executable $installedExecutable -LocalAppData $MigrationLocalAppData
    $migrationServer = Wait-DesktopHealth -ExpectedDataRoot $MigrationDataRoot
    Assert-ProjectRetained -BaseUri $migrationServer.BaseUri -ProjectNames @($ProjectName, $ImportedProjectName)
    $Result.migration_checked = $true
    Stop-TestProcessTree $MigrationLaunch
    $MigrationLaunch = $null

    $uninstallers = @(Get-ChildItem -LiteralPath $InstallRoot -File -Filter '*uninstall*.exe')
    if ($uninstallers.Count -ne 1) { throw '未找到唯一的 uninstall.exe。' }
    $uninstaller = $uninstallers[0].FullName
    $uninstallation = Start-Process -FilePath $uninstaller -ArgumentList @('/S') -Wait -PassThru
    if ($uninstallation.ExitCode -ne 0) { throw "静默卸载失败（退出码 $($uninstallation.ExitCode)）。" }
    if (-not (Test-Path -LiteralPath $AuthorDataRoot -PathType Container) -or -not (Test-Path -LiteralPath $MigrationDataRoot -PathType Container)) {
        throw '卸载后未保留隔离用户数据目录。'
    }
    $Result.uninstall_data_retained = $true

    $reinstall = Start-Process -FilePath $installer -ArgumentList @('/S', "/D=$InstallRoot") -Wait -PassThru
    if ($reinstall.ExitCode -ne 0) { throw "静默 reinstall 失败（退出码 $($reinstall.ExitCode)）。" }
    $MigrationLaunch = Start-InstalledClient -Executable $installedExecutable -LocalAppData $MigrationLocalAppData
    $migrationServer = Wait-DesktopHealth -ExpectedDataRoot $MigrationDataRoot
    Assert-ProjectRetained -BaseUri $migrationServer.BaseUri -ProjectNames @($ProjectName, $ImportedProjectName)
    $Result.reinstall_checked = $true
    $Result.status = 'passed'
}
catch {
    $Result.failure_message = $_.Exception.Message
    throw
}
finally {
    try {
        try {
            Stop-TestProcessTree $MigrationLaunch
            Stop-TestProcessTree $SidecarLaunch
            Stop-TestProcessTree $AuthorLaunch
        }
        catch {
            $ProcessTerminationFailed = $true
            throw
        }
        if (-not $ProcessTerminationFailed) {
            $cleanupAllowed = ($Result.status -eq 'passed')
            if (Test-Path -LiteralPath $InstallRoot -PathType Container) {
                $finalUninstallers = @(Get-ChildItem -LiteralPath $InstallRoot -File -Filter '*uninstall*.exe')
                if ($finalUninstallers.Count -eq 1) {
                    $finalUninstall = Start-Process -FilePath $finalUninstallers[0].FullName -ArgumentList @('/S') -Wait -PassThru
                    if ($finalUninstall.ExitCode -ne 0) { $cleanupAllowed = $false }
                }
            }
        }
    }
    finally {
        $env:LOCALAPPDATA = $OriginalLocalAppData
        if ($cleanupAllowed) {
            Remove-SafeTestPath -Path $RunRoot -SafeRoot $SafetyRoot
            Remove-SafeTestPath -Path $SafetyRoot -SafeRoot $TemporaryRoot
        }
        [pscustomobject]$Result | ConvertTo-Json -Depth 8
    }
}
