$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (Test-Path -LiteralPath $pythonPath -PathType Leaf) {
    $program = $pythonPath
    $arguments = @('-m', 'ipadhub.app', '--preview')
} else {
    $appKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{2F7A9C14-E3D0-4D64-8A12-9B59D3CD7EF1}_is1'
    $installed = Get-ItemProperty -LiteralPath $appKey -ErrorAction SilentlyContinue
    if ($installed -and $installed.DisplayVersion -eq '0.1.1-preview') {
        $program = Join-Path $installed.InstallLocation 'iPadHub.exe'
    }
    if (-not $program -or -not (Test-Path -LiteralPath $program -PathType Leaf)) {
        throw 'Install the app from release\0.1.1-preview or recreate the development environment as described in docs\packaging.md.'
    }
    $arguments = @('--preview')
}
$process = Start-Process -FilePath $program -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
Write-Output ('iPadHub preview started; PID=' + $process.Id)
