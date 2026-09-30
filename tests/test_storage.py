"""All migration checks use temporary files and an in-memory registry."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ipadhub.storage import HubStorage


class Key:
    def __init__(self, values):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Registry:
    HKEY_CURRENT_USER = 0
    KEY_READ = 1
    KEY_WRITE = 2
    REG_DWORD = 4

    def __init__(self):
        self.keys = {r"Software\MouseLink": {"monitorX_ipad": (0xFFFFFF38, 4),
                     "monitorY_ipad": (50, 4), "other": ("untouched", 1)}}

    def OpenKey(self, _, path, *args):
        if path not in self.keys:
            raise FileNotFoundError(path)
        return Key(self.keys[path])

    def CreateKeyEx(self, _, path, *args):
        return Key(self.keys.setdefault(path, {}))

    def QueryInfoKey(self, key):
        return (0, len(key.values), 0)

    def EnumValue(self, key, index):
        name, (value, kind) = list(key.values.items())[index]
        return name, value, kind

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name]

    def SetValueEx(self, key, name, _, kind, value):
        key.values[name] = (value, kind)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.old_bridge = root / "old-bridge"
        self.old_display = root / "old-display"
        self.old_bridge.mkdir()
        self.old_display.mkdir()
        self.registry = Registry()
        self.storage = HubStorage(root / "new", legacy_bridge=self.old_bridge,
                                  legacy_display=self.old_display, registry=self.registry)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_constructor_and_default_load_make_no_writes(self):
        self.assertFalse(self.storage.root.exists())
        self.assertEqual(self.storage.load_settings()["theme"], "system")
        self.assertFalse(self.storage.root.exists())

    def test_copy_only_preserves_active_calibration_and_firmware(self):
        settings = {"speed": 65, "calibration": {"device_serial": "test", "landscape": True}}
        self.write(self.old_bridge / "settings.json", settings)
        self.write(self.old_bridge / "appsettings.json", {"speed": 25})
        self.write(self.old_bridge / "device-calibration.json", {"landscape": False})
        self.write(self.old_display / "config.json", {"devices": [{"id": "display-id"}]})
        backup = self.old_bridge / "firmware-backups/device/flash.bin"
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b"firmware-test")
        before_files = {p: p.read_bytes() for directory in (self.old_bridge, self.old_display)
                        for p in directory.rglob("*") if p.is_file()}
        before_registry = copy.deepcopy(self.registry.keys[r"Software\MouseLink"])
        report = self.storage.migrate()
        self.assertTrue(report["complete"], report)
        self.assertEqual(self.storage.load_json(self.storage.bridge_dir / "settings.json"), settings)
        self.assertFalse((self.storage.bridge_dir / "device-calibration.json").exists())
        self.assertEqual((self.storage.firmware_backups_dir / "device/flash.bin").read_bytes(), b"firmware-test")
        for path, original in before_files.items():
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.registry.keys[r"Software\MouseLink"], before_registry)
        self.assertEqual(self.registry.keys[r"Software\iPadHub\Display"],
                         {"monitorX_ipad": (0xFFFFFF38, 4), "monitorY_ipad": (50, 4)})

    def test_second_migration_never_overwrites(self):
        self.write(self.old_bridge / "settings.json", {"speed": 50})
        self.storage.migrate()
        self.write(self.storage.bridge_dir / "settings.json", {"speed": 75})
        self.write(self.old_bridge / "settings.json", {"speed": 100})
        self.assertTrue(self.storage.migrate()["already_migrated"])
        self.assertEqual(self.storage.load_json(self.storage.bridge_dir / "settings.json"), {"speed": 75})

    def test_preexisting_destination_files_and_registry_win(self):
        self.write(self.old_bridge / "settings.json", {"speed": 50})
        self.write(self.storage.bridge_dir / "settings.json", {"speed": 80})
        self.registry.keys[r"Software\iPadHub\Display"] = {"monitorX_ipad": (25, 4)}
        self.assertTrue(self.storage.migrate()["complete"])
        self.assertEqual(self.storage.load_json(self.storage.bridge_dir / "settings.json"), {"speed": 80})
        self.assertEqual(self.registry.keys[r"Software\iPadHub\Display"]["monitorX_ipad"], (25, 4))

    def test_failed_registry_migration_retries_without_overwriting_new_settings(self):
        self.write(self.old_bridge / "settings.json", {"speed": 50})
        with patch.object(self.registry, "SetValueEx", side_effect=PermissionError("test denied")):
            first = self.storage.migrate()
        self.assertFalse(first["complete"])
        self.assertFalse((self.storage.root / "migration-v1.json").exists())
        self.write(self.storage.bridge_dir / "settings.json", {"speed": 100})
        second = self.storage.migrate()
        self.assertTrue(second["complete"])
        self.assertEqual(self.storage.load_json(self.storage.bridge_dir / "settings.json"), {"speed": 100})

    def test_atomic_settings_have_valid_previous_backup(self):
        self.storage.save_settings({"theme": "dark", "中文": "长名字"})
        self.storage.save_settings({"theme": "light"})
        self.assertEqual(json.loads((self.storage.root / "settings.json.bak").read_text("utf-8")),
                         {"theme": "dark", "中文": "长名字"})
        self.assertEqual(self.storage.load_settings()["theme"], "light")
        self.assertEqual(list(self.storage.root.glob("*.tmp")), [])

    def test_corrupt_current_recovers_backup_and_retains_bad_bytes(self):
        self.storage.save_settings({"theme": "dark"})
        self.storage.save_settings({"theme": "light"})
        (self.storage.root / "settings.json").write_bytes(b"{broken")
        self.assertEqual(self.storage.load_settings()["theme"], "dark")
        self.assertIn("恢复", self.storage.last_warning)
        self.assertEqual(list(self.storage.root.glob("*.corrupt-*"))[0].read_bytes(), b"{broken")
        self.assertEqual(json.loads((self.storage.root / "settings.json").read_bytes())["theme"], "dark")

    def test_save_after_corruption_keeps_valid_backup(self):
        self.storage.save_settings({"theme": "dark"})
        self.storage.save_settings({"theme": "light"})
        (self.storage.root / "settings.json").write_text("[]", encoding="utf-8")
        self.storage.save_settings({"theme": "system"})
        self.assertEqual(json.loads((self.storage.root / "settings.json.bak").read_bytes()), {"theme": "dark"})

    def test_missing_primary_uses_backup(self):
        self.write(self.storage.root / "settings.json.bak", {"theme": "dark"})
        self.assertEqual(self.storage.load_settings()["theme"], "dark")
        self.assertTrue((self.storage.root / "settings.json").is_file())

    def test_invalid_primary_and_backup_return_defaults_without_deletion(self):
        self.write(self.storage.root / "settings.json", [])
        self.write(self.storage.root / "settings.json.bak", "broken")
        self.assertEqual(self.storage.load_settings()["theme"], "system")
        self.assertEqual(json.loads((self.storage.root / "settings.json").read_bytes()), [])

    def test_atomic_replace_failure_preserves_valid_primary(self):
        self.storage.save_settings({"theme": "dark"})
        original_replace = __import__("os").replace

        def fail_primary(source, destination):
            if Path(destination).name == "settings.json":
                raise PermissionError("test locked file")
            return original_replace(source, destination)

        with patch("ipadhub.storage.os.replace", side_effect=fail_primary):
            with self.assertRaises(PermissionError):
                self.storage.save_settings({"theme": "light"})
        self.assertEqual(self.storage.load_settings()["theme"], "dark")
        self.assertEqual(list(self.storage.root.glob("*.tmp")), [])

    def test_alternate_old_settings_name_only_when_current_missing(self):
        self.write(self.old_bridge / "appsettings.json", {"calibration": {"portrait": True}})
        self.assertTrue(self.storage.migrate()["complete"])
        self.assertEqual(self.storage.load_json(self.storage.bridge_dir / "settings.json"),
                         {"calibration": {"portrait": True}})

    def test_bad_legacy_json_does_not_block_defaults_or_backup_recovery(self):
        (self.old_bridge / "settings.json").write_bytes(b"{corrupt")
        self.write(self.old_bridge / "settings.json.bak", {"speed": 65})
        first = self.storage.migrate()
        self.assertFalse(first["complete"])
        self.assertTrue(first["errors"])
        recovered = self.storage.load_json(self.storage.bridge_dir / "settings.json", {"speed": 50})
        self.assertEqual(recovered["speed"], 65)
        self.assertEqual((self.old_bridge / "settings.json").read_bytes(), b"{corrupt")
        self.assertTrue(self.storage.migrate()["complete"])


if __name__ == "__main__":
    unittest.main()
