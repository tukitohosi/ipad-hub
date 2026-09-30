# iPadHub 第三方组件与对应源码

iPadHub 0.1.0-preview 是本地 Windows x64 整合预览。新增整合代码按 GPL-3.0 发布，完整条款见 `LICENSE`。各组件保留自己的许可和版权；本产品不是全 MIT 项目。

| 组件 | 许可与交付方式 |
| --- | --- |
| iPadHub 界面、后台协调和数据迁移 | GPL-3.0；源码位于配套 source ZIP 的 `ipadhub/`、`packaging/`、`scripts/` |
| iPadHubDisplay / opendisplay-win 衍生发送器 | GPL-3.0；修改后完整源码和构建资料位于 `vendor/ipaddisplay/`；原版权/项目引用见 `licenses/ipaddisplay-notices.txt` |
| MouseLink / KMChris esp32-kvm 键鼠及固件 | 保留 MIT 许可；代码、固件源码与构建配置位于 `vendor/mouselink/open_bridge/`，上游快照及版权在 `vendor/mouselink/third_party/` |
| MouseLinkFlash 与 esptool 5.4.0 | GPL-2.0-or-later；独立进程。`flash_helper.py` 源码和 `licenses/esptool-5.4.0-source.zip` 随包提供 |
| PySide6 / Shiboken6 / Qt 6.11.2 | LGPL-3.0；许可全文在 `licenses/LGPL-3.0-only.txt` 和 `licenses/GPL-3.0-only.txt`，Qt DLL 在 `_internal` 下独立分发 |
| pynput 1.8.2 | LGPL-3.0；许可在 `licenses/pynput-COPYING.LGPL` |
| Python、pyserial、six、其他刷机依赖 | 各组件许可完整保留在 `licenses/`，版本在 `licenses/flash-runtime.json` 与 `licenses/build-environment.txt` |
| PyInstaller 6.22.3 | GPL-2.0-or-later，含 bootloader exception；全文在 `licenses/pyinstaller-COPYING.txt` |
| parsec-vdd.h | 文件中原有 BSD 风格条款，全文随 `licenses/ipaddisplay-notices.txt` 分发；仅为接口头文件 |
| Inno Setup 与简体中文翻译 | Inno 仅用于生成安装包；翻译的 MIT 版权条款在 `licenses/ipaddisplay-notices.txt` |

匹配的 `iPadHub-<version>-source.zip` 与安装包同目录提供，也置于安装/便携目录的 `source/`。它包含修改过的两个功能组件、整合代码、资源、测试、构建与打包脚本。构建按 `docs/packaging.md` 执行。Python 依赖版本固定在 `vendor/mouselink/open_bridge/requirements-desktop-lock.txt`。

Qt/PySide DLL 可替换为兼容的修改版本，本应用不限制为调试这些库的修改而进行的逆向工程。Qt/PySide 对应上游源码：

- PySide/Shiboken：https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.11.2
- Qt：https://code.qt.io/cgit/qt/qtbase.git/tag/?h=v6.11.2
- Qt 第三方声明：https://doc.qt.io/qt-6/licenses-used-in-qt.html
- pynput：https://github.com/moses-palmer/pynput/tree/v1.8.2

**安装包不包含 Parsec 驱动的安装器、驱动二进制、INF 或 CAT，不自动安装/卸载驱动，不修改防火墙。** iPad 接收端仍需用户单独安装 OpenDisplay，当前许可和功能说明不能解释为 Parsec 官方兼容认证。

原组件详细声明完整保留在 `licenses/mouselink-notices.md` 与 `licenses/ipaddisplay-notices.txt`，这些汇总不替换原声明。
