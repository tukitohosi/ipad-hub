# iPad互联基线导入与构建记录

记录日期：2026-09-30。仅执行方案 A 的副屏基线导入、独立构建与自动化测试。本阶段不运行真实副屏会话、不连接 iPad、不修改驱动、不生成安装包。

## 来源与可回退材料

- 原项目：`D:\Desktop\本地项目\iPad副屏`。
- 新项目基线：`vendor/ipaddisplay`。
- 原项目 HEAD：`06af3a9333f3acd0a79725adffb07b681abdcaf8`。
- 当前工作树包含 **31 个已跟踪修改和 52 个未跟踪文件**；导入的依据是当前文件内容，包含当前源码、测试、许可证、构建脚本、安装脚本、文档、第三方头文件和历史源码归档。
- 共复制 **91 个文件、1,051,013 字节**，每个目标文件均与源文件 SHA-256 一致。
- 归档：`artifacts/baseline/ipaddisplay/ipaddisplay-source-working-tree.zip`。
- 归档 SHA-256：`78CA69F12070A8D3DB43F9ADFCA1B953038FA5D73317C723338687196B679E80`。

证据集中在 `artifacts/baseline/ipaddisplay`：

| 文件 | 作用 |
|---|---|
| `source-manifest.json` | 91 个导入文件的路径、大小、时间与 SHA-256 |
| `source-git-head.txt` / `source-git-status.txt` | 原项目 HEAD 与完整 dirty 状态 |
| `source-unstaged.patch` / `source-staged.patch` | 已跟踪文件的二进制兼容补丁；未跟踪文件本体在归档与基线目录中 |
| `excluded-files.json` | 1,414 个排除文件的原相对路径、大小与理由 |
| `original-release-sha256.json` | 原 `dist` 中 25 个发布/归档文件的 SHA-256；原发布物未移动、未删除 |
| `capture-summary.json` / `SHA256SUMS.txt` | 导入摘要与归档校验值 |

排除 `.git` 内部数据、`.codex` 本机元数据、`.tools` 下载工具缓存、`build` / `build-verify` / `preview-build` 编译输出、`dist` 旧发布物、`reports` 本机诊断，以及缓存和编辑器生成文件。历史 `archive/superseded` 源码压缩包被保留。基线导入不包含真实用户配置、注册表或固件数据；这些由总项目单独备份管理。

## 独立构建

在新项目根目录执行：

```powershell
& .\scripts\build-display-baseline.ps1
```

脚本使用现有 Visual Studio 2022 Build Tools 的 CMake/MSVC，生成到 `build/ipaddisplay`，保留原基线所有源码字节。为避开既有非 ASCII 路径工具链故障，在脚本执行期间建立临时 `subst` 路径，结束时释放。测试临时文件与配置重定向到新构建目录；不启动主程序。

本次工具链：CMake 3.31.6-msvc6、MSVC 19.44.35228.0、Windows SDK 10.0.26100.0、Visual Studio 17 2022 generator、x64 Release。所有依赖均为当前系统/SDK 与导入源码已有组件，没有下载新依赖或重新安装工具。

本次独立 Release 构建通过；**10/10 项 CTest 全部通过，总耗时 16.15 秒**。通过项目：`protocol-v3`、`ui-model`、`device-flow`、`panel-layout`、`panel-scroll`、`video-color`、`connection-deadlines`、`network-safety`、`encoder-color-roundtrip`、`sender-candidates`。源码无需修复。编译保留了第三方头文件现有的有符号/无符号转换及未使用函数警告（C4245、C4505）；详见日志。

构建与测试结果见 `build-result.json`、`configure.log`、`build.log`、`ctest.log` 和 `ctest-results.xml`。主程序位于 `build/ipaddisplay/Release/iPad互联.exe`，大小 1,014,784 字节，SHA-256 为 `CFC893D920BC7A6E3CD218BCF540CBE5B3D92E58CDB44A7FDDBDF2DB08B64D33`。这是原副屏基线程序，不是已整合完成的新应用，也不要求其二进制哈希与历史发布物相等。

