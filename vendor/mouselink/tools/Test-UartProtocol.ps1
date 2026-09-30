param([string]$ScratchDirectory = 'K:\runtime\native-temp')
$ErrorActionPreference = 'Stop'
$rootPath = Split-Path -Parent $PSScriptRoot
$vswhere = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
$vsPath = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsPath) { throw 'Visual C++ Build Tools are required.' }
Import-Module (Join-Path $vsPath 'Common7\Tools\Microsoft.VisualStudio.DevShell.dll')
Enter-VsDevShell -VsInstallPath $vsPath -SkipAutomaticLocation -DevCmdArguments '-arch=x64 -host_arch=x64'
$outputPath = Join-Path $rootPath 'test-results\native-protocol-test'
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
New-Item -ItemType Directory -Path $ScratchDirectory -Force | Out-Null
$originalTemp = $env:TEMP
$originalTmp = $env:TMP
$env:TEMP = $ScratchDirectory
$env:TMP = $ScratchDirectory
Push-Location $outputPath
try {
    & cl.exe /nologo /W4 /WX /std:c11 /TC /c "/I$rootPath\open_bridge\main\include" "$rootPath\tests\test_uart_proto.c" "$rootPath\open_bridge\main\src\uart_proto.c"
    if ($LASTEXITCODE -ne 0) { throw 'C protocol compilation failed.' }
    & link.exe /nologo /OUT:uart-test.exe test_uart_proto.obj uart_proto.obj
    if ($LASTEXITCODE -ne 0) { throw 'C protocol linking failed.' }
    & .\uart-test.exe
    if ($LASTEXITCODE -ne 0) { throw 'C protocol assertions failed.' }
    Write-Output 'Firmware serial protocol tests passed.'
} finally {
    Pop-Location
    $env:TEMP = $originalTemp
    $env:TMP = $originalTmp
}
