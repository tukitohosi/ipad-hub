[CmdletBinding()]
param([string]$Version = '0.1.1-preview')

# Uses only the portable package and Windows; no project Python or device access.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$releaseRoot = Join-Path $taskRoot "release\$Version"
$evidenceRoot = Join-Path $taskRoot ("artifacts\release-$Version\standalone-" + (Get-Date -Format 'yyyyMMdd-HHmmss-ffff'))
$staging = Join-Path $evidenceRoot 'temporary'
$bundle = Join-Path $staging 'iPadHub'
$smokePath = Join-Path $evidenceRoot 'application-smoke.json'
$environmentNames = @('PATH','PYTHONHOME','PYTHONPATH','QT_QPA_PLATFORM','TEMP','TMP')
$previous = @{}
foreach ($name in $environmentNames) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$result = [ordered]@{ ok=$false; version=$Version; project_python_used=$false; hardware_commands_sent=@(); temporary_files_removed=$false; checked_at_utc=(Get-Date).ToUniversalTime().ToString('o') }
New-Item -ItemType Directory -Path $staging -Force | Out-Null
try {
    $portable = Join-Path $releaseRoot "iPadHub-$Version-Windows-x64.zip"
    $result['portable_sha256'] = (Get-FileHash -LiteralPath $portable -Algorithm SHA256).Hash.ToLowerInvariant()
    Expand-Archive -LiteralPath $portable -DestinationPath $staging
    $env:PATH = $env:SystemRoot + '\System32;' + $env:SystemRoot
    $env:PYTHONHOME = $null
    $env:PYTHONPATH = $null
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:TEMP = Join-Path $staging 'temp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
    $process = Start-Process -FilePath (Join-Path $bundle 'iPadHub.exe') -ArgumentList @('--smoke-test',('"' + $smokePath + '"'),'--data-dir',('"' + (Join-Path $staging 'data') + '"'),'--no-migrate') -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(60000)) { $process.Kill(); throw 'Standalone application startup timed out.' }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "Standalone application failed ($($process.ExitCode))." }
    $smoke = Get-Content -LiteralPath $smokePath -Raw | ConvertFrom-Json
    if (-not $smoke.ok -or $smoke.engine_started -or $smoke.migration_performed) { throw 'Standalone device-free smoke failed.' }
    & (Join-Path $bundle 'MouseLinkFlash.exe') --self-check *> (Join-Path $evidenceRoot 'firmware-self-check.log')
    if ($LASTEXITCODE -ne 0) { throw 'Standalone firmware resource self-check failed.' }
    $result['application_smoke_passed'] = $true
    $result['firmware_resource_check_passed'] = $true
    $result['project_venv_present'] = Test-Path -LiteralPath (Join-Path $taskRoot '.venv')
    $result['ok'] = $true
} catch { $result['error'] = $_.Exception.Message }
finally {
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    # Check every existing ancestor and descendant before deleting our own staging.
    try {
        $absolute = [IO.Path]::GetFullPath($staging)
        if (-not $absolute.StartsWith($taskRoot + '\artifacts\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Standalone staging escaped the workspace.' }
        for ($probe=$absolute; $probe; $probe=[IO.Path]::GetDirectoryName($probe)) {
            if ((Test-Path -LiteralPath $probe) -and ((Get-Item -LiteralPath $probe -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Reparse point refused: $probe" }
        }
        $all = @(Get-ChildItem -LiteralPath $absolute -Force -Recurse)
        if (@($all | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { throw 'Reparse point in standalone staging; retained for review.' }
        $result['temporary_removed_files'] = @($all | Where-Object { -not $_.PSIsContainer }).Count
        $result['temporary_removed_bytes'] = ($all | Where-Object { -not $_.PSIsContainer } | Measure-Object Length -Sum).Sum
        foreach ($file in ($all | Where-Object { -not $_.PSIsContainer })) { Remove-Item -LiteralPath $file.FullName -Force }
        foreach ($directory in ($all | Where-Object PSIsContainer | Sort-Object { $_.FullName.Length } -Descending)) { Remove-Item -LiteralPath $directory.FullName -Force }
        Remove-Item -LiteralPath $absolute -Force
        $result['temporary_files_removed'] = -not (Test-Path -LiteralPath $absolute)
    } catch { $result['cleanup_error'] = $_.Exception.Message; $result['ok'] = $false }
    [IO.File]::WriteAllText((Join-Path $evidenceRoot 'result.json'), (ConvertTo-Json -InputObject $result -Depth 10) + "`n", [Text.UTF8Encoding]::new($false))
    $result | ConvertTo-Json -Depth 10
    Write-Output "Standalone evidence: $evidenceRoot"
}
if (-not $result.ok) { throw 'Standalone validation failed.' }
