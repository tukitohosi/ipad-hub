$ErrorActionPreference = 'Stop'
$workspacePath = Split-Path -Parent $PSScriptRoot
$setupPath = Join-Path $workspacePath 'release\1.0.1\MouseLink-1.0.1-Setup.exe'
$installRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\MouseLink'))
$dataRoot = Join-Path $env:LOCALAPPDATA 'MouseLink'
$expectedApp = Join-Path $workspacePath 'release\1.0.1\MouseLink\MouseLink.exe'
$installedApp = Join-Path $installRoot 'MouseLink.exe'
$records = @()
$dataBefore = @{}
Get-ChildItem -LiteralPath $dataRoot -Recurse -File | ForEach-Object { $dataBefore[$_.FullName] = (Get-FileHash -LiteralPath $_.FullName).Hash }

function Check-Data {
    foreach ($path in $dataBefore.Keys) {
        if (-not (Test-Path -LiteralPath $path) -or (Get-FileHash -LiteralPath $path).Hash -ne $dataBefore[$path]) { throw "User data changed: $path" }
    }
}
function Install([string]$Stage) {
    $logPath = Join-Path $workspacePath "test-results\1.0.1-install-$Stage.log"
    $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /LOG="' + $logPath + '"'
    $setupProcess = Start-Process -FilePath $setupPath -ArgumentList $arguments -WindowStyle Hidden -PassThru -Wait
    if ($setupProcess.ExitCode -ne 0) { throw "Installer failed: $($setupProcess.ExitCode)" }
    if ((Get-FileHash -LiteralPath $installedApp).Hash -ne (Get-FileHash -LiteralPath $expectedApp).Hash) { throw 'Installed app hash mismatch' }
    Check-Data
}

Install 'upgrade'
$records += @{stage='upgrade';dataPreserved=$true}
$uninstaller = [IO.Path]::GetFullPath((Join-Path $installRoot 'unins000.exe'))
if (-not $uninstaller.StartsWith($installRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected uninstall location' }
$registered = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{540E4571-24C8-44AD-83BD-D3277CD75F73}_is1'
if ([IO.Path]::GetFullPath($registered.InstallLocation).TrimEnd('\') -ne $installRoot) { throw 'Uninstall registration mismatch' }
$uninstallProcess = Start-Process -FilePath $uninstaller -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART' -WindowStyle Hidden -PassThru -Wait
if ($uninstallProcess.ExitCode -ne 0) { throw 'Uninstall failed' }
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while ((Test-Path -LiteralPath $installedApp) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
if (Test-Path -LiteralPath $installedApp) { throw 'Installed app remains after uninstall' }
Check-Data
$records += @{stage='uninstall';dataPreserved=$true;removed=$true}
Install 'reinstall'
$shortcutPaths = @((Join-Path ([Environment]::GetFolderPath('Desktop')) 'MouseLink.lnk'),
    (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\MouseLink\MouseLink.lnk'))
$shellObject = New-Object -ComObject WScript.Shell
foreach ($shortcutPath in $shortcutPaths) {
    if (-not (Test-Path -LiteralPath $shortcutPath)) { throw "Shortcut missing: $shortcutPath" }
    if ($shellObject.CreateShortcut($shortcutPath).TargetPath -ne $installedApp) { throw 'Wrong shortcut target' }
}
$records += @{stage='reinstall';dataPreserved=$true;shortcuts=$shortcutPaths;fileVersion=(Get-Item -LiteralPath $installedApp).VersionInfo.FileVersion}
$records | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $workspacePath 'test-results\1.0.1-installer-acceptance.json') -Encoding utf8
$records | ConvertTo-Json -Depth 5
