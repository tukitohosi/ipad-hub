param([string]$Python)
$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    foreach ($candidate in @('runtime\build-venv\Scripts\python.exe', '.venv\Scripts\python.exe')) {
        $candidatePath = Join-Path $workspacePath $candidate
        if (Test-Path -LiteralPath $candidatePath -PathType Leaf) {
            $Python = $candidatePath
            break
        }
    }
    if (-not $Python) { $Python = 'python' }
}
$pythonPath = (Get-Command $Python -CommandType Application -ErrorAction Stop).Source
$appVersion = (& $pythonPath -c "import runpy,sys; print(runpy.run_path(sys.argv[1])['APP_VERSION'])" `
    (Join-Path $workspacePath 'open_bridge\app_version.py')).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Cannot read the application version with the selected Python.' }
$releaseRoot = [IO.Path]::GetFullPath((Join-Path $workspacePath "release\$appVersion"))
$workRoot = [IO.Path]::GetFullPath((Join-Path $workspacePath 'runtime\desktop-build'))
if (-not $releaseRoot.StartsWith($workspacePath + '\', [StringComparison]::OrdinalIgnoreCase) -or
    -not $workRoot.StartsWith($workspacePath + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Generated output must remain inside this workspace.'
}
New-Item -ItemType Directory -Path $releaseRoot,$workRoot -Force | Out-Null
$previousPath = $env:PATH
$previousCache = $env:PYINSTALLER_CONFIG_DIR
# Do not let unrelated PATH tools supply same-named DLLs (e.g. Poppler ICU).
$env:PATH = (Split-Path -Parent $pythonPath) + ';' + $env:SystemRoot + '\System32;' + $env:SystemRoot
$env:PYINSTALLER_CONFIG_DIR = Join-Path $workRoot 'pyinstaller-cache'
Push-Location (Join-Path $workspacePath 'open_bridge')
try {
    & $pythonPath -c "from PySide6.QtWidgets import QApplication; from desktop_app import make_icon; from pathlib import Path; app=QApplication([]); Path('assets').mkdir(exist_ok=True); assert make_icon().pixmap(256,256).save('assets/mouselink.ico')"
    if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }
    $iconPath = Join-Path $workspacePath 'open_bridge\assets\mouselink.ico'
    & $pythonPath 'flash_helper.py' --self-check
    if ($LASTEXITCODE -ne 0) { throw 'Firmware bundle validation failed.' }
    & $pythonPath -m PyInstaller --clean --noconfirm --distpath $releaseRoot --workpath $workRoot `
        (Join-Path $PSScriptRoot 'MouseLink.spec')
    if ($LASTEXITCODE -ne 0) { throw 'Desktop packaging failed.' }
    $applicationPath = Join-Path $releaseRoot 'MouseLink'
    foreach ($name in @('使用说明.txt','THIRD_PARTY_NOTICES.md','LICENSE')) {
        Copy-Item -LiteralPath $name -Destination $applicationPath -Force
    }
    if (Test-Path -LiteralPath 'licenses') {
        Copy-Item -LiteralPath 'licenses' -Destination $applicationPath -Recurse -Force
    }
    & (Join-Path $applicationPath 'MouseLinkFlash.exe') --self-check
    if ($LASTEXITCODE -ne 0) { throw 'Bundled flasher validation failed.' }
    Write-Output (Join-Path $applicationPath 'MouseLink.exe')
} finally {
    Pop-Location
    $env:PATH = $previousPath
    $env:PYINSTALLER_CONFIG_DIR = $previousCache
}
