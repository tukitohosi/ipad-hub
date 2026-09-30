param([string]$CompilerPath, [string]$Python)
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
if (-not $CompilerPath) { $CompilerPath = Join-Path $workspacePath 'runtime\installer-tools\InnoSetup\ISCC.exe' }
if (-not (Test-Path -LiteralPath $CompilerPath -PathType Leaf)) { throw 'Install Inno Setup and provide -CompilerPath to ISCC.exe.' }
$CompilerPath = (Resolve-Path -LiteralPath $CompilerPath).ProviderPath
& $CompilerPath "/DAppVersion=$appVersion" (Join-Path $PSScriptRoot 'MouseLink.iss')
if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
