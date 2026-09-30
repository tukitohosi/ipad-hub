"""Exercise daily and candidate startup without USB or input hooks."""
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "open_bridge"))
import desktop_app as ui


class CandidateWorkerTests(unittest.TestCase):
    def run_worker(self, worker, failure=None):
        worker.commands.put(("start", ("mixed", .5)))
        controller = Mock()
        controller.run.side_effect = failure
        port = SimpleNamespace(device="COM99", vid=0x303A, pid=0x1001, serial_number="test")
        with patch.object(ui, "comports", return_value=[port]), \
             patch.object(ui, "SerialBridge") as serial_bridge, \
             patch.object(ui, "KVMController", return_value=controller) as constructor, \
             patch.object(ui.threading, "Timer") as timer:
            serial_bridge.return_value.connect.return_value = True
            serial_bridge.return_value.absolute_ready = True
            if failure:
                with self.assertRaises(RuntimeError):
                    worker._idle()
            else:
                worker._idle()
            config = constructor.call_args.args[0]
            self.assertTrue(config.native_edges_enabled)
            self.assertTrue(config.absolute_enabled)
            self.assertEqual(config.mode, "mixed")
            self.assertEqual(config.ipad_side, "right")
            if worker.trial_seconds:
                timer.assert_called_once()
                self.assertEqual(timer.call_args.args[0], worker.trial_seconds)
                self.assertTrue(callable(timer.call_args.args[1]))
                timer.return_value.start.assert_called_once()
                timer.return_value.cancel.assert_called_once()
            else:
                timer.assert_not_called()
            controller.stop.assert_called_once()
            self.assertFalse(worker.running_bridge)
            self.assertIsNone(worker.controller)

    def test_candidate_start_limits_capture_and_preserves_mode(self):
        self.run_worker(ui.Worker(native_edges=True, trial_seconds=180))

    def test_daily_start_defaults_to_native_edges_without_a_timer(self):
        worker = ui.Worker()
        self.assertEqual(worker.trial_seconds, 0)
        self.run_worker(worker)

    def test_timer_is_cancelled_and_capture_stopped_on_failure(self):
        self.run_worker(ui.Worker(native_edges=True, trial_seconds=180),
                        RuntimeError("simulated capture failure"))

    def test_expiry_stops_the_active_controller(self):
        worker = ui.Worker(native_edges=True, trial_seconds=180)
        worker.controller = Mock()
        worker.stop_bridge()
        worker.controller.stop.assert_called_once()
        self.assertTrue(worker.stop_requested)

    def test_candidate_data_directory_does_not_change_installed_settings(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {"LOCALAPPDATA": temp}, clear=False):
            old_data = Path(temp) / "MouseLink"
            old_data.mkdir()
            original = '{"mode":"free","speed":50}'
            (old_data / "settings.json").write_text(original, encoding="utf-8")
            candidate = old_data / "NativeEdgesCandidate"
            with patch.dict(os.environ, {"MOUSELINK_DATA_DIR": str(candidate)}):
                (ui.app_data() / "settings.json").write_text('{"mode":"locked"}', encoding="utf-8")
                self.assertEqual(ui.app_data(), candidate)
            self.assertEqual((old_data / "settings.json").read_text(encoding="utf-8"), original)


class DesktopEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = ui.W.QApplication.instance() or ui.W.QApplication([])

    def test_daily_title_has_no_trial_notice_and_candidate_keeps_its_notice(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.dict(os.environ, {"MOUSELINK_DATA_DIR": temp}):
            for seconds in (0, 180):
                with self.subTest(seconds=seconds):
                    window = ui.Window(preview=True, trial_seconds=seconds)
                    try:
                        labels = [label.text() for label in window.findChildren(ui.W.QLabel)]
                        self.assertEqual(window.windowTitle(), "MouseLink · 原生边缘候选版"
                                         if seconds else "MouseLink · 键鼠桥")
                        self.assertEqual(any("每次开启最多" in label for label in labels), bool(seconds))
                        self.assertIn("原生边缘 · 候选版" if seconds else f"键鼠桥  /  {ui.APP_VERSION}", labels)
                        self.assertIsNone(window.worker)
                    finally:
                        window.timer.stop()
                        window.close()
                        window.deleteLater()

    def test_daily_and_candidate_entry_select_native_edges_with_separate_limits_and_settings(self):
        for candidate in (False, True):
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as temp, \
                 patch.dict(os.environ, {"LOCALAPPDATA": temp, "MOUSELINK_DATA_DIR": ""}):
                original_data = Path(temp) / "MouseLink"
                original_data.mkdir()
                original = '{"mode":"free","speed":50}'
                (original_data / "settings.json").write_text(original, encoding="utf-8")
                arguments = ["MouseLink", "--preview", str(Path(temp) / "preview.png")]
                if candidate:
                    arguments += ["--native-edges", "--trial-seconds", "180"]
                with patch.object(sys, "argv", arguments), \
                     patch.object(ui.W, "QApplication") as application, \
                     patch.object(ui, "Window") as window, \
                     patch.object(ui.QtCore.QTimer, "singleShot"):
                    application.return_value.exec.return_value = 0
                    self.assertEqual(ui.main(), 0)
                    window.assert_called_once_with(preview=True, native_edges=True,
                                                   trial_seconds=180 if candidate else 0)
                self.assertEqual(ui.app_data(), original_data / "NativeEdgesCandidate"
                                 if candidate else original_data)
                self.assertEqual((original_data / "settings.json").read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
