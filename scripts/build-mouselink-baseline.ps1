param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $taskRoot '.venv\Scripts\python.exe'
$sourceRoot = Join-Path $taskRoot 'vendor\mouselink'
$buildRoot = Join-Path $taskRoot 'build\mouselink'
$artifactRoot = Join-Path $taskRoot 'artifacts\baseline\mouselink'
$distributionRoot = Join-Path $artifactRoot 'dist'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Create the root .venv and install vendor\mouselink\open_bridge\requirements-desktop-lock.txt first.' }
New-Item -ItemType Directory -Path $buildRoot,$artifactRoot,$distributionRoot -Force | Out-Null
$environmentNames = @('PATH','PYINSTALLER_CONFIG_DIR','MOUSELINK_DATA_DIR','QT_QPA_PLATFORM','PYTHONUTF8','PYTHONDONTWRITEBYTECODE')
$previous = @{}
foreach ($name in $environmentNames) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:PYTHONUTF8 = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:MOUSELINK_DATA_DIR = Join-Path $buildRoot 'isolated-data'
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $buildRoot 'pyinstaller-cache'
    & $pythonPath -m pip freeze --all | Set-Content -LiteralPath (Join-Path $artifactRoot 'environment-freeze.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Cannot capture the build environment.' }
    & $pythonPath -m pip check | Tee-Object -FilePath (Join-Path $artifactRoot 'dependency-check.log')
    if ($LASTEXITCODE -ne 0) { throw 'Build dependencies are inconsistent.' }
    Push-Location $sourceRoot
    try {
        if (-not $SkipTests) {
            & $pythonPath -m unittest discover -s tests -p 'test_*.py' *> (Join-Path $artifactRoot 'unittest.log')
            if ($LASTEXITCODE -ne 0) { throw 'MouseLink baseline regression failed; see artifacts\baseline\mouselink\unittest.log.' }
            & $pythonPath 'tools\check_desktop_101.py' --output (Join-Path $artifactRoot 'desktop-regressions.json') *> (Join-Path $artifactRoot 'desktop-regressions.log')
            if ($LASTEXITCODE -ne 0) { throw 'MouseLink offline Qt lifecycle regression failed.' }
        }
        & $pythonPath 'open_bridge\flash_helper.py' --self-check *> (Join-Path $artifactRoot 'firmware-self-check.log')
        if ($LASTEXITCODE -ne 0) { throw 'Firmware bundle validation failed.' }
        # Isolate DLL discovery from unrelated PATH programs such as Poppler.
        $env:PATH = (Split-Path -Parent $pythonPath) + ';' + $env:SystemRoot + '\System32;' + $env:SystemRoot
        & $pythonPath -m PyInstaller --clean --noconfirm --distpath $distributionRoot --workpath (Join-Path $buildRoot 'pyinstaller') (Join-Path $sourceRoot 'tools\MouseLink.spec') *> (Join-Path $artifactRoot 'pyinstaller.log')
        if ($LASTEXITCODE -ne 0) { throw 'MouseLink desktop/helper build failed; see pyinstaller.log.' }
        $applicationRoot = Join-Path $distributionRoot 'MouseLink'
        foreach ($name in @('使用说明.txt','THIRD_PARTY_NOTICES.md','LICENSE','licenses')) {
            Copy-Item -LiteralPath (Join-Path $sourceRoot "open_bridge\$name") -Destination $applicationRoot -Recurse -Force
        }
        & (Join-Path $applicationRoot 'MouseLinkFlash.exe') --self-check *> (Join-Path $artifactRoot 'bundled-firmware-self-check.log')
        if ($LASTEXITCODE -ne 0) { throw 'Packaged firmware helper validation failed.' }
        Get-ChildItem -LiteralPath $applicationRoot -File -Recurse | Sort-Object FullName | ForEach-Object {
            $relativePath = $_.FullName.Substring($applicationRoot.Length + 1).Replace('\','/')
            '{0}  {1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(),$relativePath
        } | Set-Content -LiteralPath (Join-Path $artifactRoot 'built-SHA256SUMS.txt') -Encoding utf8
        Write-Output "Baseline build complete: $applicationRoot"
    } finally { Pop-Location }
} finally {
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
