[CmdletBinding()]
param(
    [string]$Version = '0.1.1-preview',
    [string]$PreviousVersion = '0.1.0-preview',
    [string]$IsccPath
)

# QA recompilation only. Never run a production installer against the real user.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-ffff'
$qaRoot = Join-Path $taskRoot "build\upgrade-qa-$stamp"
$evidenceRoot = Join-Path $taskRoot "artifacts\release-$Version\isolated-upgrade-$stamp"
$qaAppId = '{' + [guid]::NewGuid().ToString().ToUpperInvariant() + '}'
$qaRegistryKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\${qaAppId}_is1"
$formalAppId = '{2F7A9C14-E3D0-4D64-8A12-9B59D3CD7EF1}'
$customInstall = Join-Path $qaRoot 'custom-location\iPadHub'
$defaultInstall = Join-Path $qaRoot 'default-location\iPadHub'
$qaShortcuts = Join-Path $qaRoot 'shortcuts'
$qaData = Join-Path $qaRoot 'data'
$installerScript = Join-Path $taskRoot 'packaging\iPadHub.iss'
$uninstaller = Join-Path $customInstall 'unins000.exe'
$result = [ordered]@{
    ok = $false
    started_at_utc = (Get-Date).ToUniversalTime().ToString('o')
    validation_scope = 'Actual current-user QA-only installers compiled from the same Inno definition and original release payloads. Static production installed applications, uninstall registrations and shortcuts are protected by before/after comparisons. Live user settings are intentionally neither captured nor compared during concurrent user hardware testing. Complete isolated fixture data is compared before/after both installs and uninstall. Installed GUI smoke tests use disposable copies of the fixture, preserve every original UI field and exact engine/backup bytes, and allow added UI defaults and .bak files. This is not a clean-Windows or production-AppId installation test.'
    hardware_commands_sent = @()
    qa_app_id = $qaAppId
    formal_app_id = $formalAppId
    qa_root = $qaRoot
    evidence_root = $evidenceRoot
    rounds = @()
}

function Write-Json($Value, [string]$Path) {
    [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Value -Depth 30) + "`n", [Text.UTF8Encoding]::new($false))
}

