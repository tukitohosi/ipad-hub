"""Approved desktop shell bound to the two real, isolated engines."""
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QInputDialog,
                               QLabel, QMessageBox, QPushButton, QWidget)

from . import __version__
from .app import HubWindow, NAMES, QUALITY_PRESETS, button, label
from .model import Mode, State
from .runtime import RuntimeController, bridge_modules, engine_command
from .speed_wizard import SpeedWizard, calibration_backend


def bounded_int(value, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(value)))
    except (ValueError, TypeError, OverflowError):
        return default


class LiveHubWindow(HubWindow):
    def __init__(self, storage, migrate=True, controller_factory=RuntimeController):
        self.storage = storage
        self._loading = True
        self._quality_reconnecting = False
        self.runtime = None
        self._display_move_pending = None
        self._display_move_error = ""
        self.flash_dialog = None
        self.speed_wizard = None
        self.tool_lock = None
        self.resolution_task = None
        self._resolution_after_stop = None
        self._quit_after_tool = False
        self._tool_after_stop = None
        self._closing = False
        self.devices_by_mode = {Mode.DISPLAY: [], Mode.BRIDGE: []}
        super().__init__(preview=False)
        self.setWindowTitle(f"iPadHub · {__version__}")
        self.tray.setToolTip("iPadHub")
        for action in self.tray.contextMenu().actions():
            if "模拟" in action.text():
                action.setText("停止当前功能")
        report = storage.migrate() if migrate else {}
        for directory in (storage.root, storage.bridge_dir, storage.display_dir, storage.logs_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.preferences = storage.load_settings()
        for key in ("theme", "profile", "transport", "selected_device", "selected_target"):
            if key in self.preferences and not isinstance(self.preferences[key], str):
                self.preferences.pop(key)
        bridge_settings = storage.load_json(storage.bridge_dir / "settings.json", {})
        display_settings = storage.load_json(storage.display_dir / "config.json", {})
        self.runtime = controller_factory(storage, self)
        self.session = self.runtime
        self.runtime.changed.connect(self.refresh)
        self.runtime.devices.connect(self._devices_found)
        self.runtime.engine_event.connect(self._engine_event)
        self.runtime.stopped.connect(self._stopped)
        self.discovery_buttons = []
        for mode, controls in self.mode_controls.items():
            controls["device"].clear()
            controls["device"].addItem("点击刷新，查找可连接的设备", None)
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            refresh = button("刷新设备", lambda checked=False, m=mode: self.discover(m), glyph="refresh")
            self.discovery_buttons.append(refresh)
            layout.addWidget(refresh)
            if mode == Mode.DISPLAY:
                manual = button("手动地址", self.add_manual_device, glyph="wifi")
                self.manual_button = manual
                layout.addWidget(manual)
            layout.addStretch()
            controls["form"].insertRow(1, "", row)
            controls["device"].currentIndexChanged.connect(lambda _: self.save_preferences())
        self.mode_controls[Mode.DISPLAY]["advanced"].addRow(button("Windows 显示设置", self.open_display_settings, glyph="display"))
        self.resolution_button = button("配置副屏分辨率…", self.register_resolution, glyph="settings")
        self.mode_controls[Mode.DISPLAY]["advanced"].addRow(self.resolution_button)
        self.hotkey_toggle = QCheckBox("自由模式也允许 Ctrl + 左 Alt 紧急返回")
        self.hotkey_toggle.setChecked(self.preferences.get("hotkey_return_enabled", bridge_settings.get("hotkey_return_enabled", True)))
        self.mode_controls[Mode.BRIDGE]["advanced"].addRow(self.hotkey_toggle)
        self.hotkey_toggle.toggled.connect(lambda _: self.save_preferences())
        self.speed_control.setRange(25, 150)
        self.speed_control.setValue(bounded_int(self.preferences.get("speed", bridge_settings.get("speed", 50)), 50, 25, 150))
        self._setup_speed_matching(bridge_settings)
        self.position_choice.setCurrentText("电脑左侧" if self.preferences.get("ipad_side", bridge_settings.get("ipad_side")) == "left" else "电脑右侧")
        self.bridge_mode_choice.setCurrentText("锁定模式" if self.preferences.get("bridge_mode", bridge_settings.get("mode")) == "locked" else "自由模式")
        self.transport_choice.setCurrentText({"auto": "自动", "usb": "USB", "wifi": "Wi-Fi"}.get(self.preferences.get("transport"), "自动"))
        self._show_display_side(self.preferences.get("display_side"))
        self.display_side_choice.currentTextChanged.connect(self._choose_display_side)
        self.custom_fps = bounded_int(self.preferences.get("custom_fps", display_settings.get("fps", 60)), 60, 30, 120)
        self.custom_bitrate = bounded_int(self.preferences.get("custom_bitrate", display_settings.get("bitrateMbps", 30)), 30, 5, 100)
        # Old display configs without a streamProfile use non-default numeric
        # values to indicate custom quality, as in the native config parser.
        default_id = 3 if (display_settings.get("fps", 60), display_settings.get("bitrateMbps", 30)) != (60, 30) else 0
        imported_id = bounded_int(display_settings.get("streamProfile"), default_id, 0, 3)
        profile = self.preferences.get("profile") or ("balanced", "smooth", "sharp", "custom")[imported_id]
        old_custom_values = ("custom_fps" in self.preferences and "custom_bitrate" in self.preferences
                             and (self.custom_fps, self.custom_bitrate) != (60, 30))
        has_custom = self.preferences.get("has_custom_quality", imported_id == 3 or old_custom_values)
        if profile == "custom" or has_custom:
            self.quality_choice.addItem("自定义", f"{self.custom_fps} 帧\n{self.custom_bitrate} Mbps")
        profile_labels = {key: name for name, (key, _) in QUALITY_PRESETS.items()}
        self.quality_choice.setCurrentText("自定义" if profile == "custom" else profile_labels.get(profile, "均衡"))
        self.quality_hint = label("", "hint")
        self.mode_controls[Mode.DISPLAY]["form"].addRow("", self.quality_hint)
        self.priority_choice.setCurrentIndex(0 if self.preferences.get("remember_device", True) else 1)
        self.theme_box.setCurrentText({"system": "跟随系统", "light": "浅色", "dark": "深色"}.get(self.preferences.get("theme"), "跟随系统"))
        self.set_theme(self.theme_box.currentText())
        self.quality_choice.currentTextChanged.connect(self._quality_changed)
        for control in (self.position_choice, self.transport_choice, self.bridge_mode_choice):
            control.currentTextChanged.connect(lambda _: self.save_preferences())
        self.speed_control.valueChanged.connect(lambda _: self.save_preferences())
        self.priority_choice.currentIndexChanged.connect(lambda _: self.save_preferences())
        remembered = []
        imported_devices = display_settings.get("devices", [])
        for item in imported_devices if isinstance(imported_devices, list) else []:
            if isinstance(item, dict):
                target = item.get("lastIpv4") or item.get("bonjourHost") or ""
                if isinstance(target, str) and target:
                    remembered.append(dict(id=str(item.get("id") or ""), name=str(item.get("name") or "已记录的 iPad"),
                                           target=target, port=bounded_int(item.get("port"), 9000, 1, 65535), transport="usb" if target.startswith("usb:") else "wifi", online=False))
        saved_target = self.preferences.get("selected_target")
        if saved_target and not any(d["target"] == saved_target for d in remembered):
            remembered.append(dict(id=self.preferences.get("selected_device", ""), name="上次选择的 iPad",
                                   target=saved_target, port=9000, transport="usb" if saved_target.startswith("usb:") else "wifi", online=False))
        self._devices_found(Mode.DISPLAY, remembered)
        self._loading = False
        self.update_button_icons()
        if report.get("errors") or report.get("warnings"):
            self.top_hint.setText("部分旧数据未能导入，可继续使用默认设置；详情见数据目录中的迁移记录。")
        self.refresh()

    def _bridge_options(self):
        options = dict(mode="locked" if self.bridge_mode_choice.currentText() == "锁定模式" else "free",
                       speed=self.speed_control.value() / 100,
                       ipad_side="left" if self.position_choice.currentText() == "电脑左侧" else "right",
                       hotkey_return_enabled=self.hotkey_toggle.isChecked())
        if self._has_profile_record:
            options["pointer_profile"] = self.pointer_profile if self.speed_match_toggle.isChecked() else None
        return options

    def _setup_speed_matching(self, bridge_settings):
        profile = self.preferences.get("pointer_profile", bridge_settings.get("pointer_profile"))
        self._has_profile_record = profile is not None
        self.pointer_profile = None
        if isinstance(profile, dict):
            try:
                self.pointer_profile = calibration_backend().validate_profile(profile)
            except ValueError:
                pass
        self.speed_match_toggle = QCheckBox("使用已校准速度（100% 匹配电脑）")
        self.speed_match_toggle.setToolTip("更换鼠标、切换 iPad 横竖屏或调整 Windows 指针速度后，请重新校准。")
        enabled = bool(self.pointer_profile) and self.preferences.get("pointer_profile_enabled", True) is True
        self.speed_match_toggle.setChecked(enabled)
        self.speed_match_toggle.setVisible(bool(self.pointer_profile))
        self.speed_match_toggle.toggled.connect(self._toggle_speed_match)
        if self._has_profile_record and not enabled:
            self.speed_control.setValue(bounded_int(self.preferences.get("manual_speed", 50), 50, 25, 150))
        self.calibrate_speed_button = button("匹配电脑鼠标速度…", self.calibrate_speed, glyph="settings")
        form = self.mode_controls[Mode.BRIDGE]["advanced"]
        form.insertRow(1, self.calibrate_speed_button)
        form.insertRow(2, self.speed_match_toggle)

    def _toggle_speed_match(self, enabled):
        key = "matched_speed" if enabled else "manual_speed"
        default = 100 if enabled else 50
        self.speed_control.setValue(bounded_int(self.preferences.get(key, default), default, 25, 150))
        self.save_preferences()

    def calibrate_speed(self):
        s = self.runtime
        if self.speed_wizard or s.busy_tool or s.release_blocked or self._closing or s.state in (State.ACTIVE, State.CONNECTING, State.STOPPING):
            return
        try:
            dialog = SpeedWizard(self, previous_geometry=self.preferences.get("pointer_geometry"))
        except (ImportError, OSError, RuntimeError) as error:
            QMessageBox.warning(self, "速度校准无法打开", str(error))
            return
        self.speed_wizard = dialog
        s.busy_tool = True
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.finished.connect(self._speed_wizard_finished)
        dialog.open()
        self.refresh()

    def _speed_wizard_finished(self, result):
        dialog, self.speed_wizard = self.speed_wizard, None
        self.runtime.busy_tool = False
        if result == SpeedWizard.DialogCode.Accepted and dialog.profile:
            if not self.speed_match_toggle.isChecked():
                self.preferences["manual_speed"] = self.speed_control.value()
            self.pointer_profile = dict(dialog.profile)
            self.preferences["pointer_geometry"] = dialog.geometry_settings
            board = self.runtime.snapshot.get("device_serial") if self.runtime.owner == Mode.BRIDGE else None
            if board:
                self.pointer_profile["board_serial"] = board
            self._has_profile_record = True
            self.speed_match_toggle.blockSignals(True)
            self.speed_match_toggle.setChecked(True)
            self.speed_match_toggle.blockSignals(False)
            self.speed_match_toggle.show()
            self.top_hint.setText("鼠标速度校准已完成，开启键鼠控制后使用。")
            if self.speed_control.value() == 100:
                self.save_preferences()
            else:
                self.speed_control.setValue(100)
        self.refresh()

    def _show_display_side(self, side):
        choice = self.display_side_choice
        previous = choice.blockSignals(True)
        try:
            if side in ("left", "right"):
                choice.setCurrentText("电脑左侧" if side == "left" else "电脑右侧")
            else:
                choice.clearSelection()
        finally:
            choice.blockSignals(previous)

    def _choose_display_side(self, name):
        if self._loading:
            return
        side = "left" if name == "电脑左侧" else "right"
        self._display_move_error = ""
        if self.runtime.owner == Mode.DISPLAY and self.runtime.state == State.ACTIVE:
            self._display_move_pending = side
            if self.runtime.request(Mode.DISPLAY, "set_display_side", {"display_side": side}):
                return
            self._display_move_pending = None
            self._display_move_error = "暂时无法移动副屏，请等待连接稳定后重试。"
            self._show_display_side(self.runtime.snapshot.get("display_side"))
        else:
            self.preferences["display_side"] = side
            self.save_preferences()
        self.refresh()

    def _display_session_options(self):
        options = self._quality_options()
        side = self.preferences.get("display_side")
        if side in ("left", "right"):
            options["display_side"] = side
        return options

    def _quality_options(self):
        name = self.quality_choice.currentText()
        if name == "自定义":
            return dict(fps=self.custom_fps, bitrate_mbps=self.custom_bitrate)
        profile, bitrate = QUALITY_PRESETS[name]
        return dict(profile=profile, fps=60, bitrate_mbps=bitrate)

    def _quality_changed(self, _name):
        if self._loading:
            return
        self.save_preferences()
        self.runtime.reconnect_display(self._display_session_options())

    def _display_options(self):
        controls = self.mode_controls[Mode.DISPLAY]
        selected = controls["device"].currentData() or {}
        identity = selected.get("receiver_id", selected.get("id", ""))
        if identity.startswith(("wifi:", "usb:")):
            identity = ""
        transport = {"自动": "auto", "USB": "usb", "Wi-Fi": "wifi"}[self.transport_choice.currentText()]
        return dict(target=selected.get("target", ""), device_id=identity,
                    port=selected.get("port", 9000), transport=transport, **self._display_session_options(),
                    preferred_device_id=identity if self.priority_choice.currentIndex() == 0 else "",
                    require_private_network=True)

    def save_preferences(self):
        if self._loading or not self.runtime:
            return
        bridge = self._bridge_options()
        display = self._display_options()
        self.preferences.update(theme={"跟随系统": "system", "浅色": "light", "深色": "dark"}[self.theme_choice],
            speed=self.speed_control.value(), ipad_side=bridge["ipad_side"], bridge_mode=bridge["mode"],
            hotkey_return_enabled=bridge["hotkey_return_enabled"],
            transport=display["transport"], profile=display.get("profile", "custom"),
            custom_fps=self.custom_fps, custom_bitrate=self.custom_bitrate, has_custom_quality="自定义" in self.quality_choice.items,
            remember_device=self.priority_choice.currentIndex() == 0,
            selected_device=display["device_id"], selected_target=display["target"])
        if self.pointer_profile:
            enabled = self.speed_match_toggle.isChecked()
            self.preferences.update(pointer_profile=self.pointer_profile, pointer_profile_enabled=enabled)
            self.preferences["matched_speed" if enabled else "manual_speed"] = self.speed_control.value()
        try:
            self.storage.save_settings(self.preferences)
        except OSError as exc:
            self.top_hint.setText(f"设置暂未保存：{exc}")

    def set_theme(self, name):
        super().set_theme(name)
        if not self._loading:
            self.save_preferences()

    def discover(self, mode):
        self._tool_after_stop = self._resolution_after_stop = None
        self.save_preferences()
        self.runtime.request(mode, "discover")

    def start_session(self, mode):
        self._tool_after_stop = self._resolution_after_stop = None
        self.save_preferences()
        if mode == Mode.DISPLAY:
            if self.runtime.can_retry_display:
                self.runtime.reconnect_display(self._display_session_options(), retry=True)
                return
            options = self._display_options()
            if not options["target"]:
                self.discover(mode)
                self.top_hint.setText("请先刷新并选择 iPad，再点击连接；也可以手动输入接收端地址。")
                return
        else:
            options = self._bridge_options()
            selected = self.mode_controls[Mode.BRIDGE]["device"].currentData() or {}
            if selected.get("port"):
                options["port"] = selected["port"]
        self.runtime.request(mode, "start", options)

    def stop_session(self):
        self._tool_after_stop = self._resolution_after_stop = None
        if self.runtime:
            self.runtime.stop()

    def recover_session(self):
        if self.runtime.recover():
            return
        box = QMessageBox(self)
        box.setWindowTitle("检查资源状态")
        box.setText("后台已异常退出，请先确认键鼠可正常使用，并在 Windows 显示设置中检查显示器状态。")
        box.setInformativeText("不会自动删除显示器或关闭其他应用。确认环境正常后，可重新启用连接。")
        settings = box.addButton("打开显示设置", QMessageBox.ButtonRole.ActionRole)
        checked = box.addButton("已检查，允许重试", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == settings:
            self.open_display_settings()
        elif box.clickedButton() == checked:
            self.runtime.release_blocked = False
            self.runtime.state = State.READY
            self.runtime.detail = "已由你确认资源状态，可重新连接。"
            self.runtime.changed.emit()

    def _devices_found(self, mode, devices):
        if mode not in self.mode_controls:
            return
        controls = self.mode_controls[mode]
        previous = controls["device"].currentData() or {}
        use_history = mode == Mode.DISPLAY and self.preferences.get("remember_device", True)
        target = previous.get("target") or (self.preferences.get("selected_target") if use_history else None)
        identity = previous.get("receiver_id", previous.get("id")) or (self.preferences.get("selected_device") if use_history else None)
        if identity and identity.startswith(("wifi:", "usb:")):
            identity = None
        self.devices_by_mode[mode] = devices
        box = controls["device"]
        box.blockSignals(True)
        box.clear()
        if not devices:
            box.addItem("未发现接收端，请打开 OpenDisplay 后刷新" if mode == Mode.DISPLAY else "未检测到 ESP32-C3 开发板", None)
        match_index = -1
        for item in devices:
            if not isinstance(item, dict):
                continue
            text = item.get("name") or ("ESP32-C3" if mode == Mode.BRIDGE else "iPad")
            if mode == Mode.DISPLAY:
                text += " · " + ("USB" if item.get("transport") == "usb" else "Wi-Fi")
                if item.get("online") is False:
                    text += "（已记录）"
            else:
                text += " · " + item.get("port", "")
            box.addItem(text, item)
            if identity and item.get("receiver_id", item.get("id")) == identity:
                match_index = box.count() - 1
            elif item.get("target") == target and target:
                box.setCurrentIndex(box.count() - 1)
        if match_index >= 0:
            box.setCurrentIndex(match_index)
        box.blockSignals(False)
        box.setToolTip(box.currentText())
        self.refresh()

    def add_manual_device(self):
        target, accepted = QInputDialog.getText(self, "手动连接 iPad", "输入 OpenDisplay 显示的 IPv4 地址或主机名（端口 9000）：")
        target = target.strip()
        if not accepted or not target:
            return
        if len(target) > 253 or any(c.isspace() for c in target) or any(c in target for c in '/\\"'):
            QMessageBox.warning(self, "地址格式不正确", "请输入接收端的地址或主机名，不包含协议前缀或路径。")
            return
        devices = list(self.devices_by_mode[Mode.DISPLAY])
        devices.append(dict(id="", name="手动添加的 iPad", target=target, transport="wifi", port=9000, online=False))
        self._devices_found(Mode.DISPLAY, devices)
        self.mode_controls[Mode.DISPLAY]["device"].setCurrentIndex(len(devices) - 1)
        self.transport_choice.setCurrentText("Wi-Fi")
        self.save_preferences()

    def show_info(self, title, text):
        if title.startswith("可选校准"):
            self.calibrate()
        elif title.startswith("连接诊断"):
            snapshot = self.runtime.snapshot if self.runtime and self.runtime.owner == Mode.DISPLAY else {}
            details = self.runtime.detail if snapshot else "副屏后台尚未启动。请在 iPad 打开 OpenDisplay，再刷新设备。"
            fields = [f"{name}：{snapshot[key]}" for key, name in (("transport", "连接方式"), ("width", "宽度"),
                ("height", "高度"), ("target_fps", "目标帧率"), ("target_bitrate_mbps", "目标码率（Mbps）"),
                ("failure", "错误代码")) if key in snapshot]
            super().show_info("连接诊断", details + ("\n\n" + "\n".join(fields) if fields else "") + "\n\n副屏需要 Windows 虚拟显示驱动。可在 Windows 显示设置中检查；安装包不安装或替换驱动。")
        else:
            super().show_info(title, text)

    def calibrate(self):
        if self.runtime.owner != Mode.BRIDGE or not self.runtime.snapshot.get("ready") or self.runtime.state == State.ACTIVE:
            QMessageBox.information(self, "先检查连接", "请先停止键鼠控制、刷新开发板，确认蓝牙和定位通道就绪后再校准。")
            return
        direction, accepted = QInputDialog.getItem(self, "检查光标位置", "请解锁 iPad 并转到所选方向。光标会经过七个位置，不会点击。", ["横屏", "竖屏"], 0, False)
        if accepted:
            self.runtime.request(Mode.BRIDGE, "calibrate", dict(self._bridge_options(), orientation="landscape" if direction == "横屏" else "portrait"))

    def _engine_event(self, mode, event, payload):
        if mode == Mode.DISPLAY and event == "response:set_display_side":
            requested, self._display_move_pending = self._display_move_pending, None
            if payload.get("ok") and requested:
                self.preferences["display_side"] = requested
                self.save_preferences()
            elif requested:
                self._display_move_error = payload.get("error") or "Windows 未能移动副屏，请重新选择位置。"
                self._show_display_side(self.runtime.snapshot.get("display_side"))
        elif event == "calibration":
            if payload.get("confirmation_required"):
                answer = QMessageBox.question(self, "确认校准结果", "七个光标位置是否正确，并且边缘位置真正贴边？")
                self.runtime.request(Mode.BRIDGE, "calibrate", dict(orientation=payload.get("orientation", "landscape"), confirmed=answer == QMessageBox.StandardButton.Yes))
            elif payload.get("ok") is False or payload.get("success") is False:
                QMessageBox.warning(self, "校准未完成", payload.get("detail") or payload.get("message") or "请检查开发板和 iPad 连接。")
        elif event == "response:discover" and payload.get("ok"):
            body = payload.get("payload", {})
            if "devices" in body:
                self._devices_found(mode, body["devices"])

    def refresh(self):
        if self.runtime is None:
            return super().refresh()
        s = self.runtime
        if s.release_blocked:
            self._tool_after_stop = self._resolution_after_stop = None
        busy = s.state in (State.CONNECTING, State.STOPPING)
        if s.changing_display_quality:
            self._quality_reconnecting = True
        elif s.owner != Mode.DISPLAY or s.state != State.CONNECTING:
            self._quality_reconnecting = False
        for mode, c in self.mode_controls.items():
            relevant = mode == s.owner or mode == s.selected
            state = s.state if relevant else State.READY
            detail = s.detail if relevant else "点击刷新设备，检查此用途需要的连接条件。"
            if s.owner and s.owner != mode:
                detail = f"当前{NAMES[s.owner]}后台仍在运行；启动此用途前会完整停止它。"
            if s.release_blocked:
                state, detail = State.FAILED, s.detail
            if s.busy_tool:
                detail = ("正在匹配电脑鼠标速度，完成后可启动连接。" if self.speed_wizard
                          else "设备工具正在使用开发板，请结束操作后再启动连接。")
            c["title"].setText(state.value)
            c["title"].setProperty("tone", state.name.lower())
            c["title"].style().unpolish(c["title"])
            c["title"].style().polish(c["title"])
            c["detail"].setText(detail)
            c["progress"].setVisible(busy and relevant)
            c["start"].setEnabled(not busy and not s.busy_tool and not s.release_blocked and not self._closing and not (s.owner == mode and s.state == State.ACTIVE))
            c["start"].setText(("切换并连接副屏" if mode == Mode.DISPLAY else "切换并开始键鼠") if s.owner and s.owner != mode else ("连接副屏" if mode == Mode.DISPLAY else "开始键鼠控制"))
            if mode == Mode.DISPLAY and s.can_retry_display:
                c["start"].setText("重试原副屏")
            cancel_reconnect = mode == Mode.DISPLAY and s.changing_display_quality
            c["stop"].setText("取消重连" if cancel_reconnect else ("取消" if s.state == State.CONNECTING else "停止"))
            c["stop"].setEnabled(s.engine is not None and not s.busy_tool and (s.state != State.STOPPING or cancel_reconnect))
            c["recovery"].setVisible(s.release_blocked)
            c["device"].setEnabled(not busy and not s.busy_tool and s.state != State.ACTIVE)
            if mode == Mode.DISPLAY and s.can_retry_display:
                c["device"].setEnabled(False)
                c["stop"].setEnabled(True)
        snapshot = s.snapshot if s.owner == Mode.BRIDGE else {}
        self.board_status.setText("USB：" + ("已连接" if snapshot.get("usb") else "未确认") + " · 蓝牙：" + ("已连接" if snapshot.get("ble") else "未确认") + " · 定位：" + ("就绪" if snapshot.get("ready") else "未确认"))
        active_busy = busy or s.state == State.ACTIVE or s.busy_tool or s.release_blocked
        for field in (self.position_choice, self.speed_control, self.bridge_mode_choice, self.transport_choice):
            field.setEnabled(not active_busy)
        if s.can_retry_display:
            self.transport_choice.setEnabled(False)
        if hasattr(self, "hotkey_toggle"):
            self.hotkey_toggle.setEnabled(not active_busy)
        if hasattr(self, "calibrate_speed_button"):
            self.calibrate_speed_button.setEnabled(not active_busy and not self._closing)
            self.speed_match_toggle.setEnabled(not active_busy and not self._closing)
        self.quality_choice.setEnabled(not self._closing and not self._display_move_pending and not s.busy_tool and not s.release_blocked
                                       and (not busy or s.changing_display_quality)
                                       and (s.owner != Mode.DISPLAY or s.state != State.ACTIVE or s.has_display_endpoint))
        hint = ""
        if self._quality_reconnecting:
            hint = f"正在以「{self.quality_choice.currentText()}」画质重新连接副屏…"
        elif s.can_retry_display:
            hint = "可重试原副屏，或点击停止后选择其他设备。"
        elif s.owner == Mode.DISPLAY and s.state == State.ACTIVE:
            hint = "切换画质会短暂中断画面，并自动重新连接。" if s.has_display_endpoint else "等待确认接收端地址后可切换画质。"
            if "target_fps" in s.snapshot and "target_bitrate_mbps" in s.snapshot:
                hint = f"当前目标 {s.snapshot['target_fps']} 帧 / {s.snapshot['target_bitrate_mbps']} Mbps。{hint}"
        self.quality_hint.setText(hint)
        self.quality_hint.setVisible(bool(hint))
        for b in getattr(self, "discovery_buttons", []):
            b.setEnabled(not active_busy and not self._closing and not s.can_retry_display)
        for b in self.tool_buttons:
            b.setEnabled(not busy and not s.busy_tool and not s.release_blocked and not self._closing)
        if hasattr(self, "manual_button"):
            self.manual_button.setEnabled(not active_busy and not s.can_retry_display)
        if hasattr(self, "resolution_button"):
            self.resolution_button.setEnabled(not busy and not s.busy_tool and not s.release_blocked and not self._closing)
        if s.owner != Mode.DISPLAY or s.state != State.ACTIVE:
            self._display_move_pending = None
            self._display_move_error = ""
            self._show_display_side(self.preferences.get("display_side"))
        elif not self._display_move_pending and "display_side" in s.snapshot:
            self._show_display_side(s.snapshot["display_side"])
        self.display_side_choice.setEnabled(not (self._closing or busy or s.release_blocked
                                                or s.busy_tool or self._display_move_pending))
        placement_error = self._display_move_error or (s.snapshot.get("display_side_error", "") if s.owner == Mode.DISPLAY else "")
        if placement_error:
            self.mode_controls[Mode.DISPLAY]["detail"].setText(placement_error)
        self.footer.setText(f"{NAMES[s.owner] if s.owner else '无活动用途'} · {s.state.value}  |  iPadHub {__version__}")

    def demo_tool(self, title):
        if self.runtime.busy_tool:
            return
        if title == "设备诊断":
            self.navigate(2)
            self.discover(Mode.BRIDGE)
            return
        self._tool_after_stop = title
        self.runtime.stop()

    def _stopped(self):
        if self._closing:
            self._quitting = True
            self.tray.hide()
            QApplication.quit()
        elif self._tool_after_stop:
            title, self._tool_after_stop = self._tool_after_stop, None
            self._open_firmware_tools(title)
        elif self._resolution_after_stop:
            size, self._resolution_after_stop = self._resolution_after_stop, None
            self._run_resolution_task(*size)

    def _open_firmware_tools(self, title):
        if self.runtime.engine or self.runtime.release_blocked:
            return
        module_dir = str(bridge_modules())
        if module_dir not in sys.path:
            sys.path.insert(0, module_dir)
        try:
            from .engines.pipe_client import BridgeResourceLock
            self.tool_lock = BridgeResourceLock()
            from flash_dialog import FlashDialog
            dialog = FlashDialog(self.storage.root, self)
        except Exception as exc:
            if self.tool_lock:
                self.tool_lock.close()
                self.tool_lock = None
            QMessageBox.warning(self, "设备工具无法打开", str(exc))
            return
        self.runtime.busy_tool = True
        self.flash_dialog = dialog
        dialog.setWindowTitle("iPadHub · 固件备份与恢复")
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.finished.connect(self._tools_closed)
        dialog.show()
        if title == "备份固件":
            dialog.status.setText("选择开发板后，点击“仅备份固件”；不会刷写。")
            dialog.backup_button.setFocus()
        elif title == "恢复备份":
            dialog.status.setText("点击“恢复备份”并选择这块开发板的备份记录。恢复前会再次备份。")
            dialog.restore_button.setFocus()
        self.refresh()

    def _tools_closed(self):
        self.flash_dialog = None
        if self.tool_lock:
            self.tool_lock.close()
            self.tool_lock = None
        self.runtime.busy_tool = False
        self.refresh()
        if self._quit_after_tool:
            self._quit_after_tool = False
            self.quit_app()

    def reset_preview(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.storage.root)))

    @staticmethod
    def open_display_settings():
        QDesktopServices.openUrl(QUrl("ms-settings:display"))

    def register_resolution(self):
        snapshot = self.runtime.snapshot if self.runtime.owner == Mode.DISPLAY else {}
        width, accepted = QInputDialog.getInt(self, "配置副屏分辨率", "输入接收端报告的宽度（偶数）。此操作需要 Windows 管理员确认。", int(snapshot.get("width") or 1600), 320, 8192, 2)
        if not accepted:
            return
        height, accepted = QInputDialog.getInt(self, "配置副屏分辨率", "输入接收端报告的高度（偶数）。完成后请重新连接副屏。", int(snapshot.get("height") or 1200), 320, 8192, 2)
        if not accepted:
            return
        if width % 2 or height % 2:
            QMessageBox.warning(self, "分辨率不正确", "宽度和高度需要是 320 至 8192 之间的偶数。")
            return
        self._resolution_after_stop = (width, height)
        self.runtime.stop()

    def _run_resolution_task(self, width, height):
        from .maintenance import ResolutionTask
        self.runtime.busy_tool = True
        self.resolution_task = ResolutionTask(engine_command(Mode.DISPLAY)[0], width, height, self)
        self.resolution_task.finished.connect(self._resolution_finished)
        self.resolution_task.progress.connect(self.top_hint.setText)
        self.refresh()
        self.resolution_task.start()

    def _resolution_finished(self, ok, detail):
        self.resolution_task.deleteLater()
        self.resolution_task = None
        self.runtime.busy_tool = False
        self.top_hint.setText(detail)
        self.refresh()
        if self._quit_after_tool:
            self._quit_after_tool = False
            self.quit_app()

    def quit_app(self):
        if self.runtime is None:
            return super().quit_app()
        if self.speed_wizard is not None:
            self.speed_wizard.reject()
        if self.resolution_task is not None:
            self._quit_after_tool = True
            self.top_hint.setText("分辨率配置结束后会自动退出。")
            return
        if self.flash_dialog is not None:
            self._quit_after_tool = True
            self.flash_dialog.close()
            if self.flash_dialog is not None:
                self.top_hint.setText("固件操作完成后会自动退出，请保持 USB 连接。")
            return
        self._closing = True
        self.save_preferences()
        self.runtime.quit()
        self.refresh()
