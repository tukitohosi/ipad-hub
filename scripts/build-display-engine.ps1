[CmdletBinding()]
param([ValidateRange(1, 32)][int]$Jobs = 8)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$workspaceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$sourcePath = Join-Path $workspaceRoot 'vendor\ipaddisplay'
$buildPath = Join-Path $workspaceRoot 'build\ipadhub-display'
$evidencePath = Join-Path $workspaceRoot 'build\ipadhub-display\evidence'
if (-not (Test-Path -LiteralPath (Join-Path $sourcePath 'CMakeLists.txt'))) {
    throw "Missing imported baseline: $sourcePath"
}
New-Item -ItemType Directory -Path $buildPath,$evidencePath -Force | Out-Null

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path -LiteralPath $vswhere)) { throw 'Visual Studio Installer/vswhere is required.' }
$vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsPath) { throw 'Visual Studio C++ x64 build tools are required.' }
$cmake = Join-Path $vsPath 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
$ctest = Join-Path (Split-Path -Parent $cmake) 'ctest.exe'
if (-not (Test-Path -LiteralPath $cmake) -or -not (Test-Path -LiteralPath $ctest)) {
    throw 'The Visual Studio CMake component is required.'
}

$subst = Join-Path $env:WINDIR 'System32\subst.exe'
$mappedDrive = $null
$buildRoot = $workspaceRoot
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$previousConfig = $env:IPAD_CONNECT_CONFIG_DIR
$locationPushed = $false
$started = (Get-Date).ToUniversalTime().ToString('o')
$buildSucceeded = $false

function Invoke-BuildStep {
    param([string]$Tool, [string[]]$Arguments, [string]$LogName)
    & $Tool @Arguments 2>&1 | Tee-Object -FilePath (Join-Path $evidencePath $LogName)
    if ($LASTEXITCODE -ne 0) { throw "$LogName failed with exit code $LASTEXITCODE" }
}

try {
    # VS-bundled CMake/MSBuild has crashed on the existing non-ASCII workspace
    # path. Keep both source and binary paths ASCII while retaining all bytes
    # within this new workspace. The original project is never referenced.
    if ($workspaceRoot -match '[^\x00-\x7F]') {
        foreach ($letter in @('R','S','T','U','V','W','X','Y','Z')) {
            if (Test-Path -LiteralPath ($letter + ':\')) { continue }
            & $subst ($letter + ':') $workspaceRoot
            if ($LASTEXITCODE -eq 0) {
                $mappedDrive = $letter + ':'
                $buildRoot = $mappedDrive + '\'
                break
            }
        }
        if (-not $mappedDrive) { throw 'No free temporary ASCII build drive is available.' }
    }
    $mappedSource = Join-Path $buildRoot 'vendor\ipaddisplay'
    $mappedBuild = Join-Path $buildRoot 'build\ipadhub-display'
    $testTemp = Join-Path $mappedBuild 'test-temp'
    $testConfig = Join-Path $mappedBuild 'test-config'
    New-Item -ItemType Directory -Path $testTemp,$testConfig -Force | Out-Null
    $env:TEMP = $testTemp
    $env:TMP = $testTemp
    $env:IPAD_CONNECT_CONFIG_DIR = $testConfig
    Push-Location -LiteralPath $buildRoot
    $locationPushed = $true

    [pscustomobject]@{
        StartedAtUtc = $started
        CMake = $cmake
        CMakeVersion = (& $cmake --version | Select-Object -First 1)
        VisualStudio = $vsPath
        Source = $sourcePath
        Build = $buildPath
        MappedSource = $mappedSource
        MappedBuild = $mappedBuild
        Configuration = 'Release'
        Generator = 'Visual Studio 17 2022'
        Architecture = 'x64'
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidencePath 'build-environment.json') -Encoding utf8

    Invoke-BuildStep -Tool $cmake -Arguments @('-S',$mappedSource,'-B',$mappedBuild,'-G','Visual Studio 17 2022','-A','x64','-DBUILD_TESTING=ON') -LogName 'configure.log'
    Invoke-BuildStep -Tool $cmake -Arguments @('--build',$mappedBuild,'--config','Release','--parallel',"$Jobs") -LogName 'build.log'
    Invoke-BuildStep -Tool $ctest -Arguments @('--test-dir',$mappedBuild,'-C','Release','--output-on-failure','--output-junit',(Join-Path $evidencePath 'ctest-results.xml')) -LogName 'ctest.log'

    $executable = Join-Path $buildPath 'Release\iPadHubDisplay.exe'
    if (-not (Test-Path -LiteralPath $executable)) { throw "Expected executable is missing: $executable" }
    Get-ChildItem -LiteralPath (Join-Path $buildPath 'Release') -File -Filter '*.exe' | ForEach-Object {
        [pscustomobject]@{File=$_.Name;Bytes=$_.Length;SHA256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash}
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidencePath 'built-executables-sha256.json') -Encoding utf8
    $buildSucceeded = $true
}
finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    $env:IPAD_CONNECT_CONFIG_DIR = $previousConfig
    if ($locationPushed) { Pop-Location }
    if ($mappedDrive) {
        & $subst $mappedDrive /D
        if ($LASTEXITCODE -ne 0) { Write-Warning "Could not release temporary mapping $mappedDrive" }
    }
    [pscustomobject]@{
        StartedAtUtc = $started
        FinishedAtUtc = (Get-Date).ToUniversalTime().ToString('o')
        BuildAndCTestPassed = $buildSucceeded
        MainApplicationStarted = $false
        InstallerCreated = $false
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidencePath 'build-result.json') -Encoding utf8
}
