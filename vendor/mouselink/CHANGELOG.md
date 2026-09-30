# Changelog / 更新记录

## 1.0.3

- Make pointer-position checks optional, including first use and changed boards. / 取消首次及后续使用的强制横竖屏检查，保留可选排查入口。
- Remember left/right iPad placement, with matching entry, return and edge gestures. / 新增左右摆放按钮并记住选择，同步切换进入、返回及边缘操作方向。
- Switch placement while running by releasing input and restarting in local standby. Stop, close, flashing or failed reconnection cancels resumption. / 运行中换边先释放键鼠，再恢复本地待机；停止、关闭、刷机或重连失败会取消自动恢复。
- Reuse the existing v3 firmware; this update needs no board reflash. / 沿用现有 v3 固件，此次更新无需重刷开发板。
- The user accepted this version on their device. Keep 1.0.2 for rollback; the intermittent Dock limitation remains. / 用户已完成本机实机验收，保留 1.0.2 用于回退；Dock 偶发问题仍存在。

## 1.0.2

- Enable the restored mixed absolute/relative mouse implementation in normal desktop use, without a session timer. / 日常入口启用已回退确认的混合鼠标逻辑，不限使用时长。
- Retain left-edge return, optional return hotkey, locked mode, and emergency input release. / 保留左边缘返回、可选快捷键返回、锁定模式和紧急释放。
- Remove the requirement to enable AssistiveTouch. / 移除必须开启辅助触控的提示。
- Ship a blank calibration template; keep personal calibration and device backups out of the public source and installer. / 使用空白校准模板，个人校准与设备备份不进入公开源码及安装包。
- Consolidate the two Windows/iPad keyboard-and-mouse repositories, with bilingual installation, usage, firmware and build instructions. / 合并两个 Windows/iPad 键鼠仓库，补充中英安装、使用、刷机及构建说明。

Known limitation / 已知限制：The Dock can occasionally fail to appear or close during bottom-edge input; move back into the screen and retry. 底部推动偶尔无法调出 Dock，或 Dock 收起导致退出失败，请移回屏内后重试。This is not a guarantee of identical native Bluetooth mouse behavior. 不保证所有操作与原生蓝牙鼠标完全一致。
