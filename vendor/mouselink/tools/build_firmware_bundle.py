"""Publish three images from one PlatformIO build; never opens a device."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open_bridge"))
from firmware_bundle import FLASH_SIZE, OFFSETS, digest, load_bundle

LIMITS = {"bootloader.bin": 0x8000, "partitions.bin": 0x1000, "firmware.bin": 0x100000}


def publish_bundle(build_dir, output, base_manifest, version=None):
    build_dir, output = Path(build_dir), Path(output)
    manifest = json.loads(Path(base_manifest).read_text(encoding="utf-8-sig"))
    if manifest.get("chip") != "esp32c3" or manifest.get("flash_size") != FLASH_SIZE:
        raise ValueError("The base manifest must describe a 4 MiB ESP32-C3 bundle.")
    # Read every image before touching the destination. No fallback to old images.
    payloads = {name: (build_dir / name).read_bytes() for name in OFFSETS}
    for name, data in payloads.items():
        if not 0 < len(data) <= LIMITS[name]:
            raise ValueError(f"Invalid image size: {name}")
    manifest["images"] = {
        name: {"offset": offset, "size": len(payloads[name]), "sha256": digest(payloads[name])}
        for name, offset in OFFSETS.items()
    }
    manifest["version"] = version or (manifest["version"] + " (local build)")
    # Include this exact build so reinstalling it can preserve NVS; keep every
    # legacy allowlist entry. These hashes do not assert iPad behavior acceptance.
    for key, name in (("compatible_apps", "firmware.bin"),
                      ("compatible_bootloaders", "bootloader.bin")):
        known = manifest.setdefault(key, [])
        item = {"size": len(payloads[name]), "sha256": digest(payloads[name])}
        if not any(entry.get("size") == item["size"] and entry.get("sha256") == item["sha256"]
                   for entry in known):
            known.append(item)
    output.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        (output / name).write_bytes(data)
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load_bundle(output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True,
                        help="One .pio/build/esp32-c3-supermini directory containing all three images")
    parser.add_argument("--output", type=Path, required=True,
                        help="Explicit destination; existing bundle files are replaced")
    parser.add_argument("--base-manifest", type=Path,
                        default=ROOT / "open_bridge/firmware/manifest.json")
    parser.add_argument("--version", help="Bundle label; defaults to the base label plus '(local build)'")
    args = parser.parse_args()
    publish_bundle(args.build_dir, args.output, args.base_manifest, args.version)
    print(args.output.resolve() / "manifest.json")


if __name__ == "__main__":
    main()
