"""Run the real backup-only branch against mocked esptool, without a board."""
import argparse
import contextlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "vendor/mouselink/open_bridge"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))
import flash_helper
from firmware_bundle import FLASH_SIZE, load_backup


class FirmwareBackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.original = b"\xa5" * FLASH_SIZE
        self.args = argparse.Namespace(port="COM99", serial="001122334455", backup_root=self.root,
                                       bundle=None, restore=None, backup_only=True)
        self.events = []
        self.esp = SimpleNamespace(CHIP_NAME="ESP32-C3", secure_download_mode=False,
            get_secure_boot_enabled=lambda: False, get_flash_encryption_enabled=lambda: False,
            read_mac=lambda: tuple(bytes.fromhex("001122334455")),
            _port=SimpleNamespace(close=lambda: None))
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.mocks = {}
        for name, result in {"detect_chip": self.esp, "run_stub": self.esp, "attach_flash": None,
                            "detect_flash_size": "4MB", "read_flash": self.original,
                            "verify_flash": None, "erase_flash": None, "write_flash": None,
                            "reset_chip": None}.items():
            self.mocks[name] = self.stack.enter_context(patch.object(flash_helper.cmds, name, return_value=result))
        self.stack.enter_context(patch.object(flash_helper, "check_selected_port"))
        self.compatibility = self.stack.enter_context(patch.object(flash_helper, "compatible_backup"))
        self.restart_check = self.stack.enter_context(patch.object(flash_helper, "verify_restart"))
        self.closed = self.stack.enter_context(patch.object(self.esp._port, "close"))

    def run_backup(self):
        flash_helper.flash(self.args, flash_helper.Events(self.events.append))

    def assert_read_only_flash(self):
        self.mocks["erase_flash"].assert_not_called()
        self.mocks["write_flash"].assert_not_called()
        self.compatibility.assert_not_called()
        self.restart_check.assert_not_called()
        self.closed.assert_called_once()

    def test_backup_only_saves_verified_full_flash_without_erase_or_write(self):
        self.run_backup()
        self.mocks["read_flash"].assert_called_once_with(self.esp, 0, FLASH_SIZE)
        self.mocks["verify_flash"].assert_called_once_with(self.esp, [(0, self.original)])
        result = self.events[-1]
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "backup")
        self.assertEqual(load_backup(result["backup"], self.args.serial)[1], self.original)
        self.mocks["reset_chip"].assert_called_once_with(self.esp, "hard-reset")
        self.assert_read_only_flash()

    def test_backup_only_save_failure_never_erases_writes_or_reports_success(self):
        with patch.object(flash_helper, "save_backup", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_backup()
        self.assertFalse(any(event.get("event") == "result" and event.get("ok") for event in self.events))
        self.assert_read_only_flash()

    def test_backup_only_readback_failure_stops_before_saving(self):
        self.mocks["verify_flash"].side_effect = ValueError("readback mismatch")
        with patch.object(flash_helper, "save_backup") as save:
            with self.assertRaises(ValueError):
                self.run_backup()
            save.assert_not_called()
        self.assertFalse(any(event.get("event") == "result" and event.get("ok") for event in self.events))
        self.assert_read_only_flash()


if __name__ == "__main__":
    unittest.main()
