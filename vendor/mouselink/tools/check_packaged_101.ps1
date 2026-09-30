param(
    [Parameter(Mandatory)][string]$AppPath,
    [switch]$FlashFromUI,
    [switch]$CloseDuringFlash
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
$workspacePath = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $workspacePath 'test-results\1.0.1-packaged-state'
New-Item -ItemType Directory -Path (Join-Path $statePath 'MouseLink') -Force | Out-Null
$startInfo = [Diagnostics.ProcessStartInfo]::new()
$startInfo.FileName = (Resolve-Path -LiteralPath $AppPath).Path
$startInfo.UseShellExecute = $false
$startInfo.EnvironmentVariables['LOCALAPPDATA'] = $statePath
$startInfo.EnvironmentVariables['PATH'] = $env:SystemRoot + '\System32;' + $env:SystemRoot
$startInfo.EnvironmentVariables['PYTHONPATH'] = ''
$appProcess = [Diagnostics.Process]::Start($startInfo)
$desktop = [Windows.Automation.AutomationElement]::RootElement
$processCondition = [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ProcessIdProperty, $appProcess.Id)
$deadline = [DateTime]::UtcNow.AddSeconds(15)
$mainWindow = $null
while ([DateTime]::UtcNow -lt $deadline -and -not $mainWindow) {
    $mainWindow = $desktop.FindFirst([Windows.Automation.TreeScope]::Children, $processCondition)
    Start-Sleep -Milliseconds 100
}
if (-not $mainWindow) { throw 'Packaged window did not appear.' }
$items = @()
$all = $mainWindow.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)
foreach ($item in $all) {
    if ($item.Current.Name) { $items += [PSCustomObject]@{Name=$item.Current.Name; Type=$item.Current.ControlType.ProgrammaticName; Enabled=$item.Current.IsEnabled} }
}
$items | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $workspacePath 'test-results\1.0.1-packaged-ui.json') -Encoding utf8
function Find-Button([string]$Name) {
    $condition = [Windows.Automation.AndCondition]::new($processCondition,
        [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::NameProperty, $Name),
        [Windows.Automation.PropertyCondition]::new([Windows.Automation.AutomationElement]::ControlTypeProperty, [Windows.Automation.ControlType]::Button))
    return $desktop.FindFirst([Windows.Automation.TreeScope]::Descendants, $condition)
}
if ($FlashFromUI) {
    $closeRequested = $false
    $entry = Find-Button '一键刷机'
    if (-not $entry) { throw 'Flash entry not found.' }
    $entry.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    $button = $null
    while ([DateTime]::UtcNow -lt $deadline -and -not $button) {
        $button = Find-Button '备份并刷入'
        Start-Sleep -Milliseconds 100
    }
    if (-not $button -or -not $button.Current.IsEnabled) { throw 'Flash button not ready.' }
    $flashStartedAt = [DateTime]::UtcNow
    $button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
    $logsPath = Join-Path $statePath 'MouseLink\flash-logs'
    $deadline = [DateTime]::UtcNow.AddSeconds(160)
    $result = $null
    while ([DateTime]::UtcNow -lt $deadline -and -not $result) {
        $latest = Get-ChildItem -LiteralPath $logsPath -Filter '*.jsonl' -ErrorAction SilentlyContinue | Where-Object LastWriteTimeUtc -GE $flashStartedAt | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if ($latest) {
            foreach ($line in (Get-Content -LiteralPath $latest.FullName -Encoding utf8)) {
                try { $event = $line | ConvertFrom-Json } catch { continue }
                if ($CloseDuringFlash -and -not $closeRequested -and $event.event -eq 'phase' -and $event.phase -eq 'write') {
                    $appProcess.Refresh()
                    if (-not $appProcess.CloseMainWindow()) { throw 'Close request was not delivered to the main window.' }
                    $closeRequested = $true
                }
                if ($event.event -eq 'result') { $result = $event }
            }
        }
        Start-Sleep -Milliseconds 150
    }
    if (-not $result.ok -or -not $result.ready) { throw ('Packaged UI flash failed: ' + ($result | ConvertTo-Json -Compress)) }
    Write-Output ($result | ConvertTo-Json -Compress)
    if (-not $CloseDuringFlash) {
        $returnButton = Find-Button '返回'
        $returnButton.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
        Start-Sleep -Milliseconds 250
    } elseif (-not $closeRequested) { throw 'Close during write was not exercised.' }
}
$appProcess.Refresh()
$timer = [Diagnostics.Stopwatch]::StartNew()
if (-not $appProcess.HasExited) { $null = $appProcess.CloseMainWindow() }
if (-not $appProcess.WaitForExit(4000)) { throw 'Packaged application did not close.' }
$timer.Stop()
$record = @{app=$startInfo.FileName; flashFromUI=[bool]$FlashFromUI; closeDuringFlash=[bool]$CloseDuringFlash; exitCode=$appProcess.ExitCode; closeMilliseconds=$timer.ElapsedMilliseconds; statePath=$statePath}
$suffix = if ($CloseDuringFlash) { 'close-during-flash' } elseif ($FlashFromUI) { 'flash' } else { 'close' }
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $workspacePath ("test-results\1.0.1-packaged-$suffix.json")) -Encoding utf8
$record | ConvertTo-Json -Compress | Write-Output
