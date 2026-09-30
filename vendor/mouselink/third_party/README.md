# 第三方文件说明

## packages

- `dshare-hid-1.26.0-win-x64-portable.7z`：首选硬件桥验证程序的官方发布包。
- `BleHid-Cli-v0.4.0-win-x64.zip`：无硬件备选的官方发布包。
- `esp32-hid-factory-1.2.0.fzip`：DShare 官方 v1.2.0 出厂固件包，仅下载留档，尚未刷写。
- `esp32-hid-upgrade-1.2.0.uzip`：DShare 官方 v1.2.0 升级包，仅下载留档，尚未使用。

## apps

上述发布包的解压副本。两套可执行文件都没有 Authenticode 签名。2026-09-10 经用户明确确认后启动了 DShare-HID 便携程序；当前出现“DShare-HID 致命错误”窗口，未进行固件刷写。

## sources

- DShare-HID v1.26.0 源码快照；其 ESP32 固件工具子模块指向未公开仓库，不能把此快照当作完整固件源码。
- windows-ble-hid v0.4.0 源码快照。
- OpenSpan 固定提交源码快照。
- Pico USB-BLE HID Bridge 固定提交源码快照。
- KMChris/esp32-kvm 固定提交 `99c52bc` 的 Git 工作树；MIT 许可，作为开放固件和 Windows Raw Input 串口桥基础，保持未修改。

完整哈希和来源见 `research/source-manifest.json`。

运行前可执行 `tools/Verify-ThirdParty.ps1` 复核全部文件；日常启动应使用项目根目录的 `启动DShare-HID.cmd`，不要直接双击第三方可执行文件。

## 使用约束

- 不直接运行未知更新版本；更新时重新记录下载地址、版本和 SHA-256。
- 不运行 MSI，不设置开机启动，直至实体硬件验收通过。
- DShare-HID/Deskflow 衍生代码受 GPL-2.0 约束；windows-ble-hid 与 OpenSpan 为 MIT。若后续合并或发布衍生程序，必须重新进行许可证审查。
- 申请 DShare-HID 试用固件会向项目方发送设备和联系信息，必须由用户明确操作或授权。
- DShare Factory Flash 会永久转换开发板、排除非 DShare 固件，属于不可逆操作；下载固件不等于授权刷写，刷写前必须另行取得用户的知情确认。
- DShare 最低完整授权 9.99 美元超过用户的人民币 50 元上限，路线已停止；不得刷写已下载的 DShare `.fzip/.uzip` 文件。
