param(
    [Parameter(Mandatory = $true)][string]$InputPath,
    [Parameter(Mandatory = $true)][string]$OutputPath,
    [Parameter(Mandatory = $true)][string]$StatusPath
)

$ErrorActionPreference = 'Stop'

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class WordWindowProcess {
    [DllImport("user32.dll")]
    public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
}
"@

function Write-Status([hashtable]$Value) {
    $directory = Split-Path -Parent $StatusPath
    if ($directory -and -not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $temporary = "$StatusPath.tmp"
    $Value | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $StatusPath -Force
}

function Get-OwnedWordProcessId($Application, [int[]]$ExistingProcessIds) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $handleValue = 0
        try {
            $rawHandle = $Application.Hwnd
            if ($null -ne $rawHandle) { $handleValue = [int64]$rawHandle }
        }
        catch {}
        if ($handleValue -gt 0) {
            $processId = [uint32]0
            [WordWindowProcess]::GetWindowThreadProcessId([IntPtr]::new($handleValue), [ref]$processId) | Out-Null
            if ($processId -gt 0) { return [int]$processId }
        }

        $newProcessIds = @(
            Get-Process -Name WINWORD -ErrorAction SilentlyContinue |
                Where-Object { $ExistingProcessIds -notcontains $_.Id } |
                Select-Object -ExpandProperty Id
        )
        if ($newProcessIds.Count -eq 1) { return [int]$newProcessIds[0] }
        Start-Sleep -Milliseconds 100
    }
    throw 'Unable to identify the Microsoft Word process owned by this automation run.'
}

$word = $null
$document = $null
$pidValue = 0
$stage = 'startup'
$existingWordProcessIds = @(
    Get-Process -Name WINWORD -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id
)
try {
    $stage = 'create_application'
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $word.AutomationSecurity = 3
    $pidValue = Get-OwnedWordProcessId $word $existingWordProcessIds
    Write-Status @{ status = 'running'; pid = $pidValue; started_at = [DateTime]::UtcNow.ToString('o') }

    $stage = 'resolve_paths'
    $resolvedInput = (Resolve-Path -LiteralPath $InputPath).Path
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputPath)
    $stage = 'open_document'
    $document = $word.Documents.Open($resolvedInput, $false, $false, $false)

    $stage = 'update_story_fields'
    foreach ($story in $document.StoryRanges) {
        $range = $story
        while ($null -ne $range) {
            if ($range.Fields.Count -gt 0) { $range.Fields.Update() | Out-Null }
            $range = $range.NextStoryRange
        }
    }
    $stage = 'update_toc'
    foreach ($toc in $document.TablesOfContents) { $toc.Update() | Out-Null }
    $stage = 'update_tof'
    foreach ($tof in $document.TablesOfFigures) { $tof.Update() | Out-Null }
    $stage = 'update_document_fields'
    if ($document.Fields.Count -gt 0) { $document.Fields.Update() | Out-Null }
    $stage = 'repaginate'
    $document.Repaginate()
    foreach ($toc in $document.TablesOfContents) { $toc.UpdatePageNumbers() | Out-Null }
    foreach ($tof in $document.TablesOfFigures) { $tof.UpdatePageNumbers() | Out-Null }

    $stage = 'save_as_docx'
    $document.SaveAs2($resolvedOutput, 16)
    $stage = 'compute_pages'
    $pageCount = [int]$document.ComputeStatistics(2)
    $stage = 'close_document'
    $document.Close(0)
    $document = $null
    $word.Quit()
    $word = $null
    Write-Status @{
        status = 'succeeded'
        pid = $pidValue
        page_count = $pageCount
        output_path = $resolvedOutput
        finished_at = [DateTime]::UtcNow.ToString('o')
    }
    exit 0
}
catch {
    $message = $_.Exception.Message
    if ($null -ne $document) {
        try { $document.Close(0) } catch {}
    }
    if ($null -ne $word) {
        try { $word.Quit() } catch {}
    }
    Write-Status @{
        status = 'failed'
        pid = $pidValue
        error = $message
        stage = $stage
        finished_at = [DateTime]::UtcNow.ToString('o')
    }
    exit 1
}
