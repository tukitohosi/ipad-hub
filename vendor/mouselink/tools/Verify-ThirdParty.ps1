[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$checksumFile = Join-Path $workspaceRoot 'third_party\SHA256SUMS.txt'

if (-not (Test-Path -LiteralPath $checksumFile)) {
    throw "Checksum file not found: $checksumFile"
}

$results = foreach ($line in Get-Content -LiteralPath $checksumFile) {
    if ([string]::IsNullOrWhiteSpace($line)) {
        continue
    }

    if ($line -notmatch '^(?<hash>[A-Fa-f0-9]{64})\s{2}(?<path>.+)$') {
        throw "Invalid checksum line: $line"
    }

    $expected = $Matches.hash.ToUpperInvariant()
    $relativePath = $Matches.path.Replace('/', [IO.Path]::DirectorySeparatorChar)
    $fullPath = Join-Path (Join-Path $workspaceRoot 'third_party') $relativePath

    if (-not (Test-Path -LiteralPath $fullPath)) {
        [pscustomobject]@{
            Status = 'MISSING'
            Path = $relativePath
            Expected = $expected
            Actual = ''
        }
        continue
    }

    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $fullPath).Hash.ToUpperInvariant()
    [pscustomobject]@{
        Status = if ($actual -eq $expected) { 'OK' } else { 'MISMATCH' }
        Path = $relativePath
        Expected = $expected
        Actual = $actual
    }
}

$results | Format-Table Status, Path -AutoSize

$failures = @($results | Where-Object Status -ne 'OK')
if ($failures.Count -gt 0) {
    throw "$($failures.Count) third-party file(s) failed verification."
}

Write-Host "All $($results.Count) third-party files passed SHA-256 verification."
