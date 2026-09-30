# SPDX-License-Identifier: GPL-2.0-or-later
"""Isolated esptool worker; newline JSON on stdout, no GUI or input hooks."""
import argparse
import contextlib
import json
import sys
import time
import traceback
from pathlib import Path

import esptool
from esptool import cmds
from esptool.logger import TemplateLogger, log
from serial import Serial
from serial.tools.list_ports import comports

from app_version import APP_VERSION
from absolute_protocol import StatusParser
from firmware_bundle import (FLASH_SIZE, compatible_backup, load_backup, load_bundle,
                             normalized_serial, save_backup)


class Events(TemplateLogger):
    def __init__(self, emit):
        self.emit = emit
        self.current = "prepare"
        self.last_percent = -1

    def phase(self, name, text, **fields):
        self.current, self.last_percent = name, -1
        self.emit({"event": "phase", "phase": name, "message": text, **fields})

    def print(self, *args, **kwargs):
        self.emit({"event": "log", "message": " ".join(str(a) for a in args)})

    note = print
    warning = print
    error = print

    def stage(self, finish=False):
        pass

    def set_verbosity(self, verbosity):
        pass

    def progress_bar(self, cur_iter, total_iters, **kwargs):
        percent = max(0, min(100, int(cur_iter * 100 / max(1, total_iters))))
        if percent != self.last_percent:
            self.last_percent = percent
            self.emit({"event": "progress", "phase": self.current, "percent": percent})


def supported_ports():
    return [p for p in comports() if p.vid == 0x303A and p.pid == 0x1001]


def check_selected_port(port, expected_serial):
    matches = [p for p in supported_ports() if p.device == port]
    if len(matches) != 1:
        raise ValueError("没有找到所选原生 USB 开发板，请检查数据线并刷新设备。")
    if expected_serial and normalized_serial(matches[0].serial_number) != normalized_serial(expected_serial):
        raise ValueError("所选端口的设备已改变，请重新选择开发板。")


def verify_restart(mac, seconds=12):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for port in supported_ports():
            if normalized_serial(port.serial_number) != normalized_serial(mac):
                continue
            try:
                connection = Serial(port=None, baudrate=115200, timeout=.15, write_timeout=.5)
                connection.dtr = connection.rts = False
                connection.port = port.device
                with connection:
                    parser = StatusParser()
                    until = min(deadline, time.monotonic() + 2)
                    while time.monotonic() < until:
                        state = parser.feed(connection.read(128), time.monotonic())
                        if state.version == 3:
                            return True
            except OSError:
                pass
        time.sleep(.25)
    return False


