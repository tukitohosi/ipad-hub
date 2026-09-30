"""Validated firmware and same-device backups. No USB side effects."""
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

FLASH_SIZE = 4 * 1024 * 1024
OFFSETS = {"bootloader.bin": 0, "partitions.bin": 0x8000, "firmware.bin": 0x10000}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def normalized_serial(value):
    return str(value or "").replace(":", "").replace("-", "").upper()


def load_bundle(root=None):
    root = Path(root) if root else Path(__file__).with_name("firmware")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("chip") != "esp32c3" or manifest.get("flash_size") != FLASH_SIZE:
        raise ValueError("固件包型号或容量不正确，请重新安装 MouseLink。")
    images = {}
    for name, address in OFFSETS.items():
        item = manifest["images"][name]
        data = (root / name).read_bytes()
        limit = {"bootloader.bin": 0x8000, "partitions.bin": 0x1000, "firmware.bin": 0x100000}[name]
        if (item["offset"] != address or not 0 < len(data) <= limit
                or len(data) != item["size"] or digest(data) != item["sha256"]):
            raise ValueError("固件文件校验失败，请重新安装 MouseLink；设备尚未写入。")
        images[address] = data
    return manifest, images


def compatible_backup(data, manifest, images):
    """Only a byte-verified known build may preserve NVS and partition state."""
    if len(data) != FLASH_SIZE:
        return False
    partition = images[0x8000]
    if data[0x8000:0x8000 + len(partition)] != partition:
        return False
    bootloaders = manifest.get("compatible_bootloaders", [
        {"size": len(images[0]), "sha256": digest(images[0])}])
    if not any(isinstance(item.get("size"), int) and 0 < item["size"] <= 0x8000
               and digest(data[:item["size"]]) == item["sha256"] for item in bootloaders):
        return False
    known = manifest.get("compatible_apps", [])
    return any(isinstance(item.get("size"), int) and 0 < item["size"] <= 0x100000
               and digest(data[0x10000:0x10000 + item["size"]]) == item["sha256"]
               for item in known)


def save_backup(root, data, mac, version):
    if len(data) != FLASH_SIZE:
        raise ValueError("备份长度不正确，已停止刷机。")
    folder = Path(root) / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{normalized_serial(mac)}"
    folder.mkdir(parents=True, exist_ok=False)
    binary = folder / "device-flash.bin"
    with binary.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    if digest(binary.read_bytes()) != digest(data):
        raise ValueError("备份保存后校验失败，已停止刷机。")
    metadata = {"format": 1, "chip": "esp32c3", "flash_size": FLASH_SIZE,
                "mac": normalized_serial(mac), "sha256": digest(data),
                "created_at": datetime.now().astimezone().isoformat(), "app_version": version}
    path = folder / "backup.json"
    with path.open("x", encoding="utf-8") as output:
        json.dump(metadata, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    return path


def load_backup(path, mac=None):
    path = Path(path)
    metadata = json.loads(path.read_text(encoding="utf-8"))
    data = path.with_name("device-flash.bin").read_bytes()
    if (metadata.get("format") != 1 or metadata.get("chip") != "esp32c3"
            or metadata.get("flash_size") != FLASH_SIZE or len(data) != FLASH_SIZE
            or digest(data) != metadata.get("sha256")):
        raise ValueError("备份文件损坏或格式不支持，未写入设备。")
    if mac is not None and normalized_serial(mac) != metadata.get("mac"):
        raise ValueError("这个备份属于另一块开发板，不能恢复到当前设备。")
    return metadata, data
