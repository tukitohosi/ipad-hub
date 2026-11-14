"""Exercise the calibration UI with deterministic strokes and the real profile fitter."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ipadhub.live_window import LiveHubWindow
from ipadhub.monitor_size import dimensions_from_edid
from ipadhub.speed_wizard import SpeedWizard, calibration_backend
from ipadhub.storage import HubStorage


class SimulatedStroke:
    def __init__(self, result):
        self.result = result
        self.stopped = False

    def start(self):
        pass

    def finish(self):
        return self.result

    def cancel(self):
        self.stopped = True


class SpeedWizardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.strokes = []
        self.pending = []
        self.monitor = ("reference-screen", (2000, 1200))
        self.backend = SimpleNamespace(
            WindowsStrokeSampler=self.next_stroke, build_profile=calibration_backend().build_profile)
        self.wizard = SpeedWizard(backend=self.backend, screen_reader=lambda hwnd: self.monitor)
        self.addCleanup(self.wizard.close)

    def next_stroke(self):
        stroke = SimulatedStroke(self.pending.pop(0))
        self.strokes.append(stroke)
        return stroke

    def measure(self):
        for pair, values in zip(self.wizard.sizes, ((500, 300), (250, 180))):
            for field, number in zip(pair, values):
                field.setValue(number)
        self.wizard.next.click()
        self.assertEqual(self.wizard.pages.currentIndex(), 1)

    def move(self, rate, monitor="reference-screen"):
        self.pending.append(dict(rate=rate, gain=2, mouse="mouse", windows="system", monitor=monitor))
        self.wizard.keyboard_sample()
        self.wizard.keyboard_sample()

    def test_strokes_screen_bounds_restart_and_save(self):
        self.wizard.next.click()  # Empty measurements cannot silently become guessed dimensions.
        self.assertEqual(self.wizard.pages.currentIndex(), 0)
        self.measure()
        self.move(100, "other-screen")
        self.assertFalse(self.wizard.samples)
        self.move(100)
        self.move(105)
        self.assertEqual(len(self.wizard.samples), 1)
        self.move(200)
        self.move(500)
        profile = self.wizard.profile
        self.assertEqual(profile["orientation"], "landscape")
        self.assertEqual(profile["mm_per_px"], [.25, .25])
        self.assertAlmostEqual(profile["hid_per_px"][0], 32767 / 1000)
        self.assertAlmostEqual(profile["hid_per_px"][1], 32767 / 720)
        self.assertTrue(all(s.stopped for s in self.strokes))
        self.wizard.retry.click()
        self.assertIsNone(self.wizard.profile)
        self.assertFalse(self.wizard.samples)

    def test_interrupt_cancels_sampling_and_timers(self):
        self.measure()
        self.pending.append({})
        self.wizard.prepare_sample()
        self.wizard.keyboard_sample()
        self.assertTrue(self.wizard.timer.isActive())
        self.wizard.reject()
        self.assertFalse(self.wizard.timer.isActive())
        self.assertTrue(self.strokes[0].stopped)
        self.assertIsNone(self.wizard.profile)

    def test_live_settings_can_reopen_toggle_or_abandon_calibration(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            storage = HubStorage(root / "data", legacy_bridge=root / "old-bridge",
                                 legacy_display=root / "old-display", registry=None)
            storage.save_settings({"speed": 61})
            window = LiveHubWindow(storage, migrate=False)
            second = None
            try:
                with patch("ipadhub.live_window.SpeedWizard", wraps=SpeedWizard) as factory:
                    factory.side_effect = lambda w, **kwargs: SpeedWizard(w, self.backend, lambda hwnd: self.monitor, **kwargs)
                    factory.DialogCode = SpeedWizard.DialogCode
                    window.calibrate_speed_button.click()
                    dialog = window.speed_wizard
                    self.assertTrue(window.runtime.busy_tool)
                    dialog.reject()
                    self.assertFalse(window.runtime.busy_tool)
                    self.assertEqual(window.speed_control.value(), 61)
                    self.assertNotIn("pointer_profile", window._bridge_options())
                    window.calibrate_speed_button.click()
                    dialog = window.speed_wizard
                    self.wizard = dialog
                    self.measure()
                    for rate in (100, 300, 800):
                        self.move(rate)
                    dialog.next.click()
                self.assertIsNone(window.speed_wizard)
                self.assertEqual(window.speed_control.value(), 100)
                self.assertIsNotNone(window._bridge_options()["pointer_profile"])
                window.speed_control.setValue(92)
                second = LiveHubWindow(storage, migrate=False)
                self.assertTrue(second.speed_match_toggle.isChecked())
                self.assertEqual(second._bridge_options()["speed"], .92)
                second.speed_match_toggle.setChecked(False)
                self.assertIsNone(second._bridge_options()["pointer_profile"])
                self.assertEqual(second.speed_control.value(), 61)
                second.speed_match_toggle.setChecked(True)
                self.assertEqual(second.speed_control.value(), 92)
            finally:
                for w in (window, second):
                    if w:
                        w._quitting = True
                        w.tray.hide()
                        w.close()
                        w.deleteLater()
                self.app.processEvents()

    def test_automatic_monitor_and_selected_ipad_orientation(self):
        wizard = SpeedWizard(backend=self.backend, screen_reader=lambda hwnd: self.monitor,
                             dimension_reader=lambda name, pixels: (344, 194))
        self.addCleanup(wizard.close)
        wizard.show()
        self.assertEqual([c.value() for c in wizard.sizes[0]], [344, 194])
        self.assertEqual([c.value() for c in wizard.sizes[1]], [0, 0])
        m5 = next(i for i in range(wizard.model.count()) if "M5" in wizard.model.itemText(i)
                  and "11" in wizard.model.itemText(i))
        wizard.model.setCurrentIndex(m5)
        self.assertEqual([c.value() for c in wizard.sizes[1]], [232.8, 160.5])
        wizard.direction.setCurrentIndex(1)
        self.assertEqual([c.value() for c in wizard.sizes[1]], [160.5, 232.8])
        wizard.sizes[1][1].setValue(233)
        wizard.advance()
        self.assertEqual(wizard.geometry_settings["ipad_mm"], [160.5, 233])
        reopened = SpeedWizard(backend=self.backend, previous_geometry=wizard.geometry_settings)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.model.currentIndex(), m5)
        self.assertEqual(reopened.direction.currentIndex(), 1)
        self.assertEqual([c.value() for c in reopened.sizes[1]], [160.5, 233])

    def test_unknown_display_preserves_manual_override_and_clears_on_monitor_change(self):
        wizard = SpeedWizard(backend=self.backend, screen_reader=lambda hwnd: self.monitor,
                             dimension_reader=lambda name, pixels: None)
        self.addCleanup(wizard.close)
        wizard.show()
        self.assertTrue(all(c.value() == 0 for c in wizard.sizes[0]))
        wizard.sizes[0][0].setValue(400)
        wizard.detect_screen()
        self.assertEqual(wizard.sizes[0][0].value(), 400)
        self.monitor = ("another-screen", (2000, 1200))
        wizard.detect_screen()
        self.assertEqual(wizard.sizes[0][0].value(), 0)

    def test_edid_checksum_absolute_dimensions_and_rotation(self):
        edid = bytearray(128)
        edid[:8] = bytes.fromhex("00ffffffffffff00")
        edid[54] = 1
        edid[66:69] = bytes([88, 194, 16])  # 344 x 194 millimeters.
        edid[127] = -sum(edid) % 256
        self.assertEqual(dimensions_from_edid(edid, (1440, 2560)), (194, 344))
        edid[10] += 1
        self.assertIsNone(dimensions_from_edid(edid, (2560, 1440)))
