"""Placement controls and optional diagnostics, without USB or input capture."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "open_bridge"))

import desktop_app as ui
from app_settings import migrate_settings


class PlacementSettingsTests(unittest.TestCase):
    def test_old_and_invalid_placement_settings_default_to_right(self):
        for value in ({}, None, [], {"ipad_side": None}, {"ipad_side": ""},
                      {"ipad_side": "above"}, {"ipad_side": 1},
                      {"ipad_side": []}, {"ipad_side": {}}):
            with self.subTest(value=value):
                self.assertEqual(migrate_settings(value)["ipad_side"], "right")

    def test_migration_preserves_placement_and_other_preferences(self):
        original = {"ipad_side": "left", "mode": "mixed", "speed": 80,
                    "calibration": {"device_serial": "old-board", "portrait": False},
                    "unrelated_preference": "preserved"}
        result = migrate_settings(original)
        self.assertEqual(result["ipad_side"], "left")
        self.assertEqual(result["speed"], 80)
        self.assertEqual(result["mode"], "free")
        self.assertTrue(result["hotkey_return_enabled"])
        self.assertEqual(result["calibration"], original["calibration"])
        self.assertEqual(result["unrelated_preference"], "preserved")
        self.assertEqual(original["mode"], "mixed")


class PlacementDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = ui.W.QApplication.instance() or ui.W.QApplication([])

    @contextmanager
    def window(self, settings=None, preview=False, directory=None):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(directory or temporary)
            settings_path = data / "settings.json"
            if settings is not None:
                settings_path.write_text(json.dumps(settings), encoding="utf-8")
            with patch.dict(os.environ, {"MOUSELINK_DATA_DIR": str(data)}), \
                 patch.object(ui.Worker, "start"), \
                 patch.object(ui.W.QSystemTrayIcon, "show"):
                window = ui.Window(preview=preview)
                window.timer.stop()
                if window.worker:
                    window.worker.device_serial = "current-board"
                    window.worker.monitor = Mock(is_connected=True, absolute_ready=True,
                                                 ble_connected=True)
                    window.worker.snapshot = Mock(return_value={
                        "usb": True, "ble": True, "ready": True, "remote": False})
                    window.refresh()
                try:
                    yield window
                finally:
                    if window.worker:
                        window.worker.request_shutdown()
                    window.allow_close = True
                    window.tray.hide()
                    window.close()
                    window.deleteLater()
                    self.application.processEvents()

    def test_ready_device_starts_without_completed_or_matching_calibration(self):
        profiles = ({}, {"landscape": False, "portrait": False},
                    {"device_serial": "old-board", "landscape": True, "portrait": True})
        for profile in profiles:
            with self.subTest(profile=profile), self.window({"calibration": profile}) as window:
                self.assertTrue(window.start_button.isEnabled())
                self.assertNotIn("首次使用请完成", window.status_text.text())
                window.start_button.click()
                action, values = window.worker.commands.get_nowait()
                self.assertEqual((action, values[:3]), ("start", ("edge", .5, "right")))
                self.assertIs(values[3], window.start_request)

    def test_disconnected_or_unready_device_cannot_start(self):
        for usb, ble in ((False, False), (True, False), (True, True)):
            with self.subTest(usb=usb, ble=ble), self.window() as window:
                window.worker.snapshot.return_value = {
                    "usb": usb, "ble": ble, "ready": False, "remote": False}
                window.refresh()
                self.assertFalse(window.start_button.isEnabled())
                window.start_button.click()
                self.assertTrue(window.worker.commands.empty())

    def test_selection_is_exclusive_and_persists_across_application_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            original = {"mode": "locked", "speed": 90,
                        "hotkey_return_enabled": True, "unrelated_preference": 17}
            with self.window(original, directory=temp) as window:
                self.assertEqual(window.selected_side(), "right")
                window.side_buttons["left"].click()
                self.assertEqual(window.selected_side(), "left")
                self.assertTrue(window.side_buttons["left"].isChecked())
                self.assertFalse(window.side_buttons["right"].isChecked())
                saved = json.loads((Path(temp) / "settings.json").read_text(encoding="utf-8"))
                self.assertEqual(saved["ipad_side"], "left")
                self.assertEqual(saved["mode"], "locked")
                self.assertEqual(saved["speed"], 90)
                self.assertTrue(saved["hotkey_return_enabled"])
                self.assertEqual(saved["unrelated_preference"], 17)
            with self.window(directory=temp) as reopened:
                self.assertEqual(reopened.selected_side(), "left")
                self.assertTrue(reopened.side_buttons["left"].isChecked())

    def test_entry_route_and_return_instructions_follow_placement(self):
        with self.window() as window:
            for side, entry, returning in (("left", "左", "右"), ("right", "右", "左")):
                with self.subTest(side=side):
                    window.side_buttons[side].click()
                    window.refresh()
                    route = window.route.text().upper()
                    self.assertEqual(route.index("IPAD") < route.index("WINDOWS"), side == "left")
                    self.assertIn(f"电脑{entry}边缘", window.subtitle.text())
                    self.assertIn(f"最{returning}边缘", window.cards["free"].description.text())
                    self.assertIn(f"向{returning}推动", window.notice.text())
                    window.worker.running_bridge = True
                    window.refresh()
                    self.assertIn(f"最{entry}边缘", window.subtitle.text())
                    window.worker.snapshot.return_value["remote"] = True
                    window.refresh()
                    self.assertIn(f"最{returning}边缘", window.subtitle.text())
                    window.worker.running_bridge = False
                    window.worker.snapshot.return_value["remote"] = False

    def test_start_passes_placement_mode_and_speed_to_worker(self):
        with self.window({"ipad_side": "left", "mode": "free", "speed": 80,
                          "hotkey_return_enabled": True}) as window:
            window.start_button.click()
            action, values = window.worker.commands.get_nowait()
            self.assertEqual((action, values[:3]), ("start", ("mixed", .8, "left")))
            self.assertIs(values[3], window.start_request)

    def test_retry_after_error_keeps_pending_and_second_click_cancels_start(self):
        with self.window() as window:
            window.worker.error = "上次连接失败，请重新开启。"
            window.start_button.click()
            self.assertTrue(window.start_pending)
            self.assertTrue(window.start_button.isEnabled())
            self.assertIn("取消", window.start_button.text())
            self.assertFalse(any(button.isEnabled() for button in window.side_buttons.values()))
            self.assertEqual(window.worker.commands.qsize(), 1)
            window.start_button.click()
            self.assertFalse(window.start_pending)
            self.assertIsNone(window.start_request)
            self.assertTrue(window.worker.commands.empty())

    def test_pending_start_can_be_cancelled_even_after_connection_drops(self):
        with self.window() as window:
            window.start_button.click()
            cancelled_request = window.start_request
            window.worker.snapshot.return_value = {
                "usb": False, "ble": False, "ready": False, "remote": False}
            window.refresh()
            self.assertTrue(window.start_button.isEnabled())
            self.assertIn("取消", window.start_button.text())
            window.start_button.click()
            self.assertFalse(window.start_pending)
            self.assertTrue(window.worker.stop_requested)
            self.assertTrue(window.worker.commands.empty())
            window.worker.start_handled.emit(cancelled_request)
            window.refresh()
            self.assertFalse(window.start_pending)
            self.assertFalse(window.start_button.isEnabled())

    def test_delayed_old_start_signal_cannot_unlock_new_pending_request(self):
        with self.window() as window:
            old_request = object()
            window.start_button.click()
            current_request = window.start_request
            window.worker.start_handled.emit(old_request)
            window.refresh()
            self.assertTrue(window.start_pending)
            self.assertIn("取消", window.start_button.text())
            self.assertFalse(any(button.isEnabled() for button in window.side_buttons.values()))
            self.assertIs(window.start_request, current_request)
            window.worker.start_handled.emit(current_request)
            self.assertFalse(window.start_pending)

    def test_running_placement_buttons_remain_available_and_request_switch(self):
        with self.window({"mode": "locked", "speed": 75}) as window:
            window.worker.running_bridge = True
            window.worker.request_side_change = Mock()
            window.refresh()
            self.assertFalse(window.speed.isEnabled())
            self.assertFalse(window.cards["free"].isEnabled())
            self.assertTrue(all(button.isEnabled() for button in window.side_buttons.values()))
            window.side_buttons["left"].click()
            window.worker.request_side_change.assert_called_once_with("locked", .75, "left")

    def test_switching_accepts_a_new_selection_and_keeps_stop_available_without_ready_device(self):
        with self.window({"ipad_side": "left"}) as window:
            window.worker.switching = True
            window.worker.running_bridge = False
            window.worker.snapshot.return_value = {
                "usb": False, "ble": False, "ready": False, "remote": False}
            window.worker.request_side_change = Mock()
            window.worker.stop_bridge = Mock()
            window.refresh()
            self.assertIn("切换位置", window.headline.text() + window.subtitle.text() + window.status_text.text())
            self.assertTrue(window.start_button.isEnabled())
            self.assertIn("停止", window.start_button.text())
            self.assertTrue(all(button.isEnabled() for button in window.side_buttons.values()))
            self.assertFalse(window.check_button.isEnabled())
            window.side_buttons["right"].click()
            window.worker.request_side_change.assert_called_once_with("edge", .5, "right")
            window.start_button.click()
            window.worker.stop_bridge.assert_called_once()
            self.assertTrue(window.worker.commands.empty())

    def test_busy_or_pending_start_disables_placement_buttons(self):
        for pending in (False, True):
            with self.subTest(pending=pending), self.window() as window:
                window.start_pending = pending
                window.worker.busy = not pending
                window.worker.request_side_change = Mock()
                window.refresh()
                self.assertFalse(any(button.isEnabled() for button in window.side_buttons.values()))
                window.side_buttons["left"].click()
                self.assertEqual(window.selected_side(), "right")
                window.worker.request_side_change.assert_not_called()

    def test_failed_optional_check_does_not_prevent_start(self):
        with self.window() as window, patch.object(ui.W.QMessageBox, "warning") as warning:
            window.finish_calibration("landscape", False, "模拟检查失败")
            warning.assert_called_once()
            window.refresh()
            self.assertTrue(window.start_button.isEnabled())
            window.start_button.click()
            self.assertEqual(window.worker.commands.get_nowait()[0], "start")

    def test_rejected_optional_check_stays_optional_and_prompts_for_selected_edge(self):
        with self.window({"ipad_side": "left"}) as window, \
             patch.object(ui.W.QMessageBox, "question", return_value=ui.W.QMessageBox.StandardButton.No) as question:
            window.finish_calibration("portrait", True, "")
            self.assertIn("最右边缘", question.call_args.args[2])
            self.assertFalse(window.settings["calibration"]["portrait"])
            window.refresh()
            self.assertTrue(window.start_button.isEnabled())

    def test_optional_check_queues_selected_orientation_and_placement(self):
        with self.window({"ipad_side": "left"}) as window, \
             patch.object(ui.W.QMessageBox, "exec", return_value=0), \
             patch.object(ui.W.QMessageBox, "clickedButton",
                          lambda box: next(button for button in box.buttons() if button.text() == "检查横屏")):
            window.check_button.click()
            self.assertEqual(window.worker.commands.get_nowait(),
                             ("calibrate", ("landscape", "left")))

    def test_preview_never_starts_hardware_or_changes_saved_preferences(self):
        with self.window({"ipad_side": "right", "mode": "locked"}, preview=True) as window:
            saved = window.config_path.read_bytes()
            self.assertIsNone(window.worker)
            with patch.object(ui, "SerialBridge", side_effect=AssertionError("hardware accessed")), \
                 patch.object(ui, "KVMController", side_effect=AssertionError("input capture accessed")):
                window.side_buttons["left"].click()
                self.assertEqual(window.selected_side(), "left")
                window.start_button.click()
                window.save()
                self.assertEqual(window.config_path.read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
