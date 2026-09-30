# MouseLink source / MouseLink 源码

See [English instructions](../README.md) or [简体中文说明](../README.zh-CN.md) for devices, installation, use, firmware flashing and development.

- `desktop_app.py`: PySide6 desktop interface / 桌面界面。
- `bridge.py`, `native_edges.py`, `trial_bridge.py`: Windows input and mixed mouse transport / Windows 输入与混合鼠标通道。
- `main/`: ESP32-C3 BLE HID firmware source / 开发板固件源码。
- `flash_helper.py`, `firmware_bundle.py`, `flash_dialog.py`: backup, flashing and restoration / 备份、刷写与恢复。
- `device-calibration.json`: blank public template; personal settings stay local / 公开空白模板，个人设置保存在本机。
- `firmware/manifest.json`: firmware package metadata; generate binaries using `tools/Build-Firmware.ps1` / 固件元数据，二进制需从源码构建。

Version 1.0.3 has passed the user's device acceptance. Keep the existing 1.0.2 installer for rollback; clean-machine compatibility remains unverified. 1.0.3 已通过用户实机验收；保留 1.0.2 安装包用于回退，干净机器兼容性尚未验证。

Pointer-position checks are optional. Left/right placement is saved and can be changed while running; see the linked instructions for entry, return and cancellation behavior. These changes reuse the existing v3 firmware. 光标检查改为可选，支持保存左右摆放及运行中换边；进入、返回和取消规则见上方说明，此次改动无需重刷现有 v3 固件。

The normal desktop entry has no session timer. The explicit `--native-edges` diagnostic entry retains its isolated settings and bounded sessions; changing sides does not reset its timer. 日常入口不限时；显式诊断入口仍使用隔离设置和限时会话，换边不会重置计时。
