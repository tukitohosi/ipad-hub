"""Shown-window regressions with fake USB; never moves or captures input."""
import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path,
                    default=ROOT / "test-results/1.0.1-desktop-regressions.json")
args = parser.parse_args()
sys.path.insert(0, str(ROOT / "open_bridge"))
from PySide6 import QtCore, QtGui, QtWidgets as W
import desktop_app as ui
import flash_dialog

app = W.QApplication([])
app.setQuitOnLastWindowClosed(False)
results = []


def wait_until(condition, seconds=3):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        app.processEvents()
        if condition():
            return
        time.sleep(.005)
    raise AssertionError("condition timed out")


def closed(window, method="close"):
    started = time.monotonic()
    if method == "alt-f4":
        # A native close request is also how Windows delivers Alt+F4.
        window.windowHandle().close()
    elif method == "tray":
        window.tray.contextMenu().actions()[-1].trigger()
    else:
        window.close()
    wait_until(lambda: not window.isVisible() and not window.worker.isRunning())
    duration = time.monotonic() - started
    assert not window.tray.isVisible()
    window.deleteLater()
    app.processEvents()
    return duration


with tempfile.TemporaryDirectory() as temp, patch.object(ui, "app_data", return_value=Path(temp)), \
        patch.object(ui, "comports", return_value=[]), patch.object(flash_dialog, "comports", return_value=[]):
    for method in ("close", "alt-f4", "tray"):
        window = ui.Window()
        window.show()
        wait_until(lambda: window.isVisible())
        duration = closed(window, method)
        assert duration < 1, duration
        results.append({"case": f"no_usb_{method}", "seconds": round(duration, 3)})

    Path(temp, "settings.json").write_text("[]", encoding="utf-8")
    window = ui.Window()
    window.show()
    window.config_path = Path(temp) / "missing-directory" / "settings.json"
    duration = closed(window)
    assert window.save_error and duration < 1
    results.append({"case": "invalid_settings_and_save_failure", "seconds": round(duration, 3)})

    window = ui.Window()
    window.show()
    assert set(window.cards) == {"free", "locked"}
    assert window.effective_mode() == "edge"
    window.hotkey_toggle.click()
    assert window.effective_mode() == "mixed"
    window.cards["locked"].radio.click()
    window.refresh()
    assert window.effective_mode() == "locked" and not window.hotkey_toggle.isEnabled()
    window.cards["free"].radio.click()
    window.refresh()
    assert window.effective_mode() == "mixed" and window.hotkey_toggle.isEnabled()
    closed(window)
    results.append({"case": "two_cards_hotkey_and_persistence", "ok": True})

    window = ui.Window()
    window.show()
    window.open_flash()
    wait_until(lambda: window.flash_dialog is not None)
    dialog = window.flash_dialog
    assert not window.worker.isRunning()
    assert not dialog.flash_button.isEnabled()
    dialog.close()
    wait_until(lambda: window.flash_dialog is None and window.worker.isRunning())
    closed(window)
    results.append({"case": "flash_dialog_exclusive_port_and_return", "ok": True})

    window = ui.Window()
    window.show()
    window.open_flash()
    wait_until(lambda: window.flash_dialog is not None)
    dialog = window.flash_dialog

    class FiniteFlash(QtCore.QThread):
        def run(self):
            self.msleep(250)

    job = FiniteFlash(dialog)
    dialog.job = job
    job.finished.connect(dialog.on_finished)
    job.start()
    window.close()
    assert window.isVisible() and dialog.close_after
    wait_until(lambda: not window.isVisible() and not job.isRunning())
    results.append({"case": "close_waits_for_flash_completion", "ok": True})
    window.deleteLater()
    app.processEvents()

    # A queued start must not execute after closing, even if USB appears later.
    window = ui.Window()
    window.show()
    window.worker.commands.put(("start", ("edge", .5)))
    window.start_pending = True
    with patch.object(ui, "KVMController") as controller:
        closed(window)
        controller.assert_not_called()
    results.append({"case": "pending_start_cancelled", "ok": True})

    class FakeBridge:
        def __init__(self, config):
            self.is_connected = False
            self.absolute_ready = self.ble_connected = True
            self.sent = []

        def connect(self):
            self.is_connected = True
            return True

        def disconnect(self):
            self.is_connected = False

        def send_absolute_report(self, *report):
            self.sent.append(report)
            return True

        def send_mouse_report(self, *report):
            self.sent.append(report)
            return True

    fake_port = SimpleNamespace(device="COM99", vid=0x303A, pid=0x1001, serial_number="test-board")
    with patch.object(ui, "comports", return_value=[fake_port]), patch.object(ui, "SerialBridge", FakeBridge):
        window = ui.Window()
        window.show()
        wait_until(lambda: window.worker.snapshot()["usb"])
        window.worker.commands.put(("calibrate", "landscape"))
        wait_until(lambda: bool(window.worker.phase))
        monitor = window.worker.monitor
        duration = closed(window)
        assert not monitor.is_connected and duration < 1
        assert monitor.sent[-1] == (0, 0, 0, 0), "Calibration must release mouse buttons on close"
        results.append({"case": "close_during_calibration_releases_usb", "seconds": round(duration, 3)})

args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(results, ensure_ascii=False))
