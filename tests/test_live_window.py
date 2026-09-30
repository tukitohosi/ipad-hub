"""Real widgets, fake engines and temporary data: no hardware, driver or UAC."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QPushButton, QSystemTrayIcon

from ipadhub.live_window import LiveHubWindow
from ipadhub.model import Mode, State
from ipadhub.runtime import RuntimeController
from ipadhub.storage import HubStorage


class FakeEngine(QObject):
    message = Signal(dict)
    finished = Signal(bool, str)
    fault = Signal(str)

    def __init__(self, mode, storage, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.session = uuid4().hex
        self.sent = []
        self.started = self.released = False

    def start(self):
        self.started = True

    def send(self, command, payload=None):
        request = uuid4().hex
        self.sent.append((request, command, payload or {}))
        return request

    def event(self, event, payload):
        self.message.emit(dict(v=1, session=self.session, request="", type="event",
                               event=event, payload=payload))

    def release(self):
        self.released = True
        self.event("released", {"clean": True})

    def exit(self):
        self.finished.emit(self.released, "" if self.released else "unconfirmed release")


class LiveWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        for font_name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "segoeuib.ttf"):
            font_path = Path("C:/Windows/Fonts") / font_name
            if font_path.exists():
                QFontDatabase.addApplicationFont(str(font_path))
        cls.app.setFont(QFont("Microsoft YaHei UI", 10))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.storage = HubStorage(self.root / "hub", legacy_bridge=self.root / "old-bridge",
                                  legacy_display=self.root / "old-display", registry=None)
        self.engines, self.windows = [], []
        tray = patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False)
        tray.start()
        self.addCleanup(tray.stop)
        self.addCleanup(self.dispose_windows)

    def dispose_windows(self):
        for window in reversed(self.windows):
            window.runtime.stop_timer.stop()
            window._tool_after_stop = window._resolution_after_stop = None
            window._quitting = True
            window.tray.hide()
            window.close()
            window.deleteLater()
        self.app.processEvents()

    def make_window(self):
        def factory(mode, storage, parent):
            engine = FakeEngine(mode, storage, parent)
            self.engines.append(engine)
            return engine
        def controller(storage, parent):
            return RuntimeController(storage, parent, factory=factory)
        window = LiveHubWindow(self.storage, migrate=False, controller_factory=controller)
        self.windows.append(window)
        return window

    def board_ready(self, window):
        window.discover(Mode.BRIDGE)
        engine = window.runtime.engine
        engine.event("status", {"state": "ready", "ready": True, "usb": True, "ble": True,
                                "running": False, "detail": "ready"})
        return engine

    def test_product_start_has_no_engine_and_no_fake_devices(self):
        with patch.object(self.storage, "migrate") as migration:
            window = self.make_window()
        migration.assert_not_called()
        self.assertEqual(self.engines, [])
        self.assertIsNone(window.runtime.engine)
        self.assertIsNone(window.runtime.owner)
        self.assertEqual(window.pages.currentIndex(), 0)
        self.assertTrue(window.scenario_box.isHidden())
        for controls in window.mode_controls.values():
            device = controls["device"]
            self.assertEqual(device.count(), 1)
            self.assertIsNone(device.currentData())
            self.assertNotIn("演示", device.currentText())
            self.assertNotIn("模拟", controls["detail"].text())
        self.assertNotIn("模拟", window.board_status.text())
        self.assertFalse((self.storage.root / "migration.json").exists())

    def test_valid_json_with_invalid_speed_recovers_without_blocking_startup(self):
        self.storage.save_settings({"speed": "damaged"})
        window = self.make_window()
        self.assertEqual(window.speed_control.value(), 50)
        self.assertEqual(self.engines, [])

    def test_valid_json_with_null_devices_recovers_without_blocking_startup(self):
        self.storage.save_json(self.storage.display_dir / "config.json", {"devices": None})
        window = self.make_window()
        self.assertEqual(window.mode_controls[Mode.DISPLAY]["device"].count(), 1)
        self.assertIsNone(window.mode_controls[Mode.DISPLAY]["device"].currentData())
        self.assertEqual(self.engines, [])

    def test_bridge_start_sends_all_current_options_and_selected_port(self):
        window = self.make_window()
        window.position_choice.setCurrentText("电脑左侧")
        window.speed_control.setValue(85)
        window.bridge_mode_choice.setCurrentText("锁定模式")
        window.hotkey_toggle.setChecked(False)
        window._devices_found(Mode.BRIDGE, [{"id": "bridge:board", "name": "board", "port": "COM7"}])
        window.start_session(Mode.BRIDGE)
        self.assertEqual(len(self.engines), 1)
        _, command, payload = self.engines[0].sent[0]
        self.assertEqual(command, "start")
        self.assertEqual(payload, dict(mode="locked", speed=.85, ipad_side="left",
                                       hotkey_return_enabled=False, port="COM7"))

    def test_display_start_sends_profile_transport_and_stable_identity(self):
        window = self.make_window()
        window._devices_found(Mode.DISPLAY, [{"id": "wifi:192.168.1.5", "receiver_id": "stable-iPad",
            "target": "192.168.1.5", "port": 9011, "transport": "wifi", "name": "iPad"}])
        window.transport_choice.setCurrentText("Wi-Fi")
        window.quality_choice.setCurrentText("清晰")
        window.start_session(Mode.DISPLAY)
        _, command, payload = self.engines[0].sent[0]
        self.assertEqual(command, "start")
        for key, expected in {"target": "192.168.1.5", "device_id": "stable-iPad",
                "preferred_device_id": "stable-iPad", "port": 9011, "transport": "wifi",
                "profile": "sharp", "bitrate_mbps": 45}.items():
            self.assertEqual(payload[key], expected)

    def test_imported_custom_profile_preserves_fps_and_bitrate_across_reopen(self):
        self.storage.save_json(self.storage.display_dir / "config.json",
                               {"streamProfile": 3, "fps": 90, "bitrateMbps": 62})
        window = self.make_window()
        self.assertTrue(window.custom_quality.isChecked())
        options = window._display_options()
        self.assertNotIn("profile", options)
        self.assertEqual(options["fps"], 90)
        self.assertEqual(options["bitrate_mbps"], 62)
        window.save_preferences()
        saved = self.storage.load_settings()
        self.assertEqual(saved["profile"], "custom")
        self.assertEqual(saved["custom_fps"], 90)
        self.assertEqual(saved["custom_bitrate"], 62)
        reopened = self.make_window()
        self.assertTrue(reopened.custom_quality.isChecked())
        self.assertEqual(reopened._display_options()["fps"], 90)
        self.assertEqual(reopened._display_options()["bitrate_mbps"], 62)
        self.assertNotIn("profile", reopened._display_options())

    def test_remembered_stable_receiver_is_selected_after_dhcp_address_changes(self):
        self.storage.save_settings({"remember_device": True, "selected_device": "stable-A",
                                    "selected_target": "192.168.1.10"})
        window = self.make_window()
        window._devices_found(Mode.DISPLAY, [
            dict(id="wifi:192.168.1.10", receiver_id="stable-B", target="192.168.1.10",
                 transport="wifi", name="Other receiver now has old address"),
            dict(id="wifi:192.168.1.20", receiver_id="stable-A", target="192.168.1.20",
                 transport="wifi", name="Remembered receiver now has new address"),
        ])
        selected = window.mode_controls[Mode.DISPLAY]["device"].currentData()
        self.assertEqual(selected["receiver_id"], "stable-A")
        self.assertEqual(selected["target"], "192.168.1.20")
        self.assertEqual(window._display_options()["preferred_device_id"], "stable-A")

    def test_transport_addresses_are_not_sent_as_receiver_identity(self):
        window = self.make_window()
        for item in (
            {"id": "wifi:192.168.1.5", "target": "192.168.1.5"},
            {"id": "usb:device-udid", "target": "usb:device-udid"},
            {"id": "wifi:192.168.1.5", "receiver_id": "", "target": "192.168.1.5"},
            {"id": "wifi:192.168.1.5", "receiver_id": "usb:unstable", "target": "192.168.1.5"},
        ):
            with self.subTest(item=item):
                window._devices_found(Mode.DISPLAY, [dict(name="iPad", transport="wifi", **item)])
                options = window._display_options()
                self.assertEqual(options["device_id"], "")
                self.assertEqual(options["preferred_device_id"], "")

    def test_display_without_selected_target_discovers_before_start(self):
        window = self.make_window()
        window.start_session(Mode.DISPLAY)
        self.assertEqual(len(self.engines), 1)
        self.assertEqual(self.engines[0].sent[0][1], "discover")
        self.assertFalse(any(command == "start" for _, command, _ in self.engines[0].sent))

    def test_calibration_sends_latest_visible_side_speed_and_hotkey(self):
        window = self.make_window()
        engine = self.board_ready(window)
        window.position_choice.setCurrentText("电脑左侧")
        window.speed_control.setValue(125)
        window.hotkey_toggle.setChecked(False)
        with patch.object(QInputDialog, "getItem", return_value=("竖屏", True)):
            window.calibrate()
        _, command, payload = engine.sent[-1]
        self.assertEqual(command, "calibrate")
        self.assertEqual(payload["orientation"], "portrait")
        self.assertEqual(payload["ipad_side"], "left")
        self.assertEqual(payload["speed"], 1.25)
        self.assertFalse(payload["hotkey_return_enabled"])

    def test_calibration_before_real_ready_does_not_send_command(self):
        window = self.make_window()
        with patch.object(QMessageBox, "information") as information:
            window.calibrate()
        information.assert_called_once()
        self.assertEqual(self.engines, [])

    def test_firmware_tools_wait_for_clean_release_and_exit(self):
        window = self.make_window()
        engine = self.board_ready(window)
        with patch.object(window, "_open_firmware_tools") as opened:
            window.demo_tool("备份固件")
            self.assertEqual(engine.sent[-1][1], "shutdown")
            self.assertEqual(window.runtime.state, State.STOPPING)
            opened.assert_not_called()
            engine.release()
            opened.assert_not_called()
            engine.exit()
            opened.assert_called_once_with("备份固件")
        self.assertIsNone(window.runtime.engine)

    def test_firmware_tools_do_not_open_on_failed_release(self):
        window = self.make_window()
        engine = self.board_ready(window)
        with patch.object(window, "_open_firmware_tools") as opened:
            window.demo_tool("刷写固件")
            engine.exit()
            opened.assert_not_called()
        self.assertTrue(window.runtime.release_blocked)

    def test_resolution_configuration_waits_for_stop_and_never_invokes_uac_in_test(self):
        window = self.make_window()
        engine = self.board_ready(window)
        with patch.object(QInputDialog, "getInt", side_effect=[(1600, True), (1200, True)]), \
                patch.object(window, "_run_resolution_task") as maintenance:
            window.register_resolution()
            maintenance.assert_not_called()
            engine.release()
            maintenance.assert_not_called()
            engine.exit()
            maintenance.assert_called_once_with(1600, 1200)

    def test_hotkey_preference_survives_reopen_without_starting_engine(self):
        window = self.make_window()
        window.hotkey_toggle.setChecked(False)
        value = json.loads((self.storage.root / "settings.json").read_text(encoding="utf-8"))
        self.assertIs(value["hotkey_return_enabled"], False)
        second = self.make_window()
        self.assertFalse(second.hotkey_toggle.isChecked())
        second.hotkey_toggle.setChecked(True)
        third = self.make_window()
        self.assertTrue(third.hotkey_toggle.isChecked())
        self.assertEqual(self.engines, [])

    def test_all_pages_fit_minimum_width_with_long_device_names_and_advanced_settings(self):
        window = self.make_window()
        long_name = "设计工作室会议室备用超长中文设备名称 " * 8
        window._devices_found(Mode.DISPLAY, [dict(name=long_name, id="stable", target="192.168.1.8", transport="wifi")])
        window._devices_found(Mode.BRIDGE, [dict(name=long_name, id="bridge:board", port="COM8")])
        for disclosure in window.findChildren(QPushButton):
            if disclosure.objectName() == "disclosure":
                disclosure.setChecked(True)
        requested_width = window.minimumWidth()
        window.resize(window.minimumSize())
        window.show()
        self.app.processEvents()
        self.assertEqual(window.width(), requested_width, "The live layout must honor the documented minimum width")
        capture_dir = os.environ.get("IPADHUB_LIVE_QA_DIR")
        output = Path(capture_dir) if capture_dir else None
        if output:
            output.mkdir(parents=True, exist_ok=True)
        scale = os.environ.get("QT_SCALE_FACTOR", "1")
        names = ("home", "display", "bridge", "tools", "settings")
        for index in range(window.pages.count()):
            with self.subTest(page=index):
                window.navigate(index)
                self.app.processEvents()
                scroll = window.pages.widget(index)
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0,
                    f"page={index}, window={window.width()}, content={scroll.widget().width()}, viewport={scroll.viewport().width()}")
                self.assertLessEqual(scroll.widget().width(), scroll.viewport().width())
                if output:
                    self.assertTrue(window.grab().save(str(output / f"{names[index]}-minimum-scale-{scale}.png")))
        if output:
            window.resize(1180, 1100)
            for index, name in enumerate(names):
                window.navigate(index)
                self.app.processEvents()
                self.assertTrue(window.grab().save(str(output / f"{name}-expanded-scale-{scale}.png")))


if __name__ == "__main__":
    unittest.main()
