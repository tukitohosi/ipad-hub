# MouseLink

**通过 ESP32-C3 USB 转蓝牙 HID 桥，让 Windows 的键盘和鼠标直接控制 iPad。**

[English](README.md) · [项目仓库](https://github.com/tukitohosi/mouselink)

MouseLink 在 Windows 上捕获键鼠输入，经 USB 发送给 ESP32-C3，再由开发板模拟蓝牙键盘和鼠标，控制 iPad 自身的界面。iPad 无需安装应用，无需投屏，也无需与电脑处于同一 Wi-Fi 网络。使用时不要求开启辅助触控。

```text
Windows 键盘和鼠标 → MouseLink → USB → ESP32-C3 → 蓝牙 HID → iPad
```

## 当前版本与已知限制

当前发布版本为 **1.0.3**：取消强制光标检查，新增左右摆放选择及运行中换边；用户已完成本机实机验收，现有 1.0.2 安装包保留用于回退。沿用原有 v3 固件，无需为这些改动重刷开发板。屏内使用绝对定位，部分边缘操作使用相对移动，正常使用没有单次会话时限。

从源码开发时，设置好本地 Python 环境并退出当前 MouseLink 后，可双击 `启动MouseLink开发版.cmd`。它使用 `test-results/placement-1.0.3/app-data` 中的独立设置与日志，首次复制现有设置，后续不改动安装版数据；不会自动开启键鼠捕获。

**已知问题：**调出 Dock 或退出应用时，偶尔会出现 Dock 无法调出、弹出后又收起的情况。将指针移回屏幕内，再尝试一次底部操作。本版本不宣称与 iPad 直接连接蓝牙鼠标的全部行为完全一致。

1.0.3 已通过用户本机实机验收。此前进入 iPad、点击应用及左边缘返回的检查使用 Windows 11 x64，以及运行 iPadOS 26.6.1 的 iPad Pro M5（2025）。这不代表已验证干净机器、所有 iPad，或所有模式、方向和系统边缘操作的重复表现。

## 所需设备

| 项目 | 要求 |
| --- | --- |
| 电脑 | Windows x64，连接需要共用的键盘和鼠标；已测试 Windows 11 |
| 开发板 | ESP32-C3 SuperMini，原生 USB 接口，4 MB Flash |
| 连接线 | 支持数据传输的 USB 线，用于连接开发板与电脑 |
| iPad | 支持蓝牙鼠标，开启蓝牙并保持解锁 |
| 电脑软件 | MouseLink 安装包、便携版，或通过本仓库源码自行构建 |

内置刷机流程仅针对上表中的开发板。通过 CH340/CP210x 转串口连接的开发板、其他 ESP32 型号和其他 Flash 容量不在其支持选择范围内。电脑无需自带蓝牙适配器：与 iPad 通信的蓝牙由 ESP32-C3 提供。

## 安装与开始使用

本 Git 仓库仅保存源码。Windows 安装包及便携包可在 [v1.0.3 发布页](https://github.com/tukitohosi/mouselink/releases/tag/v1.0.3)下载；应用包已内置固件。

1. 运行 `MouseLink-1.0.3-Setup.exe`；也可以解压 `MouseLink-1.0.3-Windows-x64.zip`，运行其中的 `MouseLink.exe`。请完整保留便携版文件夹，包括 `_internal`。
2. 用 USB 数据线连接 ESP32-C3 与电脑。新开发板先按下方步骤刷入固件。
3. 在 iPad 的“设置 → 蓝牙”中配对 **MouseLink-iPad**，并保持 iPad 解锁。
4. 在 1.0.3 中选择“**iPad 在电脑左侧**”或“**iPad 在电脑右侧**”，选择会自动保存；首次默认右侧。
5. 选择切换模式，连接就绪后点击“**开启键鼠桥**”。“**检查光标位置**”仅用于异常排查，首次或更换开发板也不必先检查。

打包版已包含 Python 运行时和刷机工具。日常使用和内置固件刷写均无需另装 Python、Arduino、PlatformIO，也不需要 iPad 应用。当前软件界面为中文。

## 操作方法

将 Windows 指针推到所选摆放一侧的电脑边缘，稍停即可进入 iPad。多显示器使用整个 Windows 桌面的最外侧边缘。调整模式、快捷键返回开关或指针速度前，请先停止键鼠桥。

| iPad 摆放位置 | 进入 iPad | 自由模式返回电脑 |
| --- | --- | --- |
| 电脑右侧（默认） | 电脑右边缘 → iPad 左边缘 | 到达 iPad 左边缘后继续向左推动 |
| 电脑左侧 | 电脑左边缘 → iPad 右边缘 | 到达 iPad 右边缘后继续向右推动 |

运行中可直接更改摆放位置：先释放键鼠、返回电脑，再恢复新方向的桥接待机，不会立即接管输入。切换期间仍可点击停止，连续选择以最后一次为准。停止、关闭、打开刷机页或重连失败会取消自动恢复；新位置仍会保存。候选版换边不会延长当次使用时限。

| 模式 | 返回电脑的方法 |
| --- | --- |
| **自由切换** | 在朝向电脑的 iPad 边缘继续向外推动，方向见上表 |
| 自由切换，同时开启“**快捷键返回**” | 对应边缘返回，或 `Ctrl + 左 Alt` |
| **锁定在 iPad** | `Ctrl + 左 Alt`；碰到屏幕边缘不会返回电脑 |

- 按下 `Ctrl + 左 Alt` 后将两个键全部松开，返回动作在松开时触发。全新安装的自由模式默认关闭可选快捷键返回开关。
- `Scroll Lock` 与 `Ctrl + Alt + Esc` 保留为紧急返回快捷键。
- 按住鼠标键拖动时，不会自动从边缘返回；松开后再朝电脑方向推动。
- 底部操作时，将指针移到最底部，再继续向下推动，保持鼠标键松开。当前混合方案可触发部分 iPad 系统操作，但仍有上述 Dock 问题。边缘操作后，先将指针移回屏幕内，再继续普通指针操作或边缘返回。
- Windows 键对应 iPad Command，Alt 对应 Option。
- 关闭 MouseLink 会停止键鼠桥并释放输入；USB 或蓝牙断开时也会触发输入释放。软件不会随开机或插入开发板自动启动。

## 刷入固件与恢复备份

1. 点击“**一键刷机**”。软件会停止键鼠桥，让刷机流程使用 USB 连接。
2. 选择支持的开发板，点击“**备份并刷入**”。连接多个开发板时，必须明确选择目标设备。
3. 等待备份、写入校验、重启和通信检查完成，全程保持 USB 连接。刷写中关闭窗口时，软件会等待操作结束。
4. 在 iPad 蓝牙设置中配对或重新连接 **MouseLink-iPad**。完整初始化后，可能需要忽略原来的蓝牙条目，再重新配对。

写入前，软件会检查芯片、Flash 容量、设备身份与固件包哈希，并先保存、校验完整备份。已识别的兼容固件只更新应用区，保留配对数据；空白板或未知固件会执行完整初始化，替换开发板原有内容。

“**恢复备份…**”使用 MouseLink 生成的 `backup.json`，仅允许恢复到原开发板，恢复前同样会先备份当前内容。若不能自动进入下载模式，请按住 **BOOT**、轻按一次 **Reset/复位**，再松开 **BOOT** 后重试。刷写结束后不会自动开启键鼠桥，需要自行点击开启。

## 常见问题与本地数据

- **找不到开发板：**检查 USB 线是否支持数据，尝试其他 USB 口，再刷新设备列表。
- **iPad 没有光标：**保持 iPad 解锁，确认 **MouseLink-iPad** 已连接，并运行光标位置检查。辅助触控不是使用前提。
- **更换固件或重新配对后仍无光标：**曾通过在 iPad“设置”中关闭蓝牙、拔掉开发板 USB 约 10 秒、重新连接 USB 并开启蓝牙恢复。此方法是观察到的恢复步骤，不代表已确定具体根因。
- **Dock 收起或退出应用失败：**将指针移回屏幕内后重试。此次改动不解决这一既有偶发问题。
- **需要恢复电脑键鼠：**使用紧急返回快捷键，或关闭 MouseLink。

设置和桥接日志位于 `%LOCALAPPDATA%\MouseLink`；固件备份与刷机日志分别位于其 `firmware-backups`、`flash-logs` 子目录。升级及默认卸载会保留这些文件。提交源码时，请勿上传个人固件备份、设备标识或本地日志。

## 开发工具

桌面程序使用 Python 与 PySide6；pynput 负责 Windows 输入捕获，pyserial 负责 USB 串口通信。ESP32-C3 固件通过 PlatformIO 使用 ESP-IDF 构建，内置刷机辅助程序使用 esptool。Windows 程序由 PyInstaller 打包，安装包由 Inno Setup 生成。

| 工具或依赖 | 项目使用版本 |
| --- | --- |
| Python | 3.12.10，Windows x64 |
| PySide6-Essentials / Qt | 6.11.2 |
| pynput | 1.8.2 |
| pyserial | 3.5 |
| esptool | 5.4.0 |
| PyInstaller | 6.22.3 |
| PlatformIO Core | 6.2.0 |
| PlatformIO Espressif32 平台 | 7.1.3 |
| ESP-IDF | 6.1.0 |
| Inno Setup | 7.1.0 |

桌面直接依赖固定在 `open_bridge/requirements-desktop.txt`，完整构建环境记录在 `open_bridge/requirements-desktop-lock.txt`。固件配置位于 `open_bridge/platformio.ini` 及配套 `sdkconfig` 文件。

本仓库保留 MouseLink 应用与固件源码、测试、构建脚本、文档和必要的许可证声明。生成的固件与安装包、运行时、缓存、个人设备校准、日志、备份、历史调研及无关的完整上游仓库均不进入 Git。公开校准模板不包含私人设备身份。

安装 Python 3.12 后，在仓库根目录使用 PowerShell 执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r .\open_bridge\requirements-desktop-lock.txt platformio==6.2.0
.\tools\Build-Firmware.ps1 -Python .\.venv\Scripts\python.exe -PublishBundle .\open_bridge\firmware
.\.venv\Scripts\python.exe .\tools\Collect-Licenses.py
.\tools\Build-Desktop.ps1 -Python .\.venv\Scripts\python.exe
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

首次固件构建会下载固定版本的 PlatformIO/ESP-IDF 工具链。脚本将固件源码放入路径仅含 ASCII 字符的独立构建目录；需要指定目录时，可增加 `-BuildRoot C:\MouseLinkBuild`。`-PublishBundle` 会从同一次构建复制启动程序、分区表和应用，并生成大小/SHA-256 清单；不传该参数则仅构建，不覆盖本地固件包。不要混合不同构建的组件。新编译固件仍须实机验收，构建与哈希检查成功不能证明 iPad 操作效果。

当前源码的桌面程序输出为 `release/1.0.3/MouseLink/MouseLink.exe`。生成安装包时，请安装 Inno Setup 7.1.0 后运行构建脚本，并按实际安装位置调整编译器路径：

```powershell
.\tools\Build-Installer.ps1 -CompilerPath "C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
```

安装包输出至 `release/1.0.3/`。这些生成文件不进入 Git。依赖固件包的测试须先生成固件包；界面预览、自动化检查、用户实机验收和干净机器验证分别进行。

## 来源与许可证

MouseLink 基于 [KMChris/esp32-kvm](https://github.com/KMChris/esp32-kvm)，固定提交为 `99c52bc36128867d2a7ed85417c1043a7d06ed5c`，增加了 ESP32-C3 支持、MouseLink 桌面界面、切换逻辑、绝对定位传输及内置备份刷机流程。绝对鼠标 HID 描述参考校验了 [Neradoc/CircuitPython_Absolute_Mouse](https://github.com/Neradoc/CircuitPython_Absolute_Mouse)。

保留上游版权与 MIT 许可证声明。刷机辅助程序及其 esptool 集成采用 GPL-2.0-or-later 条款；Qt/PySide6、pynput 等依赖保留各自许可证。各组件的详细声明见 [LICENSE](open_bridge/LICENSE)、[THIRD_PARTY_NOTICES.md](open_bridge/THIRD_PARTY_NOTICES.md) 及 `open_bridge/licenses/` 中的许可证文件。
