[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')]
    [string]$Version = '0.1.0-preview',
    [string]$IsccPath,
    [switch]$SkipDisplayBuild,
    [switch]$SkipInstaller
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonPath = Join-Path $taskRoot '.venv\Scripts\python.exe'
$outputRoot = Join-Path $taskRoot "release\$Version"
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-ffff'
$workRoot = Join-Path $taskRoot "build\package-$Version-$stamp"
$bundleRoot = Join-Path $workRoot 'dist\iPadHub'
$installerScript = Join-Path $taskRoot 'packaging\iPadHub.iss'

foreach ($path in @($pythonPath, $installerScript, (Join-Path $taskRoot 'LICENSE'),
                   (Join-Path $taskRoot 'docs\使用说明.md'), (Join-Path $taskRoot 'docs\release-acceptance.md'))) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required packaging input missing: $path" }
}
# No output cleanup: a repeated release keeps all earlier builds and installers.
if ((Test-Path -LiteralPath $outputRoot) -and @(Get-ChildItem -LiteralPath $outputRoot -Force).Count -gt 0) {
    throw "Versioned output is already populated; choose another version or preserve it elsewhere first: $outputRoot"
}

$installerText = [IO.File]::ReadAllText($installerScript)
if ($installerText -notmatch '(?im)^PrivilegesRequired=lowest\s*$' -or
    $installerText -notmatch '(?im)^AppId=\{\{2F7A9C14-E3D0-4D64-8A12-9B59D3CD7EF1\}\s*$') {
    throw 'Independent current-user installer identity or privilege level changed.'
}
if ($installerText -match '(?im)^\s*\[(Run|UninstallRun|Registry|Code|InstallDelete|UninstallDelete|INI)\]\s*$' -or
    $installerText -match '(?i)\b(pnputil|devcon|vddinstall)\b|netsh\s+advfirewall|New-NetFirewallRule|CurrentVersion\\Uninstall') {
    throw 'Installer contains an unexpected side-effectful command or section.'
}
if (-not $SkipInstaller) {
    if (-not $IsccPath) {
        $isccCandidates = @(
            (Join-Path $taskRoot '.tools\InnoSetup6\ISCC.exe'),
            'D:\Desktop\本地项目\iPad副屏\.tools\InnoSetup6\ISCC.exe',
            'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
        )
        $IsccPath = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
    if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) { throw 'Inno Setup 6 ISCC.exe is required, or pass -SkipInstaller.' }
}

