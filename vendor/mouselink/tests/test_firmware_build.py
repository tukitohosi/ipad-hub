"""Offline build-entry tests: tiny fixtures and a fake PlatformIO process only."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_firmware_bundle", ROOT / "tools/build_firmware_bundle.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class BundleBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build = self.root / "build"
        self.build.mkdir()
        self.output = self.root / "bundle"
        self.base = ROOT / "open_bridge/firmware/manifest.json"
        self.original = json.loads(self.base.read_text(encoding="utf-8"))
        self.payloads = {name: ("fixture-" + name).encode() for name in builder.OFFSETS}
        for name, data in self.payloads.items():
            (self.build / name).write_bytes(data)

    def test_all_images_hashes_and_legacy_allowlists_are_preserved(self):
        manifest = builder.publish_bundle(self.build, self.output, self.base, "fixture")
        loaded, images = builder.load_bundle(self.output)
        self.assertEqual(loaded, manifest)
        self.assertEqual(manifest["version"], "fixture")
        for name, address in builder.OFFSETS.items():
            self.assertEqual(images[address], self.payloads[name])
            self.assertEqual(manifest["images"][name], {
                "offset": address, "size": len(self.payloads[name]),
                "sha256": builder.digest(self.payloads[name])})
        for key, name in (("compatible_apps", "firmware.bin"),
                          ("compatible_bootloaders", "bootloader.bin")):
            self.assertEqual(manifest[key][:-1], self.original[key])
            self.assertEqual(manifest[key][-1], {
                "size": len(self.payloads[name]), "sha256": builder.digest(self.payloads[name])})
        self.assertEqual(json.loads(self.base.read_text(encoding="utf-8")), self.original)

    def test_republishing_deduplicates_current_build_allowlists(self):
        first = builder.publish_bundle(self.build, self.output, self.base)
        second = builder.publish_bundle(self.build, self.output, self.output / "manifest.json", "fixture")
        self.assertEqual(first["compatible_apps"], second["compatible_apps"])
        self.assertEqual(first["compatible_bootloaders"], second["compatible_bootloaders"])

    def test_same_generated_build_remains_eligible_for_nvs_preserving_reinstall(self):
        from firmware_bundle import compatible_backup
        manifest = builder.publish_bundle(self.build, self.output, self.base)
        _, images = builder.load_bundle(self.output)
        installed = bytearray(b"\xff" * builder.FLASH_SIZE)
        for address, data in images.items():
            installed[address:address + len(data)] = data
        self.assertTrue(compatible_backup(bytes(installed), manifest, images))

    def test_missing_image_never_falls_back_to_old_bundle(self):
        builder.publish_bundle(self.build, self.output, self.base)
        before = {p.name: p.read_bytes() for p in self.output.iterdir()}
        (self.build / "bootloader.bin").unlink()
        with self.assertRaises(FileNotFoundError):
            builder.publish_bundle(self.build, self.output, self.base)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_invalid_image_size_writes_nothing(self):
        for data in (b"", b"x" * (builder.LIMITS["partitions.bin"] + 1)):
            (self.build / "partitions.bin").write_bytes(data)
            with self.assertRaises(ValueError):
                builder.publish_bundle(self.build, self.output, self.base)
            self.assertFalse(self.output.exists())

    def test_wrong_chip_writes_nothing(self):
        wrong = self.root / "wrong.json"
        wrong.write_text(json.dumps({**self.original, "chip": "esp32s3"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            builder.publish_bundle(self.build, self.output, wrong)
        self.assertFalse(self.output.exists())


@unittest.skipUnless(os.name == "nt", "PowerShell build entry is Windows-only")
class PowerShellBuildTests(unittest.TestCase):
    def setUp(self):
        # Same ASCII system-temp fallback as the build entry; no machine-specific drive.
        temp_root = Path(tempfile.gettempdir())
        if not str(temp_root).isascii() or " " in str(temp_root):
            temp_root = Path(os.environ["SystemRoot"]) / "Temp"
        self.temp = tempfile.TemporaryDirectory(prefix="mouselink-build-test-", dir=temp_root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "source's clone"
        (self.workspace / "tools").mkdir(parents=True)
        (self.workspace / "open_bridge/main").mkdir(parents=True)
        (self.workspace / "open_bridge/firmware").mkdir()
        for name in ("Build-Firmware.ps1", "build_firmware_bundle.py"):
            shutil.copyfile(ROOT / "tools" / name, self.workspace / "tools" / name)
        shutil.copyfile(ROOT / "open_bridge/firmware_bundle.py", self.workspace / "open_bridge/firmware_bundle.py")
        shutil.copyfile(ROOT / "open_bridge/firmware/manifest.json", self.workspace / "open_bridge/firmware/manifest.json")
        for name in ("CMakeLists.txt", "platformio.ini", "sdkconfig.defaults",
                     "sdkconfig.defaults.esp32c3", "sdkconfig.esp32-c3-supermini"):
            (self.workspace / "open_bridge" / name).write_text("fixture", encoding="ascii")
        self.calls = self.root / "calls.json"
        fake_script = self.root / "fake_python.py"
        fake_script.write_text(
            "import json,os,pathlib,subprocess,sys\n"
            "args=sys.argv[1:]\n"
            "if args[:2] == ['-m','platformio']:\n"
            "    stage=pathlib.Path(args[args.index('-d')+1])\n"
            "    pathlib.Path(os.environ['MOUSELINK_TEST_CALLS']).write_text(json.dumps({'args':args,'temp':os.environ['TEMP'],'proxy':os.environ.get('HTTPS_PROXY')}))\n"
            "    if os.environ.get('MOUSELINK_TEST_FAIL'): sys.exit(7)\n"
            "    out=stage/'.pio/build/esp32-c3-supermini'; out.mkdir(parents=True)\n"
            "    for name in ('bootloader.bin','partitions.bin','firmware.bin'): (out/name).write_bytes(('fixture-'+name).encode())\n"
            "else: sys.exit(subprocess.call([sys.executable,*args]))\n",
            encoding="utf-8")
        self.python = self.root / "fake-python.cmd"
        self.python.write_text(f'@echo off\n"{sys.executable}" "{fake_script}" %*\n', encoding="utf-8")
        self.env = dict(os.environ, MOUSELINK_TEST_CALLS=str(self.calls))
        self.shell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")

    def run_build(self, *args):
        return subprocess.run([self.shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(self.workspace / "tools/Build-Firmware.ps1"), "-Python", str(self.python), *map(str, args)],
            cwd=self.workspace, env=self.env, capture_output=True, text=True, encoding="utf-8", errors="replace")

    def test_fresh_unregistered_root_and_explicit_bundle(self):
        output = self.root / "bundle with spaces"
        result = self.run_build("-BuildRoot", self.root / "isolated", "-PublishBundle", output,
                                "-BundleVersion", "offline-fixture", "-DirectDownload")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = json.loads(self.calls.read_text())
        stage = Path(calls["args"][calls["args"].index("-d") + 1])
        self.assertEqual(stage.parent, self.root / "isolated")
        self.assertTrue((stage / "main").is_dir())
        self.assertTrue(Path(calls["temp"]).is_dir())
        self.assertFalse(calls["proxy"])
        manifest, _ = builder.load_bundle(output)
        self.assertEqual(manifest["version"], "offline-fixture")
        self.assertFalse((self.workspace / "open_bridge/firmware/firmware.bin").exists())

    def test_no_publish_leaves_source_bundle_untouched_and_stage_is_fresh(self):
        base = (self.workspace / "open_bridge/firmware/manifest.json").read_bytes()
        stages = []
        for _ in range(2):
            result = self.run_build("-BuildRoot", self.root / "isolated")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            args = json.loads(self.calls.read_text())["args"]
            stages.append(args[args.index("-d") + 1])
        self.assertNotEqual(stages[0], stages[1])
        self.assertEqual((self.workspace / "open_bridge/firmware/manifest.json").read_bytes(), base)
        self.assertFalse((self.workspace / "open_bridge/firmware/firmware.bin").exists())

    def test_failed_build_never_publishes(self):
        self.env["MOUSELINK_TEST_FAIL"] = "1"
        output = self.root / "bundle"
        result = self.run_build("-BuildRoot", self.root / "isolated", "-PublishBundle", output)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_default_build_root_is_unique_under_system_temp(self):
        temp_root = self.root / "system-temp"
        temp_root.mkdir()
        self.env.update(TEMP=str(temp_root), TMP=str(temp_root))
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        args = json.loads(self.calls.read_text())["args"]
        build_root = Path(args[args.index("-d") + 1]).parent
        self.assertEqual(build_root.parent, temp_root)
        self.assertTrue(build_root.name.startswith("MouseLink-build-"))
        self.assertTrue(str(build_root).isascii())
        self.assertNotIn(" ", str(build_root))

    def test_unsafe_build_path_is_rejected_before_python(self):
        result = self.run_build("-BuildRoot", self.root / "has spaces")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