function Assert-PhysicalPath([string]$Path, [string]$Within) {
    $absolute = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $scope = [IO.Path]::GetFullPath($Within).TrimEnd('\')
    if (-not $absolute.StartsWith($scope + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escaped authorized scope: $absolute"
    }
    for ($probe = $absolute; $probe; $probe = [IO.Path]::GetDirectoryName($probe)) {
        if (Test-Path -LiteralPath $probe) {
            $item = Get-Item -LiteralPath $probe -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse-point path refused: $probe" }
        }
    }
    return $absolute
}

function Read-RegistryTree([string]$Subkey) {
    $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($Subkey, $false)
    if ($null -eq $key) { return [ordered]@{ path=$Subkey; exists=$false } }
    try {
        $values = @(foreach ($name in ($key.GetValueNames() | Sort-Object)) {
            [ordered]@{ name=$name; kind=$key.GetValueKind($name).ToString(); value=$key.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames) }
        })
        $children = @(foreach ($name in ($key.GetSubKeyNames() | Sort-Object)) { Read-RegistryTree ($Subkey + '\' + $name) })
        return [ordered]@{ path=$Subkey; exists=$true; values=$values; children=$children }
    } finally { $key.Dispose() }
}

function Read-Tree([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return [ordered]@{ path=$Path; exists=$false; files=@() } }
    $items = [Collections.Generic.List[object]]::new()
    $stack = [Collections.Generic.Stack[string]]::new()
    $stack.Push($Path)
    while ($stack.Count) {
        $item = Get-Item -LiteralPath $stack.Pop() -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            $items.Add([ordered]@{ path=$item.FullName; reparse=$true; target=@($item.Target) })
        } elseif ($item.PSIsContainer) {
            $items.Add([ordered]@{ path=$item.FullName; directory=$true })
            foreach ($child in (Get-ChildItem -LiteralPath $item.FullName -Force)) { $stack.Push($child.FullName) }
        } else {
            $items.Add([ordered]@{ path=$item.FullName; bytes=$item.Length; sha256=(Get-FileHash -LiteralPath $item.FullName -Algorithm SHA256).Hash.ToLowerInvariant() })
        }
    }
    return [ordered]@{ path=$Path; exists=$true; files=@($items | Sort-Object { $_.path }) }
}

function Read-ProtectedState {
    $programs = [Environment]::GetFolderPath('Programs')
    $desktop = [Environment]::GetFolderPath('DesktopDirectory')
    $paths = @(
        'D:\AppData\iPadHub', 'D:\AppData\iPad互联',
        (Join-Path $env:LOCALAPPDATA 'Programs\iPadHub'),
        (Join-Path $env:LOCALAPPDATA 'Programs\MouseLink')
    )
    foreach ($name in @('iPadHub','MouseLink','iPad互联')) {
        $paths += Join-Path $programs $name
        $paths += Join-Path $desktop ($name + '.lnk')
    }
    $registry = @()
    foreach ($id in @($formalAppId, '{540E4571-24C8-44AD-83BD-D3277CD75F73}', '{7A9052AA-161C-4EF0-A142-EF4DE52698F5}')) {
        $registry += "Software\Microsoft\Windows\CurrentVersion\Uninstall\${id}_is1"
    }
    return [ordered]@{ files=@($paths | Sort-Object -Unique | ForEach-Object { Read-Tree $_ }); registry=@($registry | ForEach-Object { Read-RegistryTree $_ }) }
}

function Invoke-Hidden([string]$Executable, [string[]]$Arguments, [int]$TimeoutMs=60000) {
    $process = Start-Process -FilePath $Executable -ArgumentList $Arguments -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit($TimeoutMs)) { $process.Kill(); throw "QA process timed out: $Executable" }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "QA process failed ($($process.ExitCode)): $Executable" }
    return $process.ExitCode
}

function Quote-Argument([string]$Value) { return '"' + $Value.Replace('"', '\"') + '"' }

function Assert-ReleaseHash([string]$Directory, [string]$Name) {
    $sums = Get-Content -LiteralPath (Join-Path $Directory 'SHA256SUMS.txt')
    $line = @($sums | Where-Object { $_ -match ('^[0-9a-fA-F]{64} \*' + [regex]::Escape($Name) + '$') })
    if ($line.Count -ne 1) { throw "Release checksum entry missing or ambiguous: $Name" }
    $actual = (Get-FileHash -LiteralPath (Join-Path $Directory $Name) -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $line[0].Substring(0,64).ToLowerInvariant()) { throw "Release checksum mismatch: $Name" }
    return $actual
}

function Read-PayloadManifest([string]$Payload) {
    $manifest = [ordered]@{}
    foreach ($file in (Get-ChildItem -LiteralPath $Payload -Recurse -Force -File | Sort-Object FullName)) {
        if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse payload refused: $($file.FullName)" }
        $manifest[[IO.Path]::GetRelativePath($Payload, $file.FullName).Replace('\','/')] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $manifest
}

function Assert-InstalledPayload($Manifest, [string]$Label) {
    $mismatches = @(foreach ($entry in $Manifest.GetEnumerator()) {
        $file = Join-Path $customInstall $entry.Key
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { "$($entry.Key): missing" }
        elseif ((Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value) { "$($entry.Key): different bytes" }
    })
    Write-Json ([ordered]@{ ok=($mismatches.Count -eq 0); expected_files=$Manifest.Count; mismatches=$mismatches }) (Join-Path $evidenceRoot "$Label-payload-comparison.json")
    if ($mismatches.Count) { throw "$Label installed payload differs from the original portable: $($mismatches -join ', ')" }
}

function Assert-QaData([string]$Label) {
    $current = Read-Tree $qaData | ConvertTo-Json -Depth 30 -Compress
    $expected = $script:seedData | ConvertTo-Json -Depth 30 -Compress
    if ($current -ne $expected) { throw "Isolated user data changed during $Label" }
}

function Assert-SmokeFixture([string]$SmokeProfileRoot) {
    $expected = Get-Content -LiteralPath (Join-Path $qaData 'settings.json') -Raw | ConvertFrom-Json
    $actual = Get-Content -LiteralPath (Join-Path $SmokeProfileRoot 'settings.json') -Raw | ConvertFrom-Json
    foreach ($field in $expected.PSObject.Properties) {
        if ($field.Name -notin $actual.PSObject.Properties.Name -or
            (ConvertTo-Json -InputObject $field.Value -Compress) -ne (ConvertTo-Json -InputObject $actual.($field.Name) -Compress)) {
            throw "Installed application changed an original UI fixture field: $($field.Name)"
        }
    }
    foreach ($relative in @('mouselink\settings.json','display\config.json','firmware-backups\qa-fixture.bin')) {
        $expectedHash = (Get-FileHash -LiteralPath (Join-Path $qaData $relative) -Algorithm SHA256).Hash
        if ($expectedHash -ne (Get-FileHash -LiteralPath (Join-Path $SmokeProfileRoot $relative) -Algorithm SHA256).Hash) {
            throw "Installed application changed isolated engine/backup fixture bytes: $relative"
        }
    }
}

function Assert-QaShortcuts {
    $shell = New-Object -ComObject WScript.Shell
    try {
        foreach ($name in @('StartMenu\iPadHub.lnk','Desktop\iPadHub.lnk')) {
            $shortcutPath = Join-Path $qaShortcuts $name
            if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) { throw "QA shortcut missing: $name" }
            $shortcut = $shell.CreateShortcut($shortcutPath)
            if ($shortcut.TargetPath -ne (Join-Path $customInstall 'iPadHub.exe') -or
                $shortcut.Arguments -ne ('--data-dir "' + $qaData + '" --no-migrate')) { throw "QA shortcut escaped isolated path: $name" }
        }
    } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) }
}

Assert-PhysicalPath $qaRoot (Join-Path $taskRoot 'build') | Out-Null
Assert-PhysicalPath $evidenceRoot (Join-Path $taskRoot 'artifacts') | Out-Null
if (-not $IsccPath) {
    $IsccPath = @((Join-Path $taskRoot '.tools\InnoSetup6\ISCC.exe'), 'D:\Desktop\本地项目\iPad副屏\.tools\InnoSetup6\ISCC.exe', 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe') | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $IsccPath) { throw 'Inno Setup 6 is required.' }
if ((Read-RegistryTree $qaRegistryKey).exists) { throw 'QA uninstall key already exists.' }
$formalText = [IO.File]::ReadAllText($installerScript)
if ($formalText -notmatch '(?m)^AppId=\{\{2F7A9C14-E3D0-4D64-8A12-9B59D3CD7EF1\}\r?$' -or
    $formalText -notmatch '(?m)^PrivilegesRequired=lowest\r?$' -or
    $formalText -match '(?im)^\s*\[(Run|UninstallRun|Registry|Code|InstallDelete|UninstallDelete|INI)\]\s*$') {
    throw 'Unexpected production installer identity or side effects.'
}
New-Item -ItemType Directory -Path $qaRoot,$evidenceRoot,$qaData,(Join-Path $qaRoot 'temp') -Force | Out-Null
$before = Read-ProtectedState
Write-Json $before (Join-Path $evidenceRoot 'protected-before.json')
$processEnvironment = @{}
foreach ($name in @('TEMP','TMP','QT_QPA_PLATFORM')) { $processEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:TEMP = Join-Path $qaRoot 'temp'
    $env:TMP = $env:TEMP
    $env:QT_QPA_PLATFORM = 'offscreen'
    $qaText = $formalText.Replace('#define ProjectRoot AddBackslash(SourcePath) + ".."', '#define ProjectRoot "' + $taskRoot + '"')
    $qaText = $qaText.Replace('AppId={' + $formalAppId, 'AppId={' + $qaAppId)
    $qaText = $qaText.Replace('AppName=iPadHub', 'AppName=iPadHub Isolated Upgrade QA')
    $qaText = $qaText.Replace('AppVerName=iPadHub ', 'AppVerName=iPadHub Isolated Upgrade QA ')
    $qaText = $qaText.Replace('DefaultDirName={localappdata}\Programs\iPadHub', 'DefaultDirName=' + $defaultInstall)
    $qaText = $qaText.Replace('DefaultGroupName=iPadHub', 'DefaultGroupName=iPadHub Isolated Upgrade QA')
    $qaText = $qaText.Replace('{group}', $qaShortcuts + '\StartMenu').Replace('{autodesktop}', $qaShortcuts + '\Desktop')
    $qaText = $qaText.Replace('Filename: "{app}\iPadHub.exe"; WorkingDir:', 'Filename: "{app}\iPadHub.exe"; Parameters: "--data-dir ""' + $qaData + '"" --no-migrate"; WorkingDir:')
    $qaScript = Join-Path $qaRoot 'iPadHub-QA.iss'
    [IO.File]::WriteAllText($qaScript, $qaText, [Text.UTF8Encoding]::new($true))
    Copy-Item -LiteralPath $qaScript -Destination $evidenceRoot
    $result['formal_iss_sha256'] = (Get-FileHash -LiteralPath $installerScript -Algorithm SHA256).Hash.ToLowerInvariant()
    $result['qa_iss_sha256'] = (Get-FileHash -LiteralPath $qaScript -Algorithm SHA256).Hash.ToLowerInvariant()
    $result['qa_only_changes'] = @('AppId and AppName', 'Default installation directory', 'Start menu and desktop shortcut locations', 'Shortcut arguments redirect data and disable migration', 'Absolute ProjectRoot for identical icon/license/language inputs')
    foreach ($name in @('mouselink','display','logs','firmware-backups')) { New-Item -ItemType Directory -Path (Join-Path $qaData $name) -Force | Out-Null }
    Write-Json ([ordered]@{ schema_version=1; theme='dark'; ipad_side='left'; bridge_mode='locked'; speed=75; profile='custom'; custom_fps=60; custom_bitrate=100; qa_upgrade_sentinel=$qaAppId }) (Join-Path $qaData 'settings.json')
    Write-Json ([ordered]@{ calibration=@{ physical_width_mm=123 }; ipad_side='left'; mode='locked'; speed=0.75; qa_upgrade_sentinel=$qaAppId }) (Join-Path $qaData 'mouselink\settings.json')
    Write-Json ([ordered]@{ resolution='1920x1080'; fps=60; bitrateMbps=100; streamProfile=3; qa_upgrade_sentinel=$qaAppId }) (Join-Path $qaData 'display\config.json')
    [IO.File]::WriteAllBytes((Join-Path $qaData 'firmware-backups\qa-fixture.bin'), [Text.Encoding]::UTF8.GetBytes('This is an isolated QA fixture, not device firmware.'))
    $script:seedData = Read-Tree $qaData
    Write-Json $script:seedData (Join-Path $evidenceRoot 'qa-data-before.json')
    foreach ($releaseVersion in @($PreviousVersion, $Version)) {
        $releaseDir = Join-Path $taskRoot "release\$releaseVersion"
        $portableName = "iPadHub-$releaseVersion-Windows-x64.zip"
        $installerName = "iPadHub-$releaseVersion-Setup-x64.exe"
        $portableHash = Assert-ReleaseHash $releaseDir $portableName
        $productionInstallerHash = Assert-ReleaseHash $releaseDir $installerName
        $payloadDir = Join-Path $qaRoot "$releaseVersion-payload"
        Expand-Archive -LiteralPath (Join-Path $releaseDir $portableName) -DestinationPath $payloadDir
        $payload = Join-Path $payloadDir 'iPadHub'
        $payloadManifest = Read-PayloadManifest $payload
        Write-Json $payloadManifest (Join-Path $evidenceRoot "$releaseVersion-original-payload-sha256.json")
        & $IsccPath "/DMyAppVersion=$releaseVersion" "/DStageDir=$payload" "/O$qaRoot" $qaScript *> (Join-Path $evidenceRoot "$releaseVersion-inno-compile.log")
        if ($LASTEXITCODE -ne 0) { throw "QA installer compilation failed: $releaseVersion" }
        $qaInstaller = Join-Path $qaRoot $installerName
        $installArgs = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/TASKS=desktopicon', ('/LOG=' + (Quote-Argument (Join-Path $evidenceRoot "$releaseVersion-install.log"))))
        if ($releaseVersion -eq $PreviousVersion) { $installArgs += '/DIR=' + (Quote-Argument $customInstall) }
        $exitCode = Invoke-Hidden $qaInstaller $installArgs
        $key = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($qaRegistryKey, $false)
        if ($null -eq $key) { throw "QA uninstall registration missing: $releaseVersion" }
        try {
            $registeredPath = $key.GetValue('InstallLocation').TrimEnd('\')
            $registeredVersion = $key.GetValue('DisplayVersion')
        } finally { $key.Dispose() }
        if ($registeredPath -ne $customInstall -or $registeredVersion -ne $releaseVersion) { throw "QA registration/path reuse incorrect: $releaseVersion" }
        if (Test-Path -LiteralPath $defaultInstall) { throw 'Upgrade unexpectedly used the default path.' }
        Assert-InstalledPayload $payloadManifest $releaseVersion
        Assert-QaShortcuts
        Assert-QaData "installation $releaseVersion"
        $smokePath = Join-Path $evidenceRoot "$releaseVersion-installed-smoke.json"
        $smokeData = Join-Path $qaRoot "smoke-data\$releaseVersion"
        New-Item -ItemType Directory -Path $smokeData -Force | Out-Null
        Get-ChildItem -LiteralPath $qaData -Force | Copy-Item -Destination $smokeData -Recurse
        Invoke-Hidden (Join-Path $customInstall 'iPadHub.exe') @('--smoke-test', (Quote-Argument $smokePath), '--data-dir', (Quote-Argument $smokeData), '--no-migrate') | Out-Null
        $smoke = Get-Content -LiteralPath $smokePath -Raw | ConvertFrom-Json
        if (-not $smoke.ok -or $smoke.engine_started -or $smoke.migration_performed) { throw "Installed application smoke failed: $releaseVersion" }
        Assert-SmokeFixture $smokeData
        Assert-QaData "application smoke $releaseVersion"
        $result.rounds += [ordered]@{ version=$releaseVersion; actual_installer_exit_code=$exitCode; production_portable_sha256=$portableHash; production_installer_sha256=$productionInstallerHash; qa_installer_sha256=(Get-FileHash -LiteralPath $qaInstaller -Algorithm SHA256).Hash.ToLowerInvariant(); original_payload_files=$payloadManifest.Count; installed_payload_matches=$true; registered_path=$registeredPath; registered_version=$registeredVersion; dir_argument_supplied=($releaseVersion -eq $PreviousVersion); shortcuts_isolated=$true; application_smoke_passed=$true; application_smoke_data_root=$smokeData; original_fixture_fields_preserved_on_launch=$true; fixture_data_preserved=$true }
    }
    $uninstallFiles = @(Get-ChildItem -LiteralPath $customInstall -Recurse -File -Force)
    $uninstallBytes = ($uninstallFiles | Measure-Object Length -Sum).Sum
    Invoke-Hidden $uninstaller @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART', ('/LOG=' + (Quote-Argument (Join-Path $evidenceRoot 'uninstall.log')))) | Out-Null
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    while ((Test-Path -LiteralPath $customInstall) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 200 }
    if ((Read-RegistryTree $qaRegistryKey).exists -or (Test-Path -LiteralPath $customInstall)) { throw 'QA uninstall left its registration or installed directory.' }
    if (@(Get-ChildItem -LiteralPath $qaShortcuts -File -Recurse -ErrorAction SilentlyContinue).Count) { throw 'QA uninstall left shortcuts.' }
    Assert-QaData 'uninstallation'
    $result['uninstall'] = [ordered]@{ registration_removed=$true; installed_directory_removed=$true; shortcuts_removed=$true; fixture_data_preserved=$true; temporary_installed_files_removed=$uninstallFiles.Count; temporary_installed_bytes_removed=$uninstallBytes }
    $result['ok'] = $true
} catch {
    $result['error'] = $_.Exception.Message
    # If a failed round installed the isolated identity, remove only that install.
    if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
        try { Invoke-Hidden $uninstaller @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART', ('/LOG=' + (Quote-Argument (Join-Path $evidenceRoot 'failure-cleanup-uninstall.log')))) | Out-Null }
        catch { $result['failure_cleanup_error'] = $_.Exception.Message }
    }
} finally {
    foreach ($name in $processEnvironment.Keys) { [Environment]::SetEnvironmentVariable($name, $processEnvironment[$name], 'Process') }
    $after = Read-ProtectedState
    Write-Json $after (Join-Path $evidenceRoot 'protected-after.json')
    $unchanged = (ConvertTo-Json -InputObject $before -Depth 30 -Compress) -eq (ConvertTo-Json -InputObject $after -Depth 30 -Compress)
    $result['production_state_unchanged'] = $unchanged
    $result['production_state_comparison_scope'] = 'Static installed application files, three named uninstall registrations, and product shortcuts only; live user settings were not read or persisted.'
    $result['qa_registration_removed'] = -not (Read-RegistryTree $qaRegistryKey).exists
    if (-not $unchanged -or -not $result['qa_registration_removed']) { $result['ok'] = $false }
    $result['finished_at_utc'] = (Get-Date).ToUniversalTime().ToString('o')
    Write-Json $result (Join-Path $evidenceRoot 'result.json')
    $result | ConvertTo-Json -Depth 10
}
if (-not $result['ok']) { throw "Isolated upgrade validation failed; see $evidenceRoot\result.json" }
