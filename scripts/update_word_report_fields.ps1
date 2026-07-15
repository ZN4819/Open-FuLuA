param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"

$source = (Resolve-Path -LiteralPath $InputPath).Path
$target = [System.IO.Path]::GetFullPath($OutputPath)
if ($source -eq $target) {
    throw "WORD_FIELD_OUTPUT_MUST_DIFFER_FROM_INPUT"
}
$targetParent = Split-Path -Parent $target
if (-not (Test-Path -LiteralPath $targetParent)) {
    New-Item -ItemType Directory -Path $targetParent -Force | Out-Null
}
Copy-Item -LiteralPath $source -Destination $target -Force

function Update-AllStoryFields {
    param([Parameter(Mandatory = $true)]$Document)
    foreach ($story in $Document.StoryRanges) {
        $range = $story
        while ($null -ne $range) {
            if ($range.Fields.Count -gt 0) {
                [void]$range.Fields.Update()
            }
            $range = $range.NextStoryRange
        }
    }
}

$word = $null
$document = $null
try {
    $word = New-Object -ComObject Word.Application
    $word.Visible = $false
    $word.DisplayAlerts = 0
    $document = $word.Documents.Open($target)

    $document.Repaginate()
    Update-AllStoryFields -Document $document
    foreach ($toc in $document.TablesOfContents) {
        [void]$toc.Update()
    }
    foreach ($tableOfFigures in $document.TablesOfFigures) {
        [void]$tableOfFigures.Update()
    }
    [void]$document.Fields.Update()
    $document.Repaginate()
    foreach ($toc in $document.TablesOfContents) {
        [void]$toc.UpdatePageNumbers()
    }
    Update-AllStoryFields -Document $document
    $document.Repaginate()
    $document.Save()

    Write-Host "PASS: Word refreshed all story, TOC and page-number fields."
    Write-Host "pages=$($document.ComputeStatistics(2)) toc=$($document.TablesOfContents.Count) fields=$($document.Fields.Count)"
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
