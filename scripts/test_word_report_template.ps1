param(
    [string]$TemplatePath = "",
    [string]$EvidencePath = "",
    [string]$RoundtripPath = "",
    [switch]$Required
)

$ErrorActionPreference = "Stop"

if (-not $IsWindows -and $PSVersionTable.PSEdition -eq "Core") {
    if ($Required) { throw "WORD_AUTOMATION_REQUIRES_WINDOWS" }
    Write-Host "SKIP: Microsoft Word automation requires Windows."
    exit 0
}

if (-not $TemplatePath) {
    $TemplatePath = Join-Path $PSScriptRoot "..\templates\report\2023-2025.12.08\runtime_template.docx"
}
$resolvedTemplate = (Resolve-Path -LiteralPath $TemplatePath).Path
if (-not $RoundtripPath) {
    $RoundtripPath = Join-Path $PSScriptRoot "..\artifacts\r0-word-acceptance\runtime_template-roundtrip.docx"
}
$resolvedRoundtrip = [System.IO.Path]::GetFullPath($RoundtripPath)
if ($resolvedRoundtrip -eq $resolvedTemplate) {
    throw "WORD_ROUNDTRIP_PATH_MUST_DIFFER_FROM_TEMPLATE"
}
$roundtripParent = Split-Path -Parent $resolvedRoundtrip
New-Item -ItemType Directory -Path $roundtripParent -Force | Out-Null
Copy-Item -LiteralPath $resolvedTemplate -Destination $resolvedRoundtrip -Force

$word = $null
$document = $null
$roundtripDocument = $null
$reopenedDocument = $null
try {
    try {
        $word = New-Object -ComObject Word.Application
    }
    catch {
        if ($Required) { throw "WORD_AUTOMATION_UNAVAILABLE: $($_.Exception.Message)" }
        Write-Host "SKIP: Microsoft Word is not installed or automation is unavailable."
        exit 0
    }

    $word.Visible = $false
    $word.DisplayAlerts = -1
    $document = $word.Documents.OpenNoRepairDialog($resolvedTemplate, $false, $true, $false)

    if ($document.Sections.Count -ne 17) {
        throw "WORD_SECTION_COUNT_MISMATCH: expected=17 actual=$($document.Sections.Count)"
    }
    if ($document.Tables.Count -ne 55) {
        throw "WORD_TABLE_COUNT_MISMATCH: expected=55 actual=$($document.Tables.Count)"
    }
    if ($document.ContentControls.Count -ne 605) {
        throw "WORD_CONTENT_CONTROL_COUNT_MISMATCH: expected=605 actual=$($document.ContentControls.Count)"
    }

    $sourcePages = $document.ComputeStatistics(2)
    $document.Close($false)
    $document = $null

    $roundtripDocument = $word.Documents.OpenNoRepairDialog($resolvedRoundtrip, $false, $false, $false)
    $roundtripDocument.Save()
    $roundtripDocument.Close($false)
    $roundtripDocument = $null

    $reopenedDocument = $word.Documents.OpenNoRepairDialog($resolvedRoundtrip, $false, $true, $false)
    if ($reopenedDocument.Sections.Count -ne 17) {
        throw "WORD_ROUNDTRIP_SECTION_COUNT_MISMATCH: expected=17 actual=$($reopenedDocument.Sections.Count)"
    }
    if ($reopenedDocument.Tables.Count -ne 55) {
        throw "WORD_ROUNDTRIP_TABLE_COUNT_MISMATCH: expected=55 actual=$($reopenedDocument.Tables.Count)"
    }
    if ($reopenedDocument.ContentControls.Count -ne 605) {
        throw "WORD_ROUNDTRIP_CONTENT_CONTROL_COUNT_MISMATCH: expected=605 actual=$($reopenedDocument.ContentControls.Count)"
    }

    $evidence = [ordered]@{
        schema_version = "1.0"
        package_id = "report-2023-2025.12.08"
        runtime_template_sha256 = (Get-FileHash -LiteralPath $resolvedTemplate -Algorithm SHA256).Hash.ToLowerInvariant()
        word_version = [string]$word.Version
        open_method = "OpenNoRepairDialog"
        display_alerts = "all"
        roundtrip_saved_and_reopened = $true
        section_count = [int]$reopenedDocument.Sections.Count
        table_count = [int]$reopenedDocument.Tables.Count
        content_control_count = [int]$reopenedDocument.ContentControls.Count
        page_count = [int]$sourcePages
    }
    if ($EvidencePath) {
        $resolvedEvidence = [System.IO.Path]::GetFullPath($EvidencePath)
        New-Item -ItemType Directory -Path (Split-Path -Parent $resolvedEvidence) -Force | Out-Null
        $json = ($evidence | ConvertTo-Json) -replace "`r`n", "`n"
        [System.IO.File]::WriteAllText(
            $resolvedEvidence,
            $json + "`n",
            (New-Object System.Text.UTF8Encoding($false))
        )
    }

    Write-Host "PASS: Word opened the runtime template without repair prompts."
    Write-Host "sections=$($evidence.section_count) tables=$($evidence.table_count) content_controls=$($evidence.content_control_count) pages=$($evidence.page_count) word_version=$($evidence.word_version) roundtrip=true"
}
finally {
    if ($null -ne $reopenedDocument) {
        $reopenedDocument.Close($false)
    }
    if ($null -ne $roundtripDocument) {
        $roundtripDocument.Close($false)
    }
    if ($null -ne $document) {
        $document.Close($false)
    }
    if ($null -ne $word) {
        $word.Quit()
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
    if (Test-Path -LiteralPath $resolvedRoundtrip) {
        Remove-Item -LiteralPath $resolvedRoundtrip -Force
    }
}
