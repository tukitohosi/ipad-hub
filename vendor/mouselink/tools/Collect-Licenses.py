"""Retain runtime licenses and complete esptool Python sources in the release."""
import importlib.metadata as metadata
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
licenses = ROOT / "open_bridge/licenses"
licenses.mkdir(parents=True, exist_ok=True)
inventory = []
packages = ["esptool", "esp-pylib", "cryptography", "cffi", "pycparser", "bitstring", "bitarray",
            "reedsolo", "intelhex", "PyYAML", "rich-click", "rich", "click", "markdown-it-py",
            "mdurl", "Pygments", "websockets", "typing_extensions", "colorama"]
for package in packages:
    try:
        dist = metadata.distribution(package)
    except metadata.PackageNotFoundError:
        continue
    inventory.append({"name": dist.metadata["Name"], "version": dist.version,
                      "license": dist.metadata.get("License-Expression") or dist.metadata.get("License", "")})
    for relative in dist.files or []:
        if any(word in relative.name.upper() for word in ("LICENSE", "COPYING", "NOTICE")):
            source = Path(dist.locate_file(relative))
            if source.is_file():
                (licenses / f"{package}-{relative.name}.txt").write_bytes(source.read_bytes())
(licenses / "flash-runtime.json").write_text(json.dumps(inventory, indent=2), encoding="utf-8")
esptool_dist = metadata.distribution("esptool")
with zipfile.ZipFile(licenses / "esptool-5.4.0-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    for relative in esptool_dist.files or []:
        source = Path(esptool_dist.locate_file(relative))
        if source.is_file() and "__pycache__" not in relative.parts and ".." not in relative.parts:
            archive.write(source, str(relative))
print("Collected runtime notices and esptool source.")
