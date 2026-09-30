# iPadHub

[中文](README.md) | [English](README.en.md)

**电脑与 iPad，连在一起。** iPadHub 是 Windows x64 应用，将 MouseLink 的键鼠控制与 iPad互联的副屏功能放进一个界面。选择用途后，应用按需启动相应后台；第一版一次使用一种功能，切换前会等待当前后台释放资源并退出。

**当前版本：[0.1.0-preview](https://github.com/tukitohosi/ipad-hub/releases/tag/v0.1.0-preview)。** 已接入真实 Python 键鼠后台和 C++ 副屏后台，属于整合预览版。真机 50 次切换、各模式持续 60 分钟及干净 Windows 安装仍未验证。

![iPadHub 首页预览](docs/images/home-preview.png)

截图来自隔离的交互预览，展示界面与演示状态，不代表真实设备已连接。

## 能做什么

| 用途 | 功能 | 使用条件 |
| --- | --- | --- |
| iPad 副屏 | 扩展 Windows 桌面，支持 USB / Wi-Fi 连接、设备发现、手动地址及画质预设 | iPad 打开 OpenDisplay；Windows 已配置可用的虚拟显示驱动；USB 还需 Apple 设备服务 |
| 键鼠控制 | 用电脑鼠标和键盘操作 iPad 原生应用，支持左右位置、速度、自由 / 锁定模式及可选校准 | ESP32-C3 开发板通过 USB 连接电脑，并与 iPad 完成蓝牙配对 |
| 设备工具 | 固件备份、刷写、恢复和诊断 | 使用兼容的开发板；刷写 / 恢复前在工具窗口确认 |

首页不会自动连接或接管键鼠。关闭窗口收起到托盘，退出时停止后台；无法确认资源释放时会阻止切换。界面支持明暗主题、键盘操作及高级选项。

## 下载和安装

前往 [GitHub Releases](https://github.com/tukitohosi/ipad-hub/releases) 选择版本。安装器和便携版均为 Windows x64，保留历史版本下载。

| 文件 | 用途 |
| --- | --- |
| [iPadHub-0.1.0-preview-Setup-x64.exe](https://github.com/tukitohosi/ipad-hub/releases/download/v0.1.0-preview/iPadHub-0.1.0-preview-Setup-x64.exe) | 安装版；默认安装到 `%LOCALAPPDATA%\Programs\iPadHub` |
| [iPadHub-0.1.0-preview-Windows-x64.zip](https://github.com/tukitohosi/ipad-hub/releases/download/v0.1.0-preview/iPadHub-0.1.0-preview-Windows-x64.zip) | 便携版；完整解压后打开 `iPadHub.exe`，保留全部同目录文件 |
| [iPadHub-0.1.0-preview-source.zip](https://github.com/tukitohosi/ipad-hub/releases/download/v0.1.0-preview/iPadHub-0.1.0-preview-source.zip) | 本次安装包的对应源码，亦随安装版 / 便携版提供 |
| [SHA256SUMS.txt](https://github.com/tukitohosi/ipad-hub/releases/download/v0.1.0-preview/SHA256SUMS.txt) | 校验原交付包与检查报告；新增发布报告见 `PUBLICATION_SHA256SUMS.txt` |

安装包未进行代码签名，不包含或自动安装 Parsec 驱动，不修改防火墙。接收端与驱动需要单独配置。详细连接、紧急返回和工具操作见 [使用说明](docs/使用说明.md)。

## 数据、共存和回退

首次运行将兼容的旧设置和固件备份复制到 `%LOCALAPPDATA%\iPadHub`，之后使用独立数据目录和 `HKCU\Software\iPadHub\Display` 注册表路径。迁移只复制，已有新文件不会被旧设置覆盖；卸载默认保留新用户数据。

安装 iPadHub 无需卸载 MouseLink 或 iPad互联。旧程序及其数据继续保留，同一资源应由一个应用使用。需要回退时，退出 iPadHub 后打开原应用即可。

## 验证范围

本地自动回归、C++ 构建、无设备管道联调、冻结程序启动、ZIP CRC 和许可检查已通过。真实硬件 50 次往返切换、USB / Wi-Fi / 键鼠各 60 分钟、断连与睡眠恢复、实机刷写、干净 Windows 安装和实际升级卸载仍未验证。具体证据和未验证项目见 [验收记录](docs/release-acceptance.md) 与 Release 随附报告。

## 源码与构建

| 路径 | 内容 |
| --- | --- |
| `ipadhub/` | 统一界面、生命周期、管道、迁移和维护工具 |
| `vendor/mouselink/` | 当前 MouseLink 核心、固件、资源、测试、许可证 |
| `vendor/ipaddisplay/` | 含原未提交修改的副屏源码副本与新增无界面后台 |
| `scripts/`、`packaging/` | 独立构建、测试、打包与发行检查 |
| `tests/`、`docs/` | 整合回归、接口、使用说明和验收记录 |
| `docs/images/` | 无个人设备数据的界面预览截图 |

依赖 Python 3.12、Visual Studio 2022 C++ x64/CMake/Windows SDK、Inno Setup 6。Python 使用 `vendor/mouselink/open_bridge/requirements-desktop-lock.txt` 锁定环境。完整步骤见 [构建说明](docs/packaging.md)。

```powershell
& .\.venv\Scripts\python.exe -m unittest discover -s tests -v
& .\scripts\build-display-engine.ps1
& .\scripts\package-ipadhub.ps1 -Version 0.1.0-preview
```

已有非空同版本发布目录时拒绝覆盖，历史安装包保留。打包顺序为 C++ 引擎 → Python/Qt 应用及刷机助手 → 校验与源码/便携 ZIP → Inno 安装包。冻结后的两个后台通过实际管道的无设备状态 / 退出检查。发布包保持当次构建原样；仓库中的发行脚本另补充 `frozen-engines-smoke.json` 报告别名，内容与原 `engine-smoke.json` 一致。

开发运行：`python -m ipadhub.app`。要使用完全隔离的模拟界面，运行 `python -m ipadhub.app --preview` 或双击 `启动iPadHub预览.cmd`；预览只保存内存状态，不迁移数据或访问硬件。

## 许可与致谢

整合产品按 [GPL-3.0](LICENSE) 交付对应源码，MouseLink / esp32-kvm 保留 MIT，opendisplay-win 衍生副屏后台保留 GPL，Qt / PySide6 等组件保留各自许可，Qt DLL 独立分发。上游项目引用、版权和许可全文见 [第三方声明](THIRD_PARTY_NOTICES.md) 及安装目录 `licenses/`。Parsec 驱动不内嵌，本项目不代表 Parsec 官方兼容认证。
