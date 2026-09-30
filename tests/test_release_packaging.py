"""Privacy and corresponding-source gates for release archives."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


path = Path(__file__).resolve().parents[1] / "packaging/prepare-release.py"
spec = importlib.util.spec_from_file_location("prepare_release", path)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseSourceTests(unittest.TestCase):
    def test_runtime_data_and_private_backups_never_enter_source_zip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures = [
                "ipadhub/app.py", "vendor/mouselink/open_bridge/main/src/app_main.c",
                "vendor/mouselink/open_bridge/firmware/firmware.bin",
                "vendor/mouselink/open_bridge/settings.json",
                "vendor/mouselink/open_bridge/settings.json.bak",
                "vendor/mouselink/open_bridge/settings.corrupt-123.json",
                "vendor/mouselink/open_bridge/firmware-backups/test/backup.json",
                "vendor/mouselink/open_bridge/firmware-backups/test/device-flash.bin",
                "vendor/ipaddisplay/logs/status.json", "vendor/ipaddisplay/config.json",
                "ipadhub/migration-last-report.json", "ipadhub/session.jsonl",
                "docs/.env", "scripts/auth.json", "artifacts/backups/old-user.json",
            ]
            for name in fixtures:
                destination = root / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(b"fixture")
            with patch.object(release, "ROOT", root):
                selected = {p.relative_to(root).as_posix() for p in release.source_paths()}
            self.assertEqual(selected, {"ipadhub/app.py",
                "vendor/mouselink/open_bridge/main/src/app_main.c",
                "vendor/mouselink/open_bridge/firmware/firmware.bin"})

    def test_current_cmake_and_integration_sources_are_complete(self):
        selected = {p.relative_to(release.ROOT).as_posix() for p in release.source_paths()}
        release.assert_sources(selected)
        with self.assertRaisesRegex(RuntimeError, "HubMain.cpp"):
            release.assert_sources(selected - {"vendor/ipaddisplay/src/HubMain.cpp"})


if __name__ == "__main__":
    unittest.main()