New-Item -ItemType Directory -Path $workRoot -Force | Out-Null
$environmentNames = @('PATH','PYINSTALLER_CONFIG_DIR','PYTHONUTF8','PYTHONDONTWRITEBYTECODE','QT_QPA_PLATFORM','IPADHUB_BUILD_VERSION')
$previous = @{}
foreach ($name in $environmentNames) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
Push-Location $taskRoot
try {
    $env:PYTHONUTF8 = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:IPADHUB_BUILD_VERSION = $Version
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $workRoot 'pyinstaller-cache'
    & $pythonPath -m pip check *> (Join-Path $workRoot 'dependencies.log')
    if ($LASTEXITCODE -ne 0) { throw "Dependency validation failed: $workRoot\dependencies.log" }
    if (-not $SkipDisplayBuild) {
        & (Join-Path $PSScriptRoot 'build-display-engine.ps1')
        if ($LASTEXITCODE -ne 0) { throw 'Display engine build failed.' }
    }
    & $pythonPath -m unittest discover -s tests -p 'test_*.py' *> (Join-Path $workRoot 'tests.log')
    if ($LASTEXITCODE -ne 0) { throw "iPadHub tests failed: $workRoot\tests.log" }
    & $pythonPath (Join-Path $taskRoot 'packaging\make-icon.py')
    if ($LASTEXITCODE -ne 0) { throw 'Application icon generation failed.' }
    # Restrict DLL discovery to Windows and the locked Python environment.
    $env:PATH = (Split-Path -Parent $pythonPath) + ';' + $env:SystemRoot + '\System32;' + $env:SystemRoot
    & $pythonPath -m PyInstaller --clean --noconfirm --distpath (Join-Path $workRoot 'dist') --workpath (Join-Path $workRoot 'pyinstaller') (Join-Path $taskRoot 'packaging\iPadHub.spec') *> (Join-Path $workRoot 'pyinstaller.log')
    if ($LASTEXITCODE -ne 0) { throw "Unified application build failed: $workRoot\pyinstaller.log" }
    & (Join-Path $bundleRoot 'MouseLinkFlash.exe') --self-check *> (Join-Path $workRoot 'firmware-self-check.log')
    if ($LASTEXITCODE -ne 0) { throw 'Packaged firmware validation failed; no hardware was accessed.' }
    $smokeReport = Join-Path $workRoot 'application-smoke.json'
    $smokeArgs = @('--smoke-test', ('"' + $smokeReport + '"'), '--data-dir', ('"' + (Join-Path $workRoot 'smoke-data') + '"'), '--no-migrate')
    $smokeProcess = Start-Process -FilePath (Join-Path $bundleRoot 'iPadHub.exe') -ArgumentList $smokeArgs -PassThru -WindowStyle Hidden
    if (-not $smokeProcess.WaitForExit(60000)) { $smokeProcess.Kill(); throw 'Packaged UI smoke test timed out.' }
    if ($smokeProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $smokeReport -PathType Leaf)) { throw 'Packaged UI smoke test did not produce a successful report.' }
    $smoke = Get-Content -LiteralPath $smokeReport -Raw | ConvertFrom-Json
    if ($smoke.ok -ne $true) { throw "Packaged UI smoke test reported failure: $smokeReport" }
    $engineReport = Join-Path $workRoot 'engine-smoke.json'
    & $pythonPath (Join-Path $taskRoot 'packaging\check-frozen-engines.py') --bundle $bundleRoot --report $engineReport *> (Join-Path $workRoot 'engine-smoke.log')
    if ($LASTEXITCODE -ne 0) { throw "Packaged engine handshake validation failed: $workRoot\engine-smoke.log" }
    & $pythonPath (Join-Path $taskRoot 'packaging\prepare-release.py') --bundle $bundleRoot --output $outputRoot --version $Version
    if ($LASTEXITCODE -ne 0) { throw 'Source/portable archive preparation failed.' }
    Copy-Item -LiteralPath $smokeReport -Destination (Join-Path $outputRoot 'application-smoke.json')
    Copy-Item -LiteralPath $engineReport -Destination (Join-Path $outputRoot 'engine-smoke.json')
    Copy-Item -LiteralPath $engineReport -Destination (Join-Path $outputRoot 'frozen-engines-smoke.json')
    if (-not $SkipInstaller) {
        & $IsccPath "/DMyAppVersion=$Version" "/DStageDir=$bundleRoot" "/O$outputRoot" $installerScript *> (Join-Path $workRoot 'inno-setup.log')
        if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed: $workRoot\inno-setup.log" }
        if (-not (Test-Path -LiteralPath (Join-Path $outputRoot "iPadHub-$Version-Setup-x64.exe") -PathType Leaf)) { throw 'Installer output is missing.' }
    }
    $hashLines = foreach ($file in (Get-ChildItem -LiteralPath $outputRoot -File | Where-Object Name -ne 'SHA256SUMS.txt' | Sort-Object Name)) {
        '{0} *{1}' -f (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant(),$file.Name
    }
    [IO.File]::WriteAllLines((Join-Path $outputRoot 'SHA256SUMS.txt'), $hashLines, [Text.UTF8Encoding]::new($false))
    Write-Output "Package complete: $outputRoot"
    Write-Output "Build and validation evidence: $workRoot"
    Write-Output "Old applications, shortcuts, settings, firmware backups and uninstall registrations were not modified."
} finally {
    Pop-Location
    foreach ($name in $environmentNames) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
