"""Restore verified official PlatformIO tools when whole-file CDN downloads stall."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import requests
from semantic_version import Spec, Version

ROOT = Path(__file__).resolve().parents[1]
TOOLS = {
    "tool-scons": "~4.41101.0",
    "tool-xtensa-esp-elf-gdb": "~11.2.0",
    "tool-riscv32-esp-elf-gdb": "~11.2.0",
    "toolchain-esp32ulp": "~1.23800.0",
    "tool-esptoolpy": "~2.41100.0",
    "tool-cmake": "~3.30.0",
    "tool-ninja": "^1.7.0",
    "tool-mconf": "~1.4060000.0",
    "tool-idf": "~1.0.1",
    "tool-esp-rom-elfs": "0.0.1+20241011",
}
session = requests.Session()
session.trust_env = False
core = Path(os.environ["PLATFORMIO_CORE_DIR"])
for name, constraint in TOOLS.items():
    owner = "espressif" if name.endswith("-gdb") else "platformio"
    metadata = core / "packages" / name / ".piopm"
    if metadata.exists():
        existing = json.loads(metadata.read_text())
        if existing.get("spec", {}).get("owner") == owner and Spec(constraint).match(Version(existing["version"])):
            print("Already installed:", name, flush=True)
            continue
    response = session.get(f"https://api.registry.platformio.org/v3/packages/{owner}/tool/{name}", timeout=30)
    response.raise_for_status()
    package = response.json()
    version, artifact = next((v, f) for v in package["versions"]
                             if Spec(constraint).match(Version(v["name"]))
                             for f in v["files"]
                             if {"windows_amd64", "windows_x86", "windows", "*"}.intersection(
                                 f["system"] if isinstance(f["system"], list) else [f["system"]]))
    archive = ROOT / "runtime" / "downloads" / artifact["name"]
    expected = artifact["checksum"]["sha256"]
    print("Restoring:", name, version["name"], artifact["size"], flush=True)
    valid = False
    if archive.exists():
        with archive.open("rb") as stream:
            valid = hashlib.file_digest(stream, "sha256").hexdigest() == expected
    if not valid:
        subprocess.run([sys.executable, str(ROOT / "tools" / "download_verified.py"),
                        artifact["download_url"], str(archive), expected, "--workers", "8"], check=True)
    subprocess.run([sys.executable, "-m", "platformio", "pkg", "install", "--global",
                    "--tool", "file://" + archive.as_posix()], check=True)
    data = json.loads(metadata.read_text())
    assert data["name"] == name and data["version"] == version["name"]
    # The archive hash was checked against PlatformIO's registry. Record its
    # actual owner; keep the local source URI for provenance.
    data["spec"]["owner"] = owner
    metadata.write_text(json.dumps(data), encoding="utf-8")
print("Official PlatformIO tools restored.", flush=True)
