[CmdletBinding()]
param(
    [switch]$SkipHardwareCheck
)

$ErrorActionPreference = 'Stop'

$workspaceRoot = Split-Path -Parent $PSScriptRoot
$relativeAppDirectory = 'third_party\apps\dshare-hid-v1.26.0\dshare-hid-1.26.0.9999-win-x64-portable'
$appDirectory = Join-Path $workspaceRoot $relativeAppDirectory
$executable = Join-Path $appDirectory 'dshare-hid.exe'
$expectedSha256 = '992974DFE51431E419E0D4273D3D13D60AA97546A2513ED0E965D146926658B3'

if (-not (Test-Path -LiteralPath $executable)) {
    throw "DShare-HID executable not found: $executable"
}

$actualSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $executable).Hash.ToUpperInvariant()
if ($actualSha256 -ne $expectedSha256) {
    throw 'DShare-HID executable hash does not match the reviewed v1.26.0 package.'
}

if (-not $SkipHardwareCheck) {
    $candidateDevices = @(
        Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
            Where-Object {
                $_.FriendlyName -match 'ESP32|CP210|CH340|USB JTAG|USB Serial' -or
                $_.InstanceId -match 'VID_303A|VID_10C4|VID_1A86'
            }
    )

    if ($candidateDevices.Count -eq 0) {
        [Console]::Error.WriteLine('No ESP32-C3 or common USB serial device was detected. Connect the board with a data cable and try again.')
        exit 2
    }

    Write-Host 'Detected candidate hardware:'
    $candidateDevices | Select-Object Status, FriendlyName, InstanceId | Format-Table -AutoSize
}

# DShare-HID v1.26.0 converts the certificate path with QString::toStdString()
# before constructing std::filesystem::path. On Windows this corrupts paths
# containing non-ASCII characters and TLS certificate generation fails. Keep
# the files in this workspace, but launch through an ASCII-only SUBST path.
$launchWorkspaceRoot = $workspaceRoot
if ($workspaceRoot -match '[^\x00-\x7F]') {
    $mappedDrive = $null

    foreach ($driveLetter in @('Z', 'Y', 'X', 'W', 'V', 'U', 'T')) {
        $driveRoot = "${driveLetter}:\"
        $candidateExecutable = Join-Path (Join-Path $driveRoot $relativeAppDirectory) 'dshare-hid.exe'

        if (Test-Path -LiteralPath $driveRoot) {
            if (Test-Path -LiteralPath $candidateExecutable) {
                $candidateHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $candidateExecutable).Hash.ToUpperInvariant()
                if ($candidateHash -eq $expectedSha256) {
                    $mappedDrive = $driveRoot
                    break
                }
            }
            continue
        }

        & subst.exe "${driveLetter}:" $workspaceRoot
        if (($LASTEXITCODE -eq 0) -and (Test-Path -LiteralPath $candidateExecutable)) {
            $candidateHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $candidateExecutable).Hash.ToUpperInvariant()
            if ($candidateHash -eq $expectedSha256) {
                $mappedDrive = $driveRoot
                break
            }
        }
    }

    if (-not $mappedDrive) {
        throw 'Unable to create or reuse an ASCII-only drive mapping for DShare-HID.'
    }

    $launchWorkspaceRoot = $mappedDrive
    Write-Host "Using ASCII-only launch path: $launchWorkspaceRoot"
}

$launchAppDirectory = Join-Path $launchWorkspaceRoot $relativeAppDirectory
$launchExecutable = Join-Path $launchAppDirectory 'dshare-hid.exe'
$runtimeRoot = Join-Path $launchWorkspaceRoot 'runtime\dshare-hid'
$localAppData = Join-Path $runtimeRoot 'localappdata'
$roamingAppData = Join-Path $runtimeRoot 'appdata'
$temporaryData = Join-Path $runtimeRoot 'temp'

foreach ($directory in @($runtimeRoot, $localAppData, $roamingAppData, $temporaryData)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

$previousEnvironment = @{
    LOCALAPPDATA = $env:LOCALAPPDATA
    APPDATA = $env:APPDATA
    TEMP = $env:TEMP
    TMP = $env:TMP
}

try {
    $env:LOCALAPPDATA = $localAppData
    $env:APPDATA = $roamingAppData
    $env:TEMP = $temporaryData
    $env:TMP = $temporaryData

    $process = Start-Process -FilePath $launchExecutable -WorkingDirectory $launchAppDirectory -PassThru
    Write-Host "DShare-HID started. Process ID: $($process.Id)"
    Write-Host "Runtime data directory: $(Join-Path $workspaceRoot 'runtime\dshare-hid')"
}
finally {
    $env:LOCALAPPDATA = $previousEnvironment.LOCALAPPDATA
    $env:APPDATA = $previousEnvironment.APPDATA
    $env:TEMP = $previousEnvironment.TEMP
    $env:TMP = $previousEnvironment.TMP
}
