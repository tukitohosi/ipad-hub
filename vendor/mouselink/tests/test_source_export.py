"""Public source export checks use only temporary files; no hardware or network."""
import json
from pathlib import Path
import runpy
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXPORT = runpy.run_path(str(ROOT / "tools/Export-Source.py"))


class SourceExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "working-tree"
        for name in EXPORT["SOURCE_FILES"]:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"source text\n")
        for name in EXPORT["EMPTY_SETTINGS"]:
            (self.source / name).write_text(
                json.dumps({"device_serial": "private-device", "landscape": True}), encoding="utf-8")

    def test_only_selected_source_is_exported_and_device_settings_are_empty(self):
        excluded = (
            "test-results/trace.json", "runtime/python.exe", "third_party/upstream/README.md",
            "open_bridge/firmware/firmware.bin", "open_bridge/candidates/experiment/bridge.py",
            "open_bridge/licenses/esptool-5.4.0-source.zip", "tools/Start-DShareHidPortable.ps1",
            "tests/private-fixture.json", "open_bridge/private-notes.txt", "open_bridge/extra.py",
            "open_bridge/assets/extra.dll", "open_bridge/dist/firmware.elf",
        )
        for name in excluded:
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("private-device", encoding="utf-8")
        output = self.base / "public-source"
        count = EXPORT["export_source"](output, self.source)
        exported = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
        self.assertEqual(exported, set(EXPORT["SOURCE_FILES"]))
        self.assertEqual(count, len(exported))
        for name in exported:
            self.assertNotIn(b"private-device", (output / name).read_bytes())
        for name in EXPORT["EMPTY_SETTINGS"]:
            self.assertEqual(json.loads((output / name).read_text()), {})
            self.assertIn("private-device", (self.source / name).read_text())

    def test_source_archive_and_directory_share_exact_names_and_bytes(self):
        output = self.base / "public-source"
        archive = self.base / "source.zip"
        EXPORT["export_source"](output, self.source)
        EXPORT["write_source_archive"](archive, self.source)
        with zipfile.ZipFile(archive) as contents:
            self.assertEqual(set(contents.namelist()), set(EXPORT["SOURCE_FILES"]))
            for name in contents.namelist():
                self.assertEqual(contents.read(name), (output / name).read_bytes())

    def test_missing_required_source_fails_before_creating_export(self):
        (self.source / "open_bridge/native_edges.py").unlink()
        output = self.base / "public-source"
        with self.assertRaisesRegex(FileNotFoundError, "native_edges.py"):
            EXPORT["export_source"](output, self.source)
        self.assertFalse(output.exists())

    def test_nonempty_destination_is_preserved(self):
        output = self.base / "public-source"
        output.mkdir()
        sentinel = output / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "new or empty"):
            EXPORT["export_source"](output, self.source)
        self.assertEqual(list(output.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(), "keep")

    def test_accidental_binary_source_is_rejected_before_output(self):
        (self.source / "tools/Build-Desktop.ps1").write_bytes(b"\xff\x00")
        output = self.base / "public-source"
        with self.assertRaises(UnicodeDecodeError):
            EXPORT["export_source"](output, self.source)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
