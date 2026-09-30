# 旧软件与用户数据安全基线

采集时间：2026-09-30 11:00（Asia/Singapore）。本记录针对方案 A 的第 1 阶段，只读取两款旧软件和相关数据；没有启动引擎、连接设备、结束进程、执行迁移或写入旧注册表。

## 可回退资料

本机备份目录：`artifacts/baseline/system/20260930-110005-791/`。

- `system-baseline.json`：快捷方式、版本、发布文件 SHA-256、数据文件复制前/备份/复制后 SHA-256、相关注册表前后快照、卸载登记和进程快照。
- `backup-hashes.json`：该目录内全部备份文件的 SHA-256 清单（不包含自身）。
- `data/mouselink-local/`：完整复制 `%LOCALAPPDATA%\MouseLink`，10 个文件，12,592,741 字节；包含 3 份固件全闪存备份和对应元数据，以及旧配置、日志和候选版资料。
- `data/ipadconnect-roaming/`：完整复制 `%APPDATA%\MouseLink`，27 个文件，199,235 字节；包含 `config.json`、`config.json.bak` 与日志。
- `Software-MouseLink.reg`：导出 `HKCU\Software\MouseLink`，包含 14 个值；导出前后读取内容一致。
- `shortcuts/`：保留两个原桌面快捷方式。

共复制 37 个数据文件、12,791,976 字节。所有文件的源文件前后哈希与备份哈希三者一致，文件列表无变化，未发现复制异常。旧 `%APPDATA%\opendisplay-win` 目录及 `HKCU\Software\opendisplay-win` 注册表项均不存在，已记录为缺失，未创建它们。

这些备份包含私人设备记录、设备标识和日志，只保留在本地。`artifacts/baseline/system/` 必须整体忽略，不能纳入 Git、公开源码包或安装包。

可在项目根执行 `scripts/capture-system-baseline.ps1` 重建独立时间戳备份；脚本不覆盖先前捕获。原文件系统及注册表一直是回退来源，本轮没有变更，无需执行恢复。未来如确需恢复，应先检查当时数据，不能直接用旧备份覆盖更新后的用户数据。

## 安装版本及发布物

| 项目 | 桌面快捷方式目标 | 实际 EXE 版本 | 与本地发布主程序匹配 |
|---|---|---|---|
| MouseLink | `D:\AppData\MouseLink\MouseLink.exe` | 1.0.3 | 是，匹配 `外设复用\release\1.0.3\MouseLink\MouseLink.exe` |
| iPad互联 | `D:\AppData\iPad互联\iPad互联.exe` | 0.3.0-preview | 是，匹配 `iPad副屏\dist\iPad互联-0.3.0-preview-portable.exe` |

两快捷方式均无额外参数。主程序 SHA-256：

```text
MouseLink.exe
13EF59507022D067BAF873A4D00383CE545ECF1AA6C881D59BA6BE6FB667F5D3

iPad互联.exe
D34E9BFDFBEE65C3AC330DDFAE18C20779ACF327767A84D5E0D763068B83708F
```

安装包、便携包、源码包及原 SHA-256 清单已读取并记录哈希。安装包哈希：

```text
MouseLink-1.0.3-Setup.exe (32,890,618 bytes)
83B67E130639ECE895CC172FCA40BA93807D53A4A85402AAAC3811F62FF0B914

iPad互联-0.3.0-preview-Setup-x64.exe (2,518,340 bytes)
B78795E0D76CA8E66BD9654FDBD995A6FCA8D5DAEB440EC9DB882DDF90000BF4
```

这些主程序和安装包哈希与两项目现有发布清单一致。该结论仅确认实际读取的文件，不能推导为整个安装目录的逐文件等同、源码重建一致或新程序真机验收。

MouseLink 卸载登记仍为 **1.0.1**，指向 `%LOCALAPPDATA%\Programs\MouseLink\`；桌面实际使用 **D 盘 1.0.3**。新安装器必须使用新的 AppId、路径及快捷方式，不能按此旧卸载项替换或删除现有软件。iPad互联登记为 0.3.0-preview，路径与快捷方式相符。

采集时未发现对应安装目录或主程序名称的运行进程。该快照不能保证后续操作时仍空闲，也没有检查真实串口或驱动占用。采集结束时 C 盘约 61.7 GiB、D 盘约 68.6 GiB 可用；空间会随并行源码备份和构建变化。

## 下一阶段必须处理的事实

1. **配置根同名但位置不同。** Python 键鼠读取 `%LOCALAPPDATA%\MouseLink`，已有 `MOUSELINK_DATA_DIR` 覆盖入口；C++ 副屏读取 `%APPDATA%\MouseLink`，已有绝对路径 `IPAD_CONNECT_CONFIG_DIR` 覆盖入口。应分别注入新应用的独立子目录，再实现一次性复制迁移，不能把两者合成同一个 JSON。
2. **副屏注册表尚未隔离。** `src/display/VirtualDisplay.cpp` 直接读取和写入 `HKCU\Software\MouseLink`，回退读取 `Software\opendisplay-win`。其 `LoadSavedPosition` 在把共享位置迁给设备身份时会删除原 `monitorX`/`monitorY` 值，并非纯读取函数。新引擎必须先支持独立注册表根；迁移器只能读旧根、复制到新根，保留旧值。仅覆盖配置目录不足以保证旧数据不变。
3. **停止桥接不等于释放串口。** Python `Worker.stop_bridge()` 停止当前会话；`Worker._idle()` 仍创建并保留 `SerialBridge` 做设备状态监测。模式交接必须调用完整 shutdown 并等线程/进程退出。不能依据旧界面的“停止”状态宣告资源已释放。
4. **保留跨旧新软件的底层锁。** 副屏单实例锁是 `Local\IpadConnect.Tray.v1`，连接锁是 `Global\opendisplay-win-<设备身份或 IP>`，虚拟显示锁是 `Global\opendisplay-win-vdd-claim`。键鼠单实例锁是 `Local\MouseLink.KVM.Desktop`，串口连接失败会显示其他程序占用提示。新 UI 的单实例身份应独立；底层资源锁不能因改名而绕开旧软件保护。
5. **固件恢复必须保留设备对应关系。** 本地备份包含 `backup.json` 和完整 `device-flash.bin`，不能只复制推荐固件。现有恢复代码按设备身份校验；迁移时保留整个备份目录及元数据，不将名称相同视作同一设备。

本轮没有验证真实设备、USB/Wi-Fi 通信、全局输入释放、虚拟屏清理、安装回退或 60 分钟稳定性。新应用在用户审核可操作界面预览前，不进入界面定稿和统一打包。

## 追加：源码目录内的旧校准数据

2026-09-30 11:03 追加独立捕获 `artifacts/baseline/system/20260930-110342-810-calibration/`，没有覆盖上面的首轮证据。源文件是 `D:\Desktop\本地项目\外设复用\open_bridge\device-calibration.json`，117 字节；备份前、备份文件、备份后原文件 SHA-256 均为：

```text
81AB6B910DE09E189679D0D662AFAB5EB2DB8AB3563707CBC583C43B4A2E97EF
```

该目录的 `calibration-capture.json` 保存来源和核验记录。当前 1.0.3 的 `desktop_app.py` 从用户 `settings.json` 的 `calibration` 字段保存检查结果，当前运行代码未检索到对 `device-calibration.json` 的引用；保留的 2026-09-27 之前源快照曾读取此文件作为 seed。这个追加文件用于保全历史参数，不能直接当作当前生效配置，也不能让较旧副本覆盖已迁入的新参数。
