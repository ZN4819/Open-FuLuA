Set-StrictMode -Version Latest

function Assert-BuildPathNotReparsePoint {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to clean a build path containing a reparse point: $($item.FullName)"
    }
}

function Remove-ManagedBuildTree {
    param([Parameter(Mandatory = $true)][string]$Path)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to clean a build tree containing a reparse point: $($item.FullName)"
    }
    if ($item.PSIsContainer) {
        foreach ($child in @(Get-ChildItem -LiteralPath $item.FullName -Force)) {
            Remove-ManagedBuildTree -Path $child.FullName
        }
    }
    Remove-Item -LiteralPath $item.FullName -Force
}

function Remove-ManagedBuildDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [Parameter(Mandatory = $true)][string]$CandidatePath
    )

    $workspace = [System.IO.Path]::GetFullPath($WorkspaceRoot).TrimEnd('\')
    $artifacts = [System.IO.Path]::GetFullPath((Join-Path $workspace 'artifacts'))
    $managedRoot = [System.IO.Path]::GetFullPath((Join-Path $artifacts 'desktop')).TrimEnd('\')
    $candidate = [System.IO.Path]::GetFullPath($CandidatePath).TrimEnd('\')
    $managedPrefix = "$managedRoot\"

    if ($candidate -eq $managedRoot -or -not $candidate.StartsWith($managedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the managed desktop build directory: $candidate"
    }

    foreach ($parent in @($workspace, $artifacts, $managedRoot)) {
        Assert-BuildPathNotReparsePoint -Path $parent
    }

    $current = $managedRoot
    $relativeParts = $candidate.Substring($managedPrefix.Length).Split(@('\'), [System.StringSplitOptions]::RemoveEmptyEntries)
    foreach ($part in $relativeParts) {
        $current = Join-Path $current $part
        Assert-BuildPathNotReparsePoint -Path $current
    }

    if (Test-Path -LiteralPath $candidate) {
        Remove-ManagedBuildTree -Path $candidate
    }
}
