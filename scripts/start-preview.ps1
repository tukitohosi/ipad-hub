$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Preview environment is missing. See README.md for setup.'
}
$process = Start-Process -FilePath $pythonPath -ArgumentList '-m', 'ipadhub.app', '--preview' -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
Write-Output ('iPadHub preview started; PID=' + $process.Id)
