"""Build auditable source/portable ZIPs without reading old app data."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("ipadhub", "scripts", "tests", "packaging", "docs", "vendor")
SOURCE_FILES = ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", ".gitignore", "启动iPadHub预览.cmd")
SKIP_DIRS = {".git", ".venv", ".tools", "__pycache__", ".pytest_cache", "build", "out", "dist",
             "artifacts", "release", "reports", "node_modules", ".pio", "candidates",
             "logs", "flash-logs", "firmware-backups"}
ALLOWED_EXTENSIONS = {".py", ".ps1", ".cmd", ".bat", ".spec", ".iss", ".isl", ".md", ".txt",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cpp", ".c", ".h", ".hpp", ".rc",
    ".cmake", ".in", ".ico", ".svg", ".png", ".ui", ".qrc", ".conf", ".defaults", ".esp32c3",
    ".esp32-c3-supermini", ".csv", ".html", ".css", ".js", ".mjs", ".ts", ".sh", ".patch",
    ".cs", ".csproj", ".xml", ".xaml", ".manifest", ".m", ".mm", ".plist", ".entitlements",
    ".service", ".desktop", ".projbuild", ".pio", ".lua", ".theme", ".example", ".applescript",
    ".icns", ".tiff"}
SPECIAL_FILES = {"vendor/mouselink/open_bridge/dist/bootloader.bin"}


def source_paths():
    found = {ROOT / name for name in SOURCE_FILES if (ROOT / name).is_file()}
    for name in SOURCE_DIRS:
        for path in (ROOT / name).rglob("*"):
            relative = path.relative_to(ROOT)
            normalized = relative.as_posix()
            if not path.is_file() or path.is_symlink():
                continue
            if normalized in SPECIAL_FILES:
                found.add(path)
                continue
            if any(part in SKIP_DIRS for part in relative.parts):
                continue
            if (path.name.lower().startswith((".env", "migration-", "settings.corrupt-", "config.corrupt-")) or
                    path.name.lower() in {"auth.json", "settings.json", "config.json"}):
                continue
            if path.suffix == ".zip":
                if normalized == "vendor/mouselink/open_bridge/licenses/esptool-5.4.0-source.zip":
                    found.add(path)
                continue
            if path.suffix == ".bin":
                if normalized.startswith("vendor/mouselink/open_bridge/firmware/"):
                    found.add(path)
                continue
            if (path.suffix.lower() in ALLOWED_EXTENSIONS or not path.suffix or
                    path.name.startswith(("LICENSE", "COPYING", "sdkconfig", ".git", ".clang", ".editor"))):
                found.add(path)
    return sorted(found)


def sha256(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def make_zip(destination, files, prefix):
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for source, relative in files:
            archive.write(source, prefix + "/" + relative)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP CRC validation failed")


def assert_bundle(bundle):
    required = ["iPadHub.exe", "MouseLinkFlash.exe", "_internal/engines/iPadHubDisplay.exe",
                "_internal/bridge/bridge.py", "_internal/bridge/firmware/firmware.bin",
                "_internal/firmware/manifest.json", "_internal/PySide6/Qt6Core.dll"]
    for relative in required:
        if not (bundle / relative).is_file():
            raise RuntimeError("Incomplete bundle: " + relative)
    for path in bundle.rglob("*"):
        if path.is_file() and path.suffix.lower() in (".sys", ".inf", ".cat"):
            raise RuntimeError("Driver artifacts must never be bundled: " + str(path))
    for relative in required[:3]:
        with (bundle / relative).open("rb") as handle:
            if handle.read(2) != b"MZ":
                raise RuntimeError("Not a PE executable: " + relative)
            handle.seek(0x3C)
            handle.seek(int.from_bytes(handle.read(4), "little"))
            if handle.read(6) != b"PE\0\0\x64\x86":
                raise RuntimeError("Not a Windows x64 executable: " + relative)


def assert_sources(included):
    required = {"ipadhub/app.py", "ipadhub/live_window.py", "ipadhub/storage.py", "ipadhub/runtime.py",
        "ipadhub/engines/bridge.py", "ipadhub/engines/pipe_client.py", "vendor/ipaddisplay/CMakeLists.txt",
        "vendor/ipaddisplay/src/HubMain.cpp", "vendor/ipaddisplay/src/app/HubJson.h",
        "vendor/ipaddisplay/third_party/parsec-vdd/parsec-vdd.h", "vendor/mouselink/open_bridge/bridge.py",
        "vendor/mouselink/open_bridge/flash_helper.py", "vendor/mouselink/open_bridge/flash_dialog.py",
        "vendor/mouselink/open_bridge/main/src/ble_hid_api.c", "scripts/build-display-engine.ps1",
        "scripts/package-ipadhub.ps1", "packaging/iPadHub.spec", "packaging/check-frozen-engines.py", "LICENSE"}
    cmake = (ROOT / "vendor/ipaddisplay/CMakeLists.txt").read_text(encoding="utf-8-sig")
    required.update("vendor/ipaddisplay/" + match for match in
                    re.findall(r"\b((?:src|tests)/[A-Za-z0-9_./-]+\.(?:cpp|h|rc))\b", cmake))
    missing = required - included
    if missing:
        raise RuntimeError("Corresponding source is missing: " + ", ".join(sorted(missing)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", default="0.1.0-preview")
    args = parser.parse_args()
    bundle, output = args.bundle.resolve(), args.output.resolve()
    bundle.relative_to(ROOT / "build")
    output.relative_to(ROOT / "release")
    assert_bundle(bundle)
    output.mkdir(parents=True, exist_ok=True)
    licenses = bundle / "licenses"
    shutil.copytree(ROOT / "vendor/mouselink/open_bridge/licenses", licenses, dirs_exist_ok=True)
    for source, target in (
        ("vendor/mouselink/open_bridge/LICENSE", "mouselink-MIT.txt"),
        ("vendor/mouselink/open_bridge/THIRD_PARTY_NOTICES.md", "mouselink-notices.md"),
        ("vendor/ipaddisplay/THIRD_PARTY_NOTICES.txt", "ipaddisplay-notices.txt"),
        ("vendor/ipaddisplay/LICENSE", "ipaddisplay-GPL-3.0.txt")):
        shutil.copy2(ROOT / source, licenses / target)
    environment = subprocess.check_output([sys.executable, "-m", "pip", "freeze", "--all"], text=True)
    (licenses / "build-environment.txt").write_text(environment, encoding="utf-8")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, bundle / name)
    (bundle / "docs").mkdir(exist_ok=True)
    for name in ("packaging.md", "使用说明.md", "release-acceptance.md"):
        source = ROOT / "docs" / name
        if not source.is_file():
            raise RuntimeError("Required release document is missing: " + name)
        shutil.copy2(source, bundle / "docs" / name)
    source_archive = output / f"iPadHub-{args.version}-source.zip"
    paths = source_paths()
    included = {path.relative_to(ROOT).as_posix() for path in paths}
    assert_sources(included)
    make_zip(source_archive, [(p, p.relative_to(ROOT).as_posix()) for p in paths], f"iPadHub-{args.version}-source")
    with zipfile.ZipFile(source_archive) as archive:
        physical_names = {name.split("/", 1)[1] for name in archive.namelist()}
        if physical_names != included:
            raise RuntimeError("Written source ZIP does not match the selected complete source tree")
    (bundle / "source").mkdir(exist_ok=True)
    shutil.copy2(source_archive, bundle / "source" / source_archive.name)
    manifest = {path.relative_to(bundle).as_posix(): sha256(path)
                for path in sorted(bundle.rglob("*")) if path.is_file()}
    (bundle / "bundle-SHA256.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    portable = output / f"iPadHub-{args.version}-Windows-x64.zip"
    make_zip(portable, [(p, p.relative_to(bundle).as_posix()) for p in sorted(bundle.rglob("*")) if p.is_file()], "iPadHub")
    report = {"version": args.version, "source_files": len(paths), "bundle_files": len(manifest),
              "source_sha256": sha256(source_archive), "portable_sha256": sha256(portable),
              "driver_binaries_included": False, "runtime_data_paths_excluded": True,
              "hardware_acceptance": "not verified"}
    (output / "packaging-verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
