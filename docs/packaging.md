# iPadHub 构建、打包与回退

## 输出与边界

版本 `0.1.0-preview` 是接入真实后台的首个整合预览版。安装身份为独立的 `{2F7A9C14-E3D0-4D64-8A12-9B59D3CD7EF1}`；默认安装在 `%LOCALAPPDATA%\Programs\iPadHub`，新快捷方式名为 `iPadHub`。两个原程序、快捷方式、卸载登记与数据均保留。安装程序不访问旧卸载登记、不自动替换旧版本应用、不安装驱动、不修改防火墙。

打包只生成本地交付物，不安装、不公开发布。数据保存在 `%LOCALAPPDATA%\iPadHub`，卸载仅移除安装文件和 iPadHub 快捷方式，保留数据根。

## 工具与锁定环境

- Windows x64、Python 3.12、Visual Studio 2022 C++ x64 工具及其 CMake。
- Python 依赖使用 `vendor/mouselink/open_bridge/requirements-desktop-lock.txt`，安装到项目 `.venv`。
- Inno Setup 6 可通过 `-IsccPath` 显式指定。此机器可复用 `D:\Desktop\本地项目\iPad副屏\.tools\InnoSetup6\ISCC.exe`，不会修改其所属旧应用。
- 原基线构建脚本：`scripts/build-mouselink-baseline.ps1`、`scripts/build-display-baseline.ps1`。

在项目根目录执行：

```powershell
& 'C:\Users\稚青\AppData\Local\Programs\Python\Python312\python.exe' -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r vendor\mouselink\open_bridge\requirements-desktop-lock.txt
& .\scripts\package-ipadhub.ps1 -Version 0.1.0-preview
```

已有项目虚拟环境无需重建。脚本依次构建与测试 C++ 后台、运行 iPadHub 单元回归、构建 PyInstaller 主应用和独立刷机助手、验证固件、运行无设备应用自检与冻结后台握手、生成对应源码与便携 ZIP，最后编译 Inno 安装包。后台握手启动已打包的 `iPadHub.exe --engine bridge` 与带 `--no-device` 的 C++ 后台，仅发送 `status/shutdown`，核对 `hello`、状态、资源释放及正常退出；不运行发现或连接。`-SkipDisplayBuild` 仅用于刚完成同一源码版本 C++ 构建的本地重试；`-SkipInstaller` 可仅生成便携与源码。

## 安装目录布局

```text
iPadHub.exe                  # 同时支持 --engine bridge 后台入口
MouseLinkFlash.exe            # 独立刷机/备份子进程
_internal/
  engines/iPadHubDisplay.exe  # C++ 后台，无旧窗口或旧托盘
  bridge/                    # MouseLink Python 核心及固件资源
  firmware/                  # 刷机助手校验的固件资源
  PySide6/                   # 可替换的 Qt DLL
  ipadhub/assets/            # 新界面资源
licenses/                    # 完整原版权与许可证
source/iPadHub-<version>-source.zip
docs/
LICENSE
THIRD_PARTY_NOTICES.md
bundle-SHA256.json
```

冻结程序内的 `sys._MEIPASS` 指向 `_internal`；后台使用 `sys._MEIPASS/engines/iPadHubDisplay.exe`。刷机助手与 `sys.executable` 同目录。解压便携版后必须保留整个 `iPadHub/` 文件夹，不能单独复制 EXE。便携版也使用独立的本机用户数据根，不在解压目录旁读写真实设置。

## 交付文件和保护

输出为 `release/<version>/iPadHub-<version>-Setup-x64.exe`、`iPadHub-<version>-Windows-x64.zip`、`iPadHub-<version>-source.zip`、`SHA256SUMS.txt` 和验证报告。所有构建中间物在时间戳命名的 `build/package-*` 下保留。已有同版本发布目录非空时脚本拒绝覆盖；无需删除旧安装包或旧构建缓存。

源码 ZIP 仅从明确的源码目录和许可文件生成，排除虚拟环境、构建缓存、运行日志、个人数据备份与原始对话附件。固件 BIN 仅接受已校验发行固件与原测试所需 bootloader；不分发 Parsec 驱动二进制。完整对应源码也嵌入新安装与便携目录，旧 GPL 和 MIT 声明均保留。Qt DLL 保持独立且可替换。

## 数据迁移与恢复

`HubStorage` 默认不在构造时写文件，真实应用取得单实例所有权后调用 `migrate()`：

| 旧来源 | 新位置 |
| --- | --- |
| `%LOCALAPPDATA%\MouseLink\settings.json`、`.bak` | `iPadHub\mouselink\settings.json`、`.bak` |
| 旧版仅有 `appsettings.json` 时的兼容来源 | 同上，仅实际 `settings.json` 缺失时使用 |
| `%APPDATA%\MouseLink\config.json`、`.bak` | `iPadHub\display\config.json`、`.bak` |
| `%LOCALAPPDATA%\MouseLink\firmware-backups` | `iPadHub\firmware-backups` |
| `HKCU\Software\MouseLink` 的 `monitorX/monitorY` 和设备专属 DWORD 条目 | `HKCU\Software\iPadHub\Display` 同名条目 |

迁移仅复制，不修改或删除旧来源。新文件和注册表值存在就跳过。实际设置中的 `calibration` 完整保留；源代码里的 `device-calibration.json` 不参与数据迁移。副屏设备身份和串口/蓝牙身份分属不同配置文件。

成功后写入 `migration-v1.json` 完成标记；失败写入 `migration-last-report.json`，保留已复制项，下次启动只重试尚未完成内容。失败允许默认设置继续使用。根 `settings.json` 是 UI 偏好，`schema_version=1`，`theme` 为 `system/light/dark`；应用可附加字段。

`load_json`/`save_json` 支持引擎与 UI 配置：临时文件写入并刷新后原子替换，旧有效版本保留 `.bak`。主配置损坏时读取有效 `.bak`，原损坏字节另存 `.corrupt-<时间>`。无有效备份时使用默认，不删除损坏文件。

## 验证范围

本地构建、单元测试、无设备自检及 ZIP/哈希检查不等于真机验收。真机至少 50 次切换，USB/Wi-Fi/键鼠分别 60 分钟，睡眠和断连、干净 Windows、新旧共存、升级卸载与数据保留等结果见 `release-acceptance.md`；未实测项目必须标记未验证。

新安装无需卸载旧软件。要回退，退出 iPadHub 后使用原 `iPad互联.lnk` 或 `MouseLink.lnk`；同一资源只让一个应用使用。卸载 iPadHub 时保留数据，无需运行任何旧卸载器。
