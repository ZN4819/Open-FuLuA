param(
    [string]$TemplatePath = "",
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

$word = $null
$document = $null
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
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($resolvedTemplate)

    if ($document.Sections.Count -ne 17) {
        throw "WORD_SECTION_COUNT_MISMATCH: expected=17 actual=$($document.Sections.Count)"
    }
    if ($document.Tables.Count -ne 55) {
        throw "WORD_TABLE_COUNT_MISMATCH: expected=55 actual=$($document.Tables.Count)"
    }
    if ($document.ContentControls.Count -lt 594) {
        throw "WORD_CONTENT_CONTROL_COUNT_MISMATCH: expected_at_least=594 actual=$($document.ContentControls.Count)"
    }

    Write-Host "PASS: Word opened the runtime template without repair prompts."
    Write-Host "sections=$($document.Sections.Count) tables=$($document.Tables.Count) content_controls=$($document.ContentControls.Count) pages=$($document.ComputeStatistics(2))"
}
finally {
    if ($null -ne $document) {
        $document.Close($false)
    }
    if ($null -ne $word) {
        $word.Quit()
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