def flash(args, events):
    manifest, images = load_bundle(args.bundle)
    if args.restore:
        load_backup(args.restore)  # Check file before resetting any board.
    check_selected_port(args.port, args.serial)
    events.phase("connect", "正在连接开发板，请保持 USB 连接…")
    esp = None
    reset_done = False
    try:
        esp = cmds.detect_chip(args.port, connect_attempts=2)
        if esp.CHIP_NAME != "ESP32-C3":
            raise ValueError("仅支持 ESP32-C3 SuperMini 原生 USB 开发板，未写入设备。")
        if esp.secure_download_mode or esp.get_secure_boot_enabled() or esp.get_flash_encryption_enabled():
            raise ValueError("开发板启用了安全启动或加密，不能使用此刷机功能；未写入。")
        mac = normalized_serial(bytes(esp.read_mac()).hex())
        if args.serial and normalized_serial(args.serial) != mac:
            raise ValueError("芯片身份与所选 USB 设备不符，已停止。")
        esp = cmds.run_stub(esp)
        cmds.attach_flash(esp)
        if cmds.detect_flash_size(esp) != "4MB":
            raise ValueError("当前版本仅支持 4 MB 同款开发板，未写入设备。")
        restore_data = load_backup(args.restore, mac)[1] if args.restore else None
        events.phase("backup", "正在备份原固件，备份通过校验后才会写入…", mac=mac)
        data = cmds.read_flash(esp, 0, FLASH_SIZE)
        cmds.verify_flash(esp, [(0, data)])
        backup = save_backup(args.backup_root, data, mac, APP_VERSION)
        events.emit({"event": "backup", "path": str(backup)})
        if getattr(args, "backup_only", False):
            events.phase("restart", "备份校验通过，正在恢复开发板运行…")
            cmds.reset_chip(esp, "hard-reset")
            reset_done = True
            events.emit({"event": "result", "ok": True, "kind": "backup",
                         "backup": str(backup), "message": "原固件备份与校验已完成，未擦除或写入固件。"})
            return
        compatible = compatible_backup(data, manifest, images)
        if restore_data is not None:
            to_write, kind = [(0, restore_data)], "restore"
        elif compatible:
            to_write, kind = [(0x10000, images[0x10000])], "upgrade"
        else:
            to_write, kind = sorted(images.items()), "initialize"
        events.phase("write", {"upgrade": "正在升级固件，保留蓝牙配对信息…",
                               "initialize": "正在初始化并刷入完整固件…",
                               "restore": "正在恢复这块开发板的备份…"}[kind], protected=True, kind=kind)
        if kind in ("initialize", "restore"):
            cmds.erase_flash(esp)
        # Keep the approved image headers unchanged, including on full restore.
        cmds.write_flash(esp, to_write, flash_mode="keep", flash_freq="keep", flash_size="keep")
        events.phase("verify", "正在校验已写入的固件…", protected=True)
        cmds.verify_flash(esp, to_write)
        events.phase("restart", "固件校验通过，正在重启开发板…", protected=True)
        cmds.reset_chip(esp, "hard-reset")
        reset_done = True
        esp._port.close()
        esp = None
        events.phase("reconnect", "正在检查开发板恢复通信…", protected=False)
        ready = verify_restart(mac)
        message = ("固件已刷入并恢复通信。请在 iPad 蓝牙设置中连接 MouseLink-iPad。" if ready
                   else "固件写入与校验已完成，尚未检测到 MouseLink 通信。请重新插拔 USB；恢复其他固件后可能没有此通道。")
        events.emit({"event": "result", "ok": True, "ready": ready, "kind": kind,
                     "backup": str(backup), "message": message})
    finally:
        if esp is not None:
            try:
                if not reset_done:
                    cmds.reset_chip(esp, "hard-reset")
            except Exception:
                pass
            esp._port.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port")
    parser.add_argument("--serial", default="")
    parser.add_argument("--backup-root", type=Path)
    parser.add_argument("--restore", type=Path)
    parser.add_argument("--backup-only", action="store_true")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.backup_only and args.restore:
        parser.error("--backup-only cannot be combined with --restore")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    output = sys.stdout

    def emit(value):
        output.write(json.dumps(value, ensure_ascii=False) + "\n")
        output.flush()

    events = Events(emit)
    log.set_logger(events)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if args.self_check:
                manifest, _ = load_bundle(args.bundle)
                from esptool.loader import StubFlasher
                from esptool.targets.esp32c3 import ESP32C3ROM
                StubFlasher(ESP32C3ROM)
                emit({"event": "result", "ok": True, "version": APP_VERSION,
                      "esptool": esptool.__version__, "firmware": manifest["version"]})
            else:
                if not args.port or not args.backup_root:
                    raise ValueError("缺少设备或备份目录。")
                flash(args, events)
        return 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        if isinstance(exc, ValueError):
            message = str(exc)
        elif events.current == "connect":
            message = "无法进入刷机模式。请关闭占用串口的程序；按住 BOOT、轻按复位后松开 BOOT，再重试。"
        elif events.current == "backup":
            message = "备份未完成，尚未写入固件。请检查 USB 连接、磁盘空间及目录权限后重试。"
        else:
            message = "操作未完成。请保持 USB 连接后重试，或选择原板备份恢复；详情见日志。"
        emit({"event": "result", "ok": False, "message": message, "detail": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
