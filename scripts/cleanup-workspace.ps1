[CmdletBinding()]
param(
    [string]$Version = '0.1.1-preview',
    [switch]$Execute,
    [string]$PlanFile,
    [string]$ValidationReport
)

# Planning is read-only. Execute is called only after explicit CLEAN authorization.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$workspaceRoot = Split-Path -Parent $taskRoot
$planB = Join-Path $workspaceRoot '方案B-iPadHub2'
$evidenceRoot = Join-Path $taskRoot "artifacts\release-$Version\cleanup"
$releaseRoot = Join-Path $taskRoot "release\$Version"
New-Item -ItemType Directory -Path $evidenceRoot -Force | Out-Null

function Write-Json($Value, [string]$Path) {
    [IO.File]::WriteAllText($Path, (ConvertTo-Json -InputObject $Value -Depth 30) + "`n", [Text.UTF8Encoding]::new($false))
}

function Assert-PhysicalPath([string]$Path) {
    $absolute = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    $allowed = $absolute.StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $absolute -eq $planB -or $absolute.StartsWith($planB + '\', [StringComparison]::OrdinalIgnoreCase) -or
        $absolute -eq (Join-Path $workspaceRoot 'separate-plans.ps1')
    if (-not $allowed -or $absolute -eq $taskRoot -or $absolute -match '(?i)\\\.git(?:\\|$)') {
        throw "Deletion path is outside the authorized scope: $absolute"
    }
    for ($probe=$absolute; $probe; $probe=[IO.Path]::GetDirectoryName($probe)) {
        if (Test-Path -LiteralPath $probe) {
            $item = Get-Item -LiteralPath $probe -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Deletion through a reparse point refused: $probe" }
        }
    }
    if (Test-Path -LiteralPath $absolute) {
        $resolved = (Resolve-Path -LiteralPath $absolute).ProviderPath.TrimEnd('\')
        if ($resolved -ne $absolute) { throw "Resolved deletion path differs: $absolute -> $resolved" }
    }
    return $absolute
}

function Read-PhysicalTree([string]$Path) {
    $records = [Collections.Generic.List[object]]::new()
    if (-not (Test-Path -LiteralPath $Path)) { return @() }
    $stack = [Collections.Generic.Stack[string]]::new()
    $stack.Push($Path)
    while ($stack.Count) {
        $item = Get-Item -LiteralPath $stack.Pop() -Force
        $isReparse = [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
        $records.Add([pscustomobject]@{ path=$item.FullName; directory=$item.PSIsContainer; reparse=$isReparse; bytes=$(if ($item.PSIsContainer -or $isReparse) { 0 } else { $item.Length }) })
        if ($item.PSIsContainer -and -not $isReparse) {
            foreach ($child in (Get-ChildItem -LiteralPath $item.FullName -Force)) { $stack.Push($child.FullName) }
        }
    }
    return @($records | Sort-Object path)
}

function Get-Stats([string]$Path) {
    $records = @(Read-PhysicalTree $Path)
    return [ordered]@{ path=$Path; files=@($records | Where-Object { -not $_.directory -and -not $_.reparse }).Count; bytes=($records | Measure-Object bytes -Sum).Sum; reparse_points=@($records | Where-Object reparse | Select-Object -ExpandProperty path) }
}

function Get-Candidates {
    $candidates = [Collections.Generic.List[object]]::new()
    foreach ($entry in @(
        @{path=$planB; reason='用户确认停用方案 B；整个独立方案永久删除，不备份'},
        @{path=(Join-Path $taskRoot '.venv'); reason='由锁定 requirements-desktop-lock.txt 可重建的 Python 环境'},
        @{path=(Join-Path $taskRoot 'build'); reason='C++/PyInstaller 编译产物、打包副本及 QA 安装暂存；必要报告先保留到 artifacts'},
        @{path=(Join-Path $workspaceRoot 'separate-plans.ps1'); reason='方案 B 已停用，专门的双方案拆分脚本不再适用'},
        @{path=(Join-Path $taskRoot 'vendor\mouselink\open_bridge\logs'); reason='可再生成的运行日志'},
        @{path=(Join-Path $taskRoot 'vendor\mouselink\open_bridge\flash-logs'); reason='可再生成的刷机工具日志；固件和唯一备份不在此删除项'},
        @{path=(Join-Path $taskRoot 'vendor\ipaddisplay\logs'); reason='可再生成的运行日志'}
    )) {
        if (Test-Path -LiteralPath $entry.path) { $candidates.Add([pscustomobject]$entry) }
    }
    $oldRelease = Join-Path $taskRoot 'release\0.1.0-preview'
    $oldPortable = Join-Path $oldRelease 'iPadHub-0.1.0-preview-Windows-x64.zip'
    $oldInstaller = Join-Path $oldRelease 'iPadHub-0.1.0-preview-Setup-x64.exe'
    $oldSource = Join-Path $oldRelease 'iPadHub-0.1.0-preview-source.zip'
    if ((Test-Path -LiteralPath $oldPortable) -and (Test-Path -LiteralPath $oldInstaller) -and (Test-Path -LiteralPath $oldSource)) {
        $archive = [IO.Compression.ZipFile]::OpenRead($oldPortable)
        try {
            $entry = $archive.GetEntry('iPadHub/source/iPadHub-0.1.0-preview-source.zip')
            if ($null -ne $entry) {
                $stream = $entry.Open()
                $hasher = [Security.Cryptography.SHA256]::Create()
                try { $nestedHash = [Convert]::ToHexString($hasher.ComputeHash($stream)).ToLowerInvariant() }
                finally { $stream.Dispose(); $hasher.Dispose() }
                $retainedHash = (Get-FileHash -LiteralPath $oldSource -Algorithm SHA256).Hash.ToLowerInvariant()
                if ($nestedHash -eq $retainedHash) {
                    $candidates.Add([pscustomobject]@{ path=$oldPortable; reason='旧版便携发行物停止留存；保留同版回退安装器和外置源码。其嵌套源码与外置源码 SHA256 完全一致'; proof=('Retained installer: ' + $oldInstaller + '; retained source SHA256: ' + $retainedHash + '; nested source SHA256: ' + $nestedHash) })
                }
            }
        } finally { $archive.Dispose() }
    }
    foreach ($name in @('display-scale-1.png','home-scale-1.png','settings-scale-1.png')) {
        $duplicate = Join-Path $taskRoot ('artifacts\ui-refinement\20260930-112236\' + $name)
        $retained = Join-Path $taskRoot ('artifacts\preview\' + $name)
        if ((Test-Path -LiteralPath $duplicate) -and (Test-Path -LiteralPath $retained)) {
            $duplicateHash = (Get-FileHash -LiteralPath $duplicate -Algorithm SHA256).Hash
            if ($duplicateHash -eq (Get-FileHash -LiteralPath $retained -Algorithm SHA256).Hash) {
                $candidates.Add([pscustomobject]@{ path=$duplicate; reason='旧界面截图的逐字节重复副本；原图保留在 artifacts\preview'; proof=('Retained: ' + $retained + '; both SHA256: ' + $duplicateHash.ToLowerInvariant()) })
            }
        }
    }
    $pastBuildEvidence = Join-Path $taskRoot 'artifacts\publication\build-evidence'
    if (Test-Path -LiteralPath $pastBuildEvidence) {
        foreach ($directory in (Read-PhysicalTree $pastBuildEvidence | Where-Object { $_.directory -and -not $_.reparse })) {
            if ([IO.Path]::GetFileName($directory.path) -in @('test-temp','engine-smoke-data','smoke-data')) {
                $candidates.Add([pscustomobject]@{ path=$directory.path; reason='旧自动测试的隔离用户数据和空运行日志；构建/CTest/主程序验收报告保留' })
            }
        }
    }
    $releaseEvidence = Join-Path $taskRoot "artifacts\release-$Version"
    foreach ($directory in (Get-ChildItem -LiteralPath $releaseEvidence -Directory -Filter 'isolated-upgrade-*' -ErrorAction SilentlyContinue)) {
        if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        $scopeFile = Join-Path $directory.FullName 'SCOPE.txt'
        if ((Test-Path -LiteralPath $scopeFile) -and (Get-Content -LiteralPath $scopeFile -Raw).StartsWith('阶段')) {
            foreach ($file in (Get-ChildItem -LiteralPath $directory.FullName -File | Where-Object Extension -in @('.log','.png'))) {
                if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
                $candidates.Add([pscustomobject]@{ path=$file.FullName; reason='中间版 QA 演练的可再生成详细日志/图片；该次真实结果、范围声明和 payload 哈希保留，最终 QA 全证据保留' })
            }
        }
    }
    $stack = [Collections.Generic.Stack[string]]::new()
    foreach ($name in @('ipadhub','packaging','scripts','tests','vendor')) { $stack.Push((Join-Path $taskRoot $name)) }
    while ($stack.Count) {
        $directory = Get-Item -LiteralPath $stack.Pop() -Force
        if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        foreach ($child in (Get-ChildItem -LiteralPath $directory.FullName -Directory -Force)) {
            if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            if ($child.Name -in @('__pycache__','.pytest_cache','.pio')) {
                $candidates.Add([pscustomobject]@{ path=$child.FullName; reason='可再生成的 Python/测试/固件编译缓存' })
            } elseif ($child.Name -notin @('.git','node_modules')) { $stack.Push($child.FullName) }
        }
    }
    $selected = [Collections.Generic.List[object]]::new()
    foreach ($candidate in ($candidates | Sort-Object { $_.path.Length },path)) {
        if (-not @($selected | Where-Object { $candidate.path.StartsWith($_.path + '\', [StringComparison]::OrdinalIgnoreCase) }).Count) {
            $physical = Assert-PhysicalPath $candidate.path
            $stats = Get-Stats $physical
            $proof = if ('proof' -in $candidate.PSObject.Properties.Name) { $candidate.proof } else { $null }
            $selected.Add([pscustomobject]@{ path=$physical; reason=$candidate.reason; files=$stats.files; bytes=$stats.bytes; reparse_points=$stats.reparse_points; proof=$proof })
        }
    }
    return @($selected)
}

function Write-ReadablePlan($Plan, [string]$Path) {
    $lines = [Collections.Generic.List[string]]::new()
    $lines.Add('iPadHub ' + $Version + ' 清理清单（' + $Plan.created_at_utc + '）')
    $lines.Add('状态：仅规划；实际删除必须由单独 CLEAN 指令触发，且新版及隔离升级检查全部通过。')
    $lines.Add('大小为文件逻辑字节，保留所有联接/符号链接；磁盘实际可用空间可能因压缩和簇大小而不同。')
    $lines.Add('')
    foreach ($target in $Plan.targets) {
        $lines.Add(('{0} | {1:N2} MiB | {2} 个文件' -f $target.path,($target.bytes / 1MB),$target.files))
        $lines.Add('  原因：' + $target.reason)
        if ($target.proof) { $lines.Add('  依据：' + $target.proof) }
        foreach ($reparse in $target.reparse_points) { $lines.Add('  跳过联接：' + $reparse) }
    }
    $lines.Add('')
    $lines.Add(('计划删除合计：{0:N2} MiB，{1} 个文件。' -f ($Plan.total_bytes/1MB),$Plan.total_files))
    $lines.Add('')
    $lines.Add('保留：')
    foreach ($keep in $Plan.retained) { $lines.Add('  ' + $keep.path + '：' + $keep.reason) }
    [IO.File]::WriteAllLines($Path,$lines,[Text.UTF8Encoding]::new($true))
}

function Get-ProtectedManifest($Targets) {
    $records = @(Read-PhysicalTree $taskRoot)
    return @(foreach ($record in $records) {
        if ($record.directory -or $record.reparse -or
            $record.path.StartsWith($evidenceRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
            @($Targets | Where-Object { $record.path -eq $_.path -or $record.path.StartsWith($_.path + '\', [StringComparison]::OrdinalIgnoreCase) }).Count) { continue }
        [ordered]@{ path=$record.path; bytes=$record.bytes; sha256=(Get-FileHash -LiteralPath $record.path -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
}

function Remove-PhysicalTree([string]$Path) {
    $physical = Assert-PhysicalPath $Path
    $records = @(Read-PhysicalTree $physical)
    $removedFiles = 0
    $removedBytes = [long]0
    $skipped = @($records | Where-Object reparse | Select-Object -ExpandProperty path)
    foreach ($record in ($records | Where-Object { -not $_.directory -and -not $_.reparse })) {
        Assert-PhysicalPath $record.path | Out-Null
        Remove-Item -LiteralPath $record.path -Force
        $removedFiles++
        $removedBytes += $record.bytes
    }
    foreach ($record in ($records | Where-Object { $_.directory -and -not $_.reparse } | Sort-Object { $_.path.Length } -Descending)) {
        Assert-PhysicalPath $record.path | Out-Null
        if (-not @(Get-ChildItem -LiteralPath $record.path -Force).Count) { Remove-Item -LiteralPath $record.path -Force }
    }
    return [ordered]@{ path=$physical; removed_files=$removedFiles; removed_bytes=$removedBytes; skipped_reparse_points=$skipped; target_absent=(-not (Test-Path -LiteralPath $physical)) }
}

function Assert-ReleaseHashes {
    $verified = @()
    foreach ($line in (Get-Content -LiteralPath (Join-Path $releaseRoot 'SHA256SUMS.txt'))) {
        if ($line -notmatch '^([0-9a-fA-F]{64}) \*(.+)$') { throw 'Malformed release checksum line.' }
        $expected,$name = $Matches[1].ToLowerInvariant(),$Matches[2]
        if ([IO.Path]::GetFileName($name) -ne $name) { throw 'Release checksum path escaped its directory.' }
        $actual = (Get-FileHash -LiteralPath (Join-Path $releaseRoot $name) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($expected -ne $actual) { throw "Release checksum mismatch: $name" }
        $verified += [ordered]@{ name=$name; sha256=$actual }
    }
    foreach ($name in @("iPadHub-$Version-Setup-x64.exe","iPadHub-$Version-Windows-x64.zip","iPadHub-$Version-source.zip")) {
        if ($name -notin $verified.name) { throw "Required release file missing from checksums: $name" }
    }
    return $verified
}

if (-not $Execute) {
    $targets = @(Get-Candidates)
    $plan = [ordered]@{
        schema_version=1; created_at_utc=(Get-Date).ToUniversalTime().ToString('o'); version=$Version; workspace=$workspaceRoot; physical_plan_a=$taskRoot
        status='planned_only'; targets=$targets; total_files=($targets | Measure-Object files -Sum).Sum; total_bytes=($targets | Measure-Object bytes -Sum).Sum
        retained=@(
            @{path=(Join-Path $taskRoot '.git'); reason='版本历史和当前未提交修改'},
            @{path='方案 A 源码、测试、脚本、锁定依赖及所有唯一未跟踪源码'; reason='继续开发和重建所需，包括 candidates、archive、第三方源码/许可'},
            @{path=(Join-Path $taskRoot 'data-snapshot-20260930'); reason='唯一历史用户数据快照'},
            @{path=(Join-Path $taskRoot 'artifacts\baseline'); reason='旧源码快照、未提交 patch、第三方来源哈希和原设备/注册表备份'},
            @{path=(Join-Path $taskRoot 'artifacts'); reason='必要历史与新版验收、构建及清理证据'},
            @{path=(Join-Path $taskRoot 'vendor\mouselink\open_bridge\firmware'); reason='实际发行固件及清单'},
            @{path=(Join-Path $taskRoot 'release\0.1.0-preview'); reason='上一版回退安装器、外置对应源码及必要哈希；旧 portable 的嵌套源码与外置源码重复，拟删除旧 portable'},
            @{path=$releaseRoot; reason='新版安装器、便携包、对应源码及 SHA256'},
            @{path='父目录 .venv/artifacts/build/docs/ipadhub/packaging/release/scripts/tests/vendor 等兼容联接及硬链接'; reason='旧路径入口，不是另一份文件；不按副本删除'},
            @{path='当前已安装应用、HKCU 注册、桌面/开始菜单快捷方式和所有外部数据'; reason='验证与清理范围外，保持不变'},
            @{path=(Join-Path $workspaceRoot 'MouseLink与iPad互联合并_完整对话记录与计划.txt'); reason='用户原始记录，未授权删除'}
        )
    }
    if (-not $PlanFile) { $PlanFile = Join-Path $evidenceRoot 'cleanup-plan.json' }
    Write-Json $plan $PlanFile
    Write-ReadablePlan $plan ([IO.Path]::ChangeExtension($PlanFile,'.txt'))
    [ordered]@{ plan=$PlanFile; readable=[IO.Path]::ChangeExtension($PlanFile,'.txt'); targets=$targets.Count; files=$plan.total_files; bytes=$plan.total_bytes } | ConvertTo-Json
    exit 0
}

if (-not $PlanFile -or -not $ValidationReport) { throw 'Execute requires a reviewed PlanFile and successful ValidationReport.' }
$plan = Get-Content -LiteralPath $PlanFile -Raw | ConvertFrom-Json
$validation = Get-Content -LiteralPath $ValidationReport -Raw | ConvertFrom-Json
if ($plan.workspace -ne $workspaceRoot -or $plan.physical_plan_a -ne $taskRoot -or $plan.version -ne $Version -or
    -not $validation.ok -or -not $validation.production_state_unchanged -or -not $validation.qa_registration_removed) {
    throw 'Cleanup plan or actual isolated-upgrade validation is invalid.'
}
$currentTargets = @(Get-Candidates)
if ((ConvertTo-Json -InputObject $currentTargets -Depth 10 -Compress) -ne (ConvertTo-Json -InputObject $plan.targets -Depth 10 -Compress)) {
    throw 'Cleanup targets changed after planning. Regenerate the plan before executing.'
}
$releaseHashes = @(Assert-ReleaseHashes)
$qaRound = @($validation.rounds | Where-Object { $_.version -eq $Version })
if ($qaRound.Count -ne 1 -or
    $qaRound[0].production_portable_sha256 -ne ($releaseHashes | Where-Object name -eq "iPadHub-$Version-Windows-x64.zip").sha256 -or
    $qaRound[0].production_installer_sha256 -ne ($releaseHashes | Where-Object name -eq "iPadHub-$Version-Setup-x64.exe").sha256 -or
    $validation.formal_iss_sha256 -ne (Get-FileHash -LiteralPath (Join-Path $taskRoot 'packaging\iPadHub.iss') -Algorithm SHA256).Hash.ToLowerInvariant()) {
    throw 'QA validation does not match the exact current production installer, portable payload or Inno definition.'
}
foreach ($name in @('application-smoke.json','frozen-engines-smoke.json','packaging-verification.json')) {
    $check = Get-Content -LiteralPath (Join-Path $releaseRoot $name) -Raw | ConvertFrom-Json
    if ($name -ne 'packaging-verification.json' -and -not $check.ok) { throw "Release check failed: $name" }
}
if (@($currentTargets | Where-Object { $_.path -eq (Join-Path $taskRoot 'release\0.1.0-preview\iPadHub-0.1.0-preview-Windows-x64.zip') }).Count) {
    $oldRelease = Join-Path $taskRoot 'release\0.1.0-preview'
    [IO.File]::WriteAllLines((Join-Path $oldRelease 'LOCAL_RETENTION.txt'), @('本地留存说明：0.1.1-preview 清理时删除旧版 portable ZIP。','保留旧版回退安装器、外置对应源码及原始发布验证记录。','原 SHA256SUMS.txt / PUBLICATION_SHA256SUMS.txt 是完整历史发行清单，可能列出已从本机删除的 portable。','当前本机实际留存文件的哈希见 RETAINED_SHA256SUMS.txt。'), [Text.UTF8Encoding]::new($false))
    $retainedLines = @(Get-ChildItem -LiteralPath $oldRelease -File | Where-Object { $_.Name -notin @('iPadHub-0.1.0-preview-Windows-x64.zip','RETAINED_SHA256SUMS.txt') } | Sort-Object Name | ForEach-Object { '{0} *{1}' -f (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant(),$_.Name })
    [IO.File]::WriteAllLines((Join-Path $oldRelease 'RETAINED_SHA256SUMS.txt'), $retainedLines, [Text.UTF8Encoding]::new($false))
}
$protectedBefore = @(Get-ProtectedManifest $currentTargets)
Write-Json $protectedBefore (Join-Path $evidenceRoot 'protected-files-before.json')
$actual = [ordered]@{ ok=$false; started_at_utc=(Get-Date).ToUniversalTime().ToString('o'); source='Explicit user CLEAN authorization dispatched by the coordinating agent'; targets=@(); disk_free_before=(Get-PSDrive -Name D).Free }
try {
    foreach ($target in $currentTargets) {
        $removed = Remove-PhysicalTree $target.path
        $removed['reason'] = $target.reason
        $removed['proof'] = $target.proof
        $actual.targets += $removed
    }
    if (-not (Test-Path -LiteralPath $planB)) {
        $directoryNote = Join-Path $workspaceRoot '方案目录说明.txt'
        if ((Get-Item -LiteralPath $directoryNote -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Directory instructions unexpectedly became a reparse point.' }
        [IO.File]::WriteAllLines($directoryNote, @(
            '当前项目：方案A-iPadHub。方案 B（方案B-iPadHub2）已按用户授权永久删除。',
            '',
            '父目录原有目录是指向方案 A 的兼容联接，小文件为硬链接，旧路径仍作兼容入口；不要把它们当作额外副本删除。',
            '清理移除了可重建的 .venv 和 build；相关兼容联接在重建前可能暂时无目标。',
            ('新版发行物：方案A-iPadHub\release\' + $Version + '。可使用安装器或解压完整 Windows-x64 ZIP；保留其整个 iPadHub 文件夹。'),
            '开发环境按 docs\packaging.md 的锁定依赖重建。启动入口的具体行为以项目当前脚本为准。',
            '当前已安装应用、外部用户数据、旧版回退安装器和唯一设备/数据备份均保留。',
            ('实际删除、逻辑空间、保留理由及校验见方案A-iPadHub\artifacts\release-' + $Version + '\cleanup。')
        ), [Text.UTF8Encoding]::new($false))
        $actual['updated_directory_instructions'] = $directoryNote
    }
    $actual['protected_files_after'] = @(Get-ProtectedManifest $currentTargets)
    $actual['protected_files_unchanged'] = (ConvertTo-Json -InputObject $protectedBefore -Depth 10 -Compress) -eq (ConvertTo-Json -InputObject $actual.protected_files_after -Depth 10 -Compress)
    $actual['release_hashes_after'] = @(Assert-ReleaseHashes)
    $actual['ok'] = $actual.protected_files_unchanged -and -not @($actual.targets | Where-Object { -not $_.target_absent }).Count
} catch { $actual['error'] = $_.Exception.Message }
finally {
    if ($actual.Contains('protected_files_after')) {
        Write-Json $actual.protected_files_after (Join-Path $evidenceRoot 'protected-files-after.json')
        $actual.Remove('protected_files_after')
    }
    $actual['finished_at_utc'] = (Get-Date).ToUniversalTime().ToString('o')
    $actual['disk_free_after'] = (Get-PSDrive -Name D).Free
    $actual['removed_files'] = ($actual.targets | Measure-Object removed_files -Sum).Sum
    $actual['removed_bytes'] = ($actual.targets | Measure-Object removed_bytes -Sum).Sum
    Write-Json $actual (Join-Path $evidenceRoot 'actual-cleanup.json')
    $lines = @('iPadHub ' + $Version + ' 实际清理记录', '结果：' + $actual.ok, ('删除逻辑大小：{0:N2} MiB；文件 {1}' -f ($actual.removed_bytes/1MB),$actual.removed_files), '')
    foreach ($entry in $actual.targets) { $lines += ('{0} | {1:N2} MiB | 已不存在：{2} | {3}' -f $entry.path,($entry.removed_bytes/1MB),$entry.target_absent,$entry.reason) }
    $lines += @('', '保留理由及范围见 cleanup-plan.txt。新版全部交付物已在删除后重新核对 SHA256。', '实际可用空间变化仅作旁证，删除大小采用文件逻辑字节。')
    [IO.File]::WriteAllLines((Join-Path $evidenceRoot 'actual-cleanup.txt'),$lines,[Text.UTF8Encoding]::new($true))
    $actual | ConvertTo-Json -Depth 10
}
if (-not $actual.ok) { throw "Cleanup did not fully pass; see $evidenceRoot\actual-cleanup.json" }
