import argparse
import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "open_bridge"))
import flash_helper as helper
from firmware_bundle import (FLASH_SIZE, compatible_backup, load_backup, load_bundle,
                             save_backup)
from app_settings import migrate_settings, bridge_mode


class FlashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.args = argparse.Namespace(port="COM99", serial="00:11:22:33:44:55",
            backup_root=Path(self.temp.name), bundle=None, restore=None)
        self.manifest, self.images = load_bundle()
        self.original = bytearray(b"\xff" * FLASH_SIZE)
        for address, image in self.images.items():
            self.original[address:address + len(image)] = image
        # Representative non-empty pairing data outside application partitions.
        self.original[0x9000:0x9010] = b"paired-NVS-state"
        self.original = bytes(self.original)
        self.calls, self.events = [], []
        self.flash_memory = self.original
        self.esp = SimpleNamespace(CHIP_NAME="ESP32-C3", secure_download_mode=False,
            get_secure_boot_enabled=lambda: False, get_flash_encryption_enabled=lambda: False,
            read_mac=lambda: tuple(bytes.fromhex("001122334455")),
            _port=SimpleNamespace(close=lambda: self.calls.append("close")))
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        mocks = {
            "detect_chip": lambda *a, **k: self.esp,
            "run_stub": lambda esp: esp,
            "attach_flash": lambda esp: None,
            "detect_flash_size": lambda esp: "4MB",
            "read_flash": lambda *a: self.flash_memory,
            "verify_flash": self.verify,
            "write_flash": self.write,
            "erase_flash": self.erase,
            "reset_chip": lambda *a: self.calls.append("reset"),
        }
        for name, value in mocks.items():
            self.stack.enter_context(patch.object(helper.cmds, name, value))
        self.stack.enter_context(patch.object(helper, "check_selected_port"))
        self.stack.enter_context(patch.object(helper, "verify_restart", return_value=True))

    def verify(self, esp, images):
        self.calls.append("verify")
        for address, data in images:
            self.assertEqual(self.flash_memory[address:address + len(data)], data)

    def erase(self, esp):
        self.assertTrue(list(self.args.backup_root.glob("*/backup.json")), "Never erase before durable backup")
        self.calls.append("erase")
        self.flash_memory = b"\xff" * FLASH_SIZE

    def write(self, esp, images, **kwargs):
        self.assertTrue(list(self.args.backup_root.glob("*/backup.json")))
        self.calls.append("write")
        mutable = bytearray(self.flash_memory)
        for address, data in images:
            mutable[address:address + len(data)] = data
        self.flash_memory = bytes(mutable)

    def run_flash(self):
        helper.flash(self.args, helper.Events(self.events.append))

    def test_compatible_upgrade_preserves_pairing_and_backs_up(self):
        self.run_flash()
        self.assertNotIn("erase", self.calls)
        self.assertEqual(self.flash_memory, self.original)
        self.assertEqual(self.events[-1]["kind"], "upgrade")
        self.assertTrue(self.events[-1]["ready"])
        self.assertEqual(load_backup(self.events[-1]["backup"], self.args.serial)[1], self.original)

    def test_blank_board_gets_all_three_images(self):
        self.flash_memory = b"\xff" * FLASH_SIZE
        self.run_flash()
        self.assertIn("erase", self.calls)
        self.assertEqual(self.events[-1]["kind"], "initialize")
        for address, data in self.images.items():
            self.assertEqual(self.flash_memory[address:address + len(data)], data)

    def test_backup_failure_never_erases_or_writes(self):
        with patch.object(helper, "save_backup", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.run_flash()
        self.assertNotIn("write", self.calls)
        self.assertNotIn("erase", self.calls)

    def test_wrong_chip_and_encryption_do_not_write(self):
        for chip, secure in (("ESP32-S3", False), ("ESP32-C3", True)):
            self.esp.CHIP_NAME, self.esp.secure_download_mode = chip, secure
            with self.assertRaises(ValueError):
                self.run_flash()
        self.assertNotIn("write", self.calls)

    def test_restore_checks_device_identity_and_hash(self):
        path = save_backup(self.args.backup_root, self.original, "000000000000", "1.0.1")
        self.args.restore = path
        with self.assertRaises(ValueError):
            self.run_flash()
        self.assertNotIn("write", self.calls)
        metadata = json.loads(path.read_text())
        metadata["mac"] = "001122334455"
        path.write_text(json.dumps(metadata))
        self.run_flash()
        self.assertEqual(self.events[-1]["kind"], "restore")
        self.assertEqual(self.flash_memory, self.original)
        path.with_name("device-flash.bin").write_bytes(b"broken")
        with self.assertRaises(ValueError):
            load_backup(path)

    def test_unknown_app_or_partition_cannot_preserve_nvs(self):
        for address in (0, 0x8000, 0x10020):
            data = bytearray(self.original)
            data[address] ^= 0xff
            self.assertFalse(compatible_backup(bytes(data), self.manifest, self.images))

    def test_verified_legacy_bootloader_preserves_pairing(self):
        old = bytearray(self.original)
        legacy = Path(__file__).resolve().parents[1] / "open_bridge/dist/bootloader.bin"
        if not legacy.exists():
            self.skipTest("Legacy bootloader fixture is not included in this source checkout")
        boot = legacy.read_bytes()
        old[:len(boot)] = boot
        self.assertTrue(compatible_backup(bytes(old), self.manifest, self.images))

    def test_wrong_capacity_and_serial_rejected(self):
        with patch.object(helper.cmds, "detect_flash_size", return_value="8MB"):
            with self.assertRaises(ValueError):
                self.run_flash()
        self.args.serial = "000000000000"
        with self.assertRaises(ValueError):
            self.run_flash()
        self.assertNotIn("write", self.calls)

    def test_readback_failure_blocks_backup_and_write(self):
        with patch.object(helper.cmds, "verify_flash", side_effect=ValueError("readback mismatch")):
            with self.assertRaises(ValueError):
                self.run_flash()
        self.assertFalse(list(self.args.backup_root.glob("*/backup.json")))
        self.assertNotIn("write", self.calls)
    def test_disconnect_during_write_has_no_success_result(self):
        with patch.object(helper.cmds, "write_flash", side_effect=OSError("unplugged")):
            with self.assertRaises(OSError):
                self.run_flash()
        self.assertFalse(any(e.get("event") == "result" and e.get("ok") for e in self.events))
        self.assertTrue(list(self.args.backup_root.glob("*/backup.json")))

    def test_corrupt_package_rejected_before_opening_port(self):
        with patch.object(helper, "load_bundle", side_effect=ValueError("bad hash")), \
                patch.object(helper.cmds, "detect_chip") as detect:
            with self.assertRaises(ValueError):
                self.run_flash()
            detect.assert_not_called()

    def test_reboot_without_pairing_is_not_failed_flash(self):
        with patch.object(helper, "verify_restart", return_value=False):
            self.run_flash()
        self.assertTrue(self.events[-1]["ok"])
        self.assertFalse(self.events[-1]["ready"])


class PortSelectionTests(unittest.TestCase):
    def test_multiple_devices_require_exact_port_and_identity(self):
        ports = [SimpleNamespace(device="COM3", serial_number="111111111111"),
                 SimpleNamespace(device="COM4", serial_number="222222222222")]
        with patch.object(helper, "supported_ports", return_value=ports):
            helper.check_selected_port("COM4", "222222222222")
            for port, serial in (("", ""), ("COM3", "222222222222"), ("COM5", "111111111111")):
                with self.assertRaises(ValueError):
                    helper.check_selected_port(port, serial)


class SettingsTests(unittest.TestCase):
    def test_fresh_defaults_and_old_modes(self):
        self.assertEqual(bridge_mode(**{"mode": migrate_settings({})["mode"], "hotkey_enabled": False}), "edge")
        for old, expected in (("edge", "edge"), ("mixed", "mixed"), ("locked", "locked")):
            new = migrate_settings({"mode": old, "speed": 75, "calibration": {"portrait": True}})
            self.assertEqual(bridge_mode(new["mode"], new["hotkey_return_enabled"]), expected)
            self.assertEqual(new["speed"], 75)
            self.assertTrue(new["calibration"]["portrait"])

    def test_corrupt_settings_are_recoverable(self):
        for value in (None, [], "invalid", {"mode": "other", "speed": "bad", "calibration": []}):
            self.assertEqual(migrate_settings(value)["mode"], "free")
            self.assertEqual(migrate_settings(value)["speed"], 50)


if __name__ == "__main__":
    unittest.main()
