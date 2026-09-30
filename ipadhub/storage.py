"""Isolated iPadHub data, copy-only migration and recoverable JSON settings.

No filesystem or registry writes occur merely by constructing HubStorage.
The application calls migrate only after acquiring its single-instance lock.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any


_SYSTEM_REGISTRY = object()
DEFAULT_SETTINGS = {"schema_version": 1, "theme": "system"}


def _read_object(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("Settings must be a JSON object")
    return value


def _atomic_bytes(path: Path, data: bytes, *, overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temporary, path)
        else:
            # Linking a completely written temporary file is atomic and fails
            # when another instance has already created the destination.
            os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class HubStorage:
    def __init__(self, root: Path | None = None, *, legacy_bridge: Path | None = None,
                 legacy_display: Path | None = None, registry: Any = _SYSTEM_REGISTRY):
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
        roaming = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
        self.root = Path(root) if root is not None else local / "iPadHub"
        self.display_dir = self.root / "display"
        self.bridge_dir = self.root / "mouselink"
        self.logs_dir = self.root / "logs"
        self.firmware_backups_dir = self.root / "firmware-backups"
        self.legacy_bridge = Path(legacy_bridge) if legacy_bridge is not None else local / "MouseLink"
        self.legacy_display = Path(legacy_display) if legacy_display is not None else roaming / "MouseLink"
        self.registry = registry
        self.last_warning = ""

    def _ensure_dirs(self) -> None:
        for directory in (self.root, self.display_dir, self.bridge_dir, self.logs_dir,
                          self.firmware_backups_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def load_settings(self) -> dict:
        return self.load_json(self.root / "settings.json", DEFAULT_SETTINGS)

    def save_settings(self, value: dict) -> None:
        self.save_json(self.root / "settings.json", value)

    def load_json(self, path: Path, defaults: dict | None = None) -> dict:
        """Load an owned settings file, recovering .bak without discarding damage."""
        self.last_warning = ""
        result = copy.deepcopy(defaults or {})
        path = Path(path)
        try:
            result.update(_read_object(path))
            return result
        except (OSError, ValueError) as error:
            if path.exists():
                self.last_warning = f"{path.name} 无法读取：{error}"
        backup = path.with_name(path.name + ".bak")
        try:
            recovered = _read_object(backup)
        except (OSError, ValueError):
            return result
        result.update(recovered)
        try:
            self._preserve_corrupt(path)
            _atomic_bytes(path, backup.read_bytes())
            self.last_warning = f"{path.name} 已从备份恢复。"
        except OSError as error:
            self.last_warning = f"已读取 {path.name} 的备份，但未能恢复文件：{error}"
        return result

    @staticmethod
    def _preserve_corrupt(path: Path) -> None:
        if path.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            _atomic_bytes(path.with_name(path.name + ".corrupt-" + stamp), path.read_bytes(), overwrite=False)

    def save_json(self, path: Path, value: dict) -> None:
        if not isinstance(value, dict):
            raise TypeError("Settings must be a dictionary")
        data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        path = Path(path)
        try:
            _read_object(path)
        except FileNotFoundError:
            pass
        except ValueError:
            self._preserve_corrupt(path)
        else:
            _atomic_bytes(path.with_name(path.name + ".bak"), path.read_bytes())
        _atomic_bytes(path, data)

    def migrate(self) -> dict:
        """Copy old settings/backups/positions once; never alter old app data.

        A failed migration has no completion marker and can be retried. Every
        retry skips existing new files and registry values, including settings
        the user has changed since the first attempt.
        """
        marker = self.root / "migration-v1.json"
        try:
            previous = _read_object(marker)
            if previous.get("complete") is True:
                return {**previous, "already_migrated": True}
        except (OSError, ValueError):
            pass
        report = {"schema_version": 1, "complete": False, "already_migrated": False,
                  "copied": [], "skipped": [], "errors": [],
                  "at": datetime.now(timezone.utc).isoformat()}
        try:
            self._ensure_dirs()
        except OSError as error:
            report["errors"].append(str(error))
            return report

        def copy_missing(source: Path, destination: Path, *, settings_file: bool = False) -> None:
            label = str(destination.relative_to(self.root))
            try:
                if not source.is_file() or source.is_symlink():
                    return
                if destination.exists():
                    report["skipped"].append(label)
                    return
                data = source.read_bytes()
                if settings_file and not isinstance(json.loads(data.decode("utf-8-sig")), dict):
                    raise ValueError("原设置不是有效的 JSON 对象，可使用默认设置并重试。")
                _atomic_bytes(destination, data, overwrite=False)
                shutil.copystat(source, destination)
                report["copied"].append(label)
            except FileExistsError:
                report["skipped"].append(label)
            except (OSError, ValueError) as error:
                report["errors"].append(f"{label}: {error}")

        # Current MouseLink stores settings.json. The older alternate name is
        # accepted only when that active file is absent. Source calibration
        # templates are intentionally not imported.
        bridge_source = self.legacy_bridge / "settings.json"
        if not bridge_source.exists():
            bridge_source = self.legacy_bridge / "appsettings.json"
        for suffix in ("", ".bak"):
            copy_missing(bridge_source.with_name(bridge_source.name + suffix),
                         self.bridge_dir / ("settings.json" + suffix), settings_file=True)
            copy_missing(self.legacy_display / ("config.json" + suffix),
                         self.display_dir / ("config.json" + suffix), settings_file=True)
        source_backups = self.legacy_bridge / "firmware-backups"
        try:
            if source_backups.is_dir() and not source_backups.is_symlink():
                def walk_error(error):
                    raise error
                for parent, directories, filenames in os.walk(source_backups, followlinks=False, onerror=walk_error):
                    directories[:] = [name for name in directories if not (Path(parent) / name).is_symlink()]
                    for name in filenames:
                        source = Path(parent) / name
                        copy_missing(source, self.firmware_backups_dir / source.relative_to(source_backups))
        except OSError as error:
            report["errors"].append(f"firmware-backups: {error}")
        self._migrate_positions(report)
        report["complete"] = not report["errors"]
        try:
            self.save_json(self.root / "migration-last-report.json", report)
            if report["complete"]:
                self.save_json(marker, report)
        except OSError as error:
            report["complete"] = False
            report["errors"].append(f"迁移记录无法保存：{error}")
        return report

    def _migrate_positions(self, report: dict) -> None:
        registry = self.registry
        if registry is False or registry is None:
            return
        if registry is _SYSTEM_REGISTRY:
            try:
                import winreg as registry
            except ImportError:
                return
        source_path = r"Software\MouseLink"
        target_path = r"Software\iPadHub\Display"
        try:
            with registry.OpenKey(registry.HKEY_CURRENT_USER, source_path, 0, registry.KEY_READ) as source:
                values = []
                for index in range(registry.QueryInfoKey(source)[1]):
                    name, value, kind = registry.EnumValue(source, index)
                    if kind == registry.REG_DWORD and (name in ("monitorX", "monitorY") or
                                                       name.startswith(("monitorX_", "monitorY_"))):
                        values.append((name, value, kind))
        except FileNotFoundError:
            return
        except OSError as error:
            report["errors"].append(f"显示位置：{error}")
            return
        if not values:
            return
        try:
            with registry.CreateKeyEx(registry.HKEY_CURRENT_USER, target_path, 0,
                                      registry.KEY_READ | registry.KEY_WRITE) as target:
                for name, value, kind in values:
                    label = target_path + "\\" + name
                    try:
                        registry.QueryValueEx(target, name)
                    except FileNotFoundError:
                        registry.SetValueEx(target, name, 0, kind, value)
                        report["copied"].append(label)
                    else:
                        report["skipped"].append(label)
        except OSError as error:
            report["errors"].append(f"显示位置：{error}")
