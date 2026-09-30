param(
    [string]$BuildRoot,
    [string]$Python,
    [string]$PublishBundle,
    [string]$BundleVersion,
    [switch]$DirectDownload
)
$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
if (-not $BuildRoot) {
    $tempRoot = [IO.Path]::GetTempPath()
    if ($tempRoot -match '[^\x20-\x7E]' -or $tempRoot.Contains(' ')) {
        $tempRoot = Join-Path $env:SystemRoot 'Temp'
    }
    $BuildRoot = Join-Path $tempRoot ('MouseLink-build-' + [guid]::NewGuid().ToString('N'))
}
$BuildRoot = [IO.Path]::GetFullPath($BuildRoot)
if ($BuildRoot -match '[^\x20-\x7E]' -or $BuildRoot.Contains(' ')) {
    throw 'Use an ASCII build directory without spaces.'
}
if (-not $Python) {
    foreach ($candidate in @('runtime\open-bridge-venv\Scripts\python.exe',
            '.venv\Scripts\python.exe', 'runtime\build-venv\Scripts\python.exe')) {
        $candidatePath = Join-Path $workspacePath $candidate
        if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
            $Python = $candidatePath
            break
        }
    }
    if (-not $Python) { $Python = 'python' }
}
$pythonPath = (Get-Command $Python -CommandType Application -ErrorAction Stop).Source
# A fresh stage prevents deleted source files from surviving a repeated build.
$stagePath = Join-Path $BuildRoot ('firmware-' + [guid]::NewGuid().ToString('N'))
$corePath = Join-Path $BuildRoot 'platformio'
$tempPath = Join-Path $BuildRoot 'temp'
try {
    New-Item -ItemType Directory -Path $stagePath,$tempPath -Force | Out-Null
} catch {
    throw "Cannot create build directories under '$BuildRoot'. Pass -BuildRoot with a writable ASCII path without spaces. $($_.Exception.Message)"
}
foreach ($name in @('CMakeLists.txt','platformio.ini','sdkconfig.defaults',
        'sdkconfig.defaults.esp32c3','sdkconfig.esp32-c3-supermini','main')) {
    Copy-Item -LiteralPath (Join-Path $workspacePath "open_bridge\$name") -Destination $stagePath -Recurse -Force
}
$settings = @{
    PLATFORMIO_CORE_DIR=$corePath
    TEMP=$tempPath
    TMP=$tempPath
    PYTHONIOENCODING='utf-8'
    PYTHONUTF8='1'
}
if ($DirectDownload) { $settings.HTTP_PROXY=''; $settings.HTTPS_PROXY=''; $settings.ALL_PROXY='' }
$previous = @{}
foreach ($key in $settings.Keys) {
    $previous[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
    [Environment]::SetEnvironmentVariable($key, $settings[$key], 'Process')
}
try {
    & $pythonPath -m platformio run -d $stagePath -e esp32-c3-supermini
    if ($LASTEXITCODE -ne 0) { throw 'Firmware build failed.' }
    $imageRoot = Join-Path $stagePath '.pio\build\esp32-c3-supermini'
    if ($PublishBundle) {
        $bundleArgs = @('--build-dir', $imageRoot, '--output', [IO.Path]::GetFullPath($PublishBundle))
        if ($BundleVersion) { $bundleArgs += @('--version', $BundleVersion) }
        & $pythonPath (Join-Path $PSScriptRoot 'build_firmware_bundle.py') @bundleArgs
        if ($LASTEXITCODE -ne 0) { throw 'Firmware bundle generation failed.' }
    }
    Write-Output (Join-Path $imageRoot 'firmware.bin')
} finally {
    foreach ($key in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($key, $previous[$key], 'Process')
    }
}