结束后重新核对：91 个原文件、91 个导入文件、快照内 91 项内容均与初始清单一致；25 个原发布物哈希及原 Git dirty 状态未改变。`final-verification.json` 保留复核结果。临时 ASCII 盘符映射已释放。

## 后续无界面引擎接入点

| 现有位置 | 可复用能力与必要改造 |
|---|---|
| `src/app/SenderApp.h` | `Start`、`RequestStop`、`Stop`、`IsRunning`、`Snapshot` 已覆盖传流工作线程及状态快照，可由新后台接口封装。`Stop` 会等待线程结束；不得只用 UI 状态宣告释放完成。 |
| `src/app/TrayApp.cpp` | `RunDiscovery`、`RefreshConnectionCandidates`、`StartUsbSession`、`StartWifiSession`、`StartTransportAttempt`、`AdvanceStartupAutoConnect` 等仍依赖 `TrayContext`，必须抽取为无窗口生命周期控制器，不能为新应用启动第二套托盘。 |
| `src/app/DeviceCoordinator.*` | 独立纯状态机，管理设备优先级、候选切换和全局/设备失败分类，可直接保留。 |
| `src/net/Mdns.*` / `UsbMux.*` / `Connection.*` | 保留既有发现、USB/Wi-Fi 传输与 OpenDisplay v3 协议。开发板/蓝牙身份应由另一引擎独立维护。 |
| `src/app/Config.cpp` | 已支持绝对路径环境变量 `IPAD_CONNECT_CONFIG_DIR`，可由新后台指定 `%LOCALAPPDATA%\iPadHub\display`；设置该变量后不会自动迁移真实旧配置。正式迁移应从已备份文件一次性复制，并保留 `.bak` 恢复。 |
| `src/display/VirtualDisplay.cpp` | 位置目前硬编码为 `HKCU\Software\MouseLink`，旧兼容来源为 `HKCU\Software\opendisplay-win`。`LoadSavedPosition` 会删除旧共享 `monitorX` / `monitorY`，正式引擎不能直接沿用此迁移路径；应使用新命名空间并只复制旧值。值名包括 `monitorX` / `monitorY` 及身份后缀，负坐标按 DWORD 位模式保存。 |
| `src/main.cpp` | 旧托盘单实例名 `Local\IpadConnect.Tray.v1` 与旧窗口消息必须和新产品独立；现有 `RunBlocking` CLI 不是所需的版本化 JSON 管道协议，尚需单独入口与父进程/管道断开清理。 |

仍需保留底层资源互斥：`SenderApp.cpp` 的 `Global\opendisplay-win-<device identity>` 连接锁，以及 `VirtualDisplay.cpp` 的 `Global\opendisplay-win-vdd-claim` 创建锁。应用身份独立不意味着底层锁也应改名，否则会破坏与旧程序共存时的保护。

`SenderApp` 的退出路径依赖工作线程结束和对象析构释放输入、捕获、虚拟屏及编码器。新管道必须在真实释放完成后报告停止成功；退出超时或清理失败应阻止切换。当前会话还未验证这些新增控制与异常退出场景。

## 许可证和验收边界

已原样保留 `LICENSE`、`THIRD_PARTY_NOTICES.txt`、第三方源头文件及构建资料。副屏来源包含 GPL-3.0，整合产品不能统一宣称全 MIT。没有内嵌或重新分发 Parsec 驱动。

自动化测试可证明本机源码构建与所覆盖的逻辑，不能代替 USB/Wi-Fi 真机传流、至少 50 次模式切换、两种模式各 60 分钟运行、睡眠/锁屏恢复、非管理员/干净 Windows 安装验收。所有这些产品验收仍待后续阶段。
