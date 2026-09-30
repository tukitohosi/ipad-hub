[CmdletBinding()]
param()

# Read-only source capture: all writes stay under this project's local baseline.
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$CaptureRoot = Join-Path $ProjectRoot ('artifacts\baseline\system\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
if (Test-Path -LiteralPath $CaptureRoot) { throw 'Capture folder already exists.' }
New-Item -ItemType Directory -Path $CaptureRoot -Force | Out-Null
$Issues = [System.Collections.Generic.List[object]]::new()

function Write-Json($Value, [string]$Path) {
    ConvertTo-Json -InputObject $Value -Depth 20 | Set-Content -LiteralPath $Path -Encoding utf8
}

function Get-FileRecord([string]$Path) {
    $item = Get-Item -LiteralPath $Path
    [pscustomobject]@{
        Path = $item.FullName
        Length = $item.Length
        LastWriteTimeUtc = $item.LastWriteTimeUtc.ToString('o')
        SHA256 = (Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash
    }
}

function Capture-Tree([string]$Label, [string]$Source) {
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        return [pscustomobject]@{ Label=$Label; Source=$Source; Exists=$false; FileCount=0; Bytes=0; AllVerified=$true; Files=@() }
    }
    $destination = Join-Path $CaptureRoot ('data\' + $Label)
    New-Item -ItemType Directory -Path $destination -Force | Out-Null
    $files = @(Get-ChildItem -LiteralPath $Source -Recurse -Force -File | Sort-Object FullName)
    $records = foreach ($file in $files) {
        $relative = $file.FullName.Substring($Source.TrimEnd('\').Length + 1)
        if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            $Issues.Add([pscustomobject]@{Kind='SkippedReparsePoint';Path=$file.FullName})
            continue
        }
        $before = Get-FileRecord $file.FullName
        $target = Join-Path $destination $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $target
        $copy = Get-FileRecord $target
        $after = Get-FileRecord $file.FullName
        $verified = $before.SHA256 -eq $copy.SHA256 -and $before.SHA256 -eq $after.SHA256
        if (-not $verified) { $Issues.Add([pscustomobject]@{Kind='SourceChangedOrCopyMismatch';Path=$file.FullName}) }
        [pscustomobject]@{RelativePath=$relative;Length=$before.Length;BeforeSHA256=$before.SHA256;BackupSHA256=$copy.SHA256;AfterSHA256=$after.SHA256;Verified=$verified}
    }
    $afterPaths = @(Get-ChildItem -LiteralPath $Source -Recurse -Force -File | Select-Object -ExpandProperty FullName | Sort-Object)
    $initialPaths = @($files.FullName | Sort-Object)
    $pathChanges = @(Compare-Object -ReferenceObject $initialPaths -DifferenceObject $afterPaths)
    if ($pathChanges.Count) { $Issues.Add([pscustomobject]@{Kind='SourceFileListChanged';Path=$Source;Changes=$pathChanges}) }
    [pscustomobject]@{Label=$Label;Source=$Source;Exists=$true;FileCount=@($records).Count;Bytes=($records | Measure-Object -Property Length -Sum).Sum;AllVerified=(@($records | Where-Object {-not $_.Verified}).Count -eq 0 -and $pathChanges.Count -eq 0);Files=@($records)}
}

function Read-AppRegistry([string]$SubKey) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($SubKey, $false)
    if ($null -eq $key) { return [pscustomobject]@{SubKey=$SubKey;Exists=$false;Values=@()} }
    try {
        $values = @(foreach ($name in ($key.GetValueNames() | Sort-Object)) {
            [pscustomobject]@{Name=$name;Kind=$key.GetValueKind($name).ToString();Value=$key.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)}
        })
        [pscustomobject]@{SubKey=$SubKey;Exists=$true;Values=$values}
    } finally { $key.Dispose() }
}

$shortcutDefinitions = @(
    @{Name='MouseLink';Path='D:\Desktop\MouseLink.lnk';Reference='D:\Desktop\本地项目\外设复用\release\1.0.3\MouseLink\MouseLink.exe'},
    @{Name='iPadConnect';Path='D:\Desktop\iPad互联.lnk';Reference='D:\Desktop\本地项目\iPad副屏\dist\iPad互联-0.3.0-preview-portable.exe'}
)
$shell = New-Object -ComObject WScript.Shell
$shortcuts = @(foreach ($definition in $shortcutDefinitions) {
    $shortcut = $shell.CreateShortcut($definition.Path)
    $target = $shortcut.TargetPath
    $installed = Get-FileRecord $target
    $reference = Get-FileRecord $definition.Reference
    $version = (Get-Item -LiteralPath $target).VersionInfo
    $shortcutFolder = Join-Path $CaptureRoot 'shortcuts'
    New-Item -ItemType Directory -Path $shortcutFolder -Force | Out-Null
    Copy-Item -LiteralPath $definition.Path -Destination $shortcutFolder
    [pscustomobject]@{
        Name=$definition.Name;Shortcut=$definition.Path;Target=$target;Arguments=$shortcut.Arguments;WorkingDirectory=$shortcut.WorkingDirectory
        ProductVersion=$version.ProductVersion;FileVersion=$version.FileVersion;Installed=$installed;Reference=$reference
        InstalledMatchesReference=($installed.SHA256 -eq $reference.SHA256)
        ShortcutSHA256=(Get-FileHash -LiteralPath $definition.Path -Algorithm SHA256).Hash
    }
})

$releasePaths = @(
    'D:\Desktop\本地项目\外设复用\release\1.0.3\MouseLink-1.0.3-Setup.exe',
    'D:\Desktop\本地项目\外设复用\release\1.0.3\MouseLink-1.0.3-Windows-x64.zip',
    'D:\Desktop\本地项目\外设复用\release\1.0.3\MouseLink-1.0.3-source.zip',
    'D:\Desktop\本地项目\外设复用\release\1.0.3\SHA256.txt',
    'D:\Desktop\本地项目\iPad副屏\dist\iPad互联-0.3.0-preview-Setup-x64.exe',
    'D:\Desktop\本地项目\iPad副屏\dist\iPad互联-source-0.3.0-preview.zip',
    'D:\Desktop\本地项目\iPad副屏\dist\SHA256SUMS-0.3.0-preview.txt'
)
$releases = @($releasePaths | ForEach-Object {Get-FileRecord $_})

$registry = @(foreach ($subkey in @('Software\MouseLink','Software\opendisplay-win')) {
    $before = Read-AppRegistry $subkey
    $baseName = $subkey.Replace('\','-')
    $exportPath = Join-Path $CaptureRoot ($baseName + '.reg')
    if ($before.Exists) {
        $exportOutput = & reg.exe export ('HKCU\' + $subkey) $exportPath /y 2>&1
        if ($LASTEXITCODE -ne 0) { throw ('Registry export failed: ' + ($exportOutput -join ' ')) }
    }
    $after = Read-AppRegistry $subkey
    $unchanged = (ConvertTo-Json -InputObject $before -Depth 10 -Compress) -eq (ConvertTo-Json -InputObject $after -Depth 10 -Compress)
    if (-not $unchanged) {$Issues.Add([pscustomobject]@{Kind='RegistryChangedDuringCapture';Path=$subkey})}
    [pscustomobject]@{Before=$before;After=$after;Unchanged=$unchanged;ExportSHA256=$(if($before.Exists){(Get-FileHash -LiteralPath $exportPath -Algorithm SHA256).Hash}else{$null})}
})

$data = @(
    (Capture-Tree 'mouselink-local' (Join-Path $env:LOCALAPPDATA 'MouseLink')),
    (Capture-Tree 'ipadconnect-roaming' (Join-Path $env:APPDATA 'MouseLink')),
    (Capture-Tree 'opendisplay-win-legacy-roaming' (Join-Path $env:APPDATA 'opendisplay-win'))
)

# Only matching application metadata is retained; no unrelated registry values.
$uninstall = @(foreach ($base in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall','HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
    if (Test-Path -LiteralPath $base) {
        foreach ($entry in (Get-ChildItem -LiteralPath $base)) {
            $properties = Get-ItemProperty -LiteralPath $entry.PSPath
            if ($properties.DisplayName -match '^(MouseLink|iPad互联)(\s|$)') {
                [pscustomobject]@{Key=$entry.Name;DisplayName=$properties.DisplayName;DisplayVersion=$properties.DisplayVersion;InstallLocation=$properties.InstallLocation;DisplayIcon=$properties.DisplayIcon;UninstallString=$properties.UninstallString}
            }
        }
    }
})
$processes = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @('MouseLink.exe','iPad互联.exe','MouseLinkFlash.exe','opendisplay-win.exe') -or
    $_.ExecutablePath -like 'D:\AppData\MouseLink\*' -or $_.ExecutablePath -like 'D:\AppData\iPad互联\*'
} | Select-Object ProcessId,Name,ExecutablePath)

$summary = [pscustomobject]@{
    SchemaVersion=1;CapturedAtUtc=(Get-Date).ToUniversalTime().ToString('o');CaptureRoot=$CaptureRoot
    Scope='Read-only capture of the two named application installations, user data and HKCU display-position keys. No device access, migration or process termination.'
    Shortcuts=$shortcuts;ReleaseFiles=$releases;Data=$data;Registry=$registry;Uninstall=$uninstall;RunningProcesses=$processes
    Disk=@(Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Name -in @('C','D')} | Select-Object Name,Free,Used)
    Issues=@($Issues)
}
Write-Json $summary (Join-Path $CaptureRoot 'system-baseline.json')
$artifactHashes = @(Get-ChildItem -LiteralPath $CaptureRoot -Recurse -File | Sort-Object FullName | ForEach-Object {
    [pscustomobject]@{Path=$_.FullName.Substring($CaptureRoot.Length+1);Length=$_.Length;SHA256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
})
Write-Json $artifactHashes (Join-Path $CaptureRoot 'backup-hashes.json')
[pscustomobject]@{CaptureRoot=$CaptureRoot;DataFileCount=($data | Measure-Object -Property FileCount -Sum).Sum;BackupDataBytes=($data | Measure-Object -Property Bytes -Sum).Sum;AllDataVerified=(@($data | Where-Object {-not $_.AllVerified}).Count -eq 0);RegistryUnchanged=(@($registry | Where-Object {-not $_.Unchanged}).Count -eq 0);InstalledMatchesReferences=(@($shortcuts | Where-Object {-not $_.InstalledMatchesReference}).Count -eq 0);Issues=$Issues.Count} | ConvertTo-Json
