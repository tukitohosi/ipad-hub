"""Firmware UI and a hidden, independently packaged flashing process."""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets as W
from serial.tools.list_ports import comports

from firmware_bundle import load_bundle


class FlashJob(QtCore.QThread):
    event = QtCore.Signal(dict)

    def __init__(self, port, device_serial, data_root, restore=None, parent=None, backup_only=False):
        super().__init__(parent)
        self.port, self.device_serial = port, device_serial
        self.data_root, self.restore = Path(data_root), restore
        self.backup_only = backup_only

    def run(self):
        result_seen = False
        try:
            logs = self.data_root / "flash-logs"
            logs.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            if getattr(sys, "frozen", False):
                command = [str(Path(sys.executable).with_name("MouseLinkFlash.exe"))]
            else:
                command = [sys.executable, str(Path(__file__).with_name("flash_helper.py"))]
            command += ["--port", self.port, "--serial", self.device_serial,
                        "--backup-root", str(self.data_root / "firmware-backups")]
            if self.restore:
                command += ["--restore", str(self.restore)]
            if self.backup_only:
                command += ["--backup-only"]
            with (logs / f"{stamp}.stderr.log").open("wb") as errors, \
                    (logs / f"{stamp}.jsonl").open("w", encoding="utf-8") as history:
                with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=errors, text=True, encoding="utf-8", errors="replace",
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0) as process:
                    for line in process.stdout:
                        history.write(line)
                        history.flush()
                        try:
                            item = json.loads(line)
                            if not isinstance(item, dict):
                                continue
                        except ValueError:
                            continue
                        result_seen |= item.get("event") == "result"
                        self.event.emit(item)
                    code = process.wait()
                if not result_seen:
                    raise RuntimeError(f"刷机工具意外结束（{code}），请查看刷机日志。")
        except Exception as exc:
            self.event.emit({"event": "result", "ok": False, "message": str(exc)})


class FlashDialog(W.QDialog):
    def __init__(self, data_root, parent=None, current_firmware=""):
        super().__init__(parent)
        self.data_root = Path(data_root)
        self.job = None
        self.close_after = False
        self.result = None
        self.setWindowTitle("一键刷机 · MouseLink")
        # The owner disables its controls while this page owns USB. Keep the
        # owner's title-bar close action available, including during a write.
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.resize(640, 600)
        self.setMinimumSize(520, 480)
        outer = W.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = W.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        content = W.QWidget()
        root = W.QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        root.setContentsMargins(26, 24, 26, 24)
        root.setSpacing(16)
        title = W.QLabel("让开发板准备就绪")
        title.setObjectName("section")
        root.addWidget(title)
        info = W.QLabel("支持 ESP32-C3 SuperMini · 原生 USB · 4 MB\n刷机工具与固件已内置，无需下载或安装开发环境。")
        info.setObjectName("muted")
        info.setWordWrap(True)
        root.addWidget(info)
        row = W.QHBoxLayout()
        self.ports = W.QComboBox()
        self.ports.setMinimumWidth(260)
        row.addWidget(self.ports, 1)
        self.refresh_button = W.QPushButton("刷新设备")
        self.refresh_button.clicked.connect(self.refresh_ports)
        row.addWidget(self.refresh_button)
        root.addLayout(row)
        self.bundle_valid = False
        try:
            manifest, _ = load_bundle()
            bundle_text = "内置固件：精准边界定位 v3 · 横竖屏通用"
            self.bundle_valid = manifest["chip"] == "esp32c3"
        except Exception as exc:
            bundle_text = f"固件包不可用：{exc}"
        firmware = W.QLabel(bundle_text + (f"\n当前设备：{current_firmware}" if current_firmware else ""))
        firmware.setWordWrap(True)
        root.addWidget(firmware)
        notice = W.QLabel("开始后会先停止桥接、备份原固件，再刷写和校验。\n"
                          "已知兼容版本保留蓝牙配对；空白板或未知固件会完整初始化，覆盖原有内容。恢复备份也会覆盖当前内容。")
        notice.setWordWrap(True)
        notice.setObjectName("muted")
        root.addWidget(notice)
        self.status = W.QLabel("连接开发板后，点击“备份并刷入”。")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(46)
        root.addWidget(self.status)
        self.progress = W.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        self.details = W.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(600)
        self.details.setVisible(False)
        root.addWidget(self.details, 1)
        help_text = W.QLabel("无法进入刷机模式：按住 BOOT，轻按一次复位，再松开 BOOT 后重试。\n刷机过程中保持 USB 连接；若请求关闭，软件会等操作结束后退出。")
        help_text.setObjectName("muted")
        help_text.setWordWrap(True)
        root.addWidget(help_text)
        root.addStretch()
        links = W.QHBoxLayout()
        self.details_button = W.QPushButton("显示详细日志")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self.details.setVisible)
        links.addWidget(self.details_button)
        folder = W.QPushButton("打开备份与日志")
        folder.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.data_root))))
        links.addWidget(folder)
        links.addStretch()
        root.addLayout(links)
        buttons = W.QHBoxLayout()
        self.restore_button = W.QPushButton("恢复备份…")
        self.restore_button.clicked.connect(self.choose_restore)
        buttons.addWidget(self.restore_button)
        self.backup_button = W.QPushButton("仅备份固件")
        self.backup_button.clicked.connect(lambda: self.start_job(backup_only=True))
        buttons.addWidget(self.backup_button)
        buttons.addStretch()
        self.done_button = W.QPushButton("返回")
        self.done_button.clicked.connect(self.close)
        buttons.addWidget(self.done_button)
        self.flash_button = W.QPushButton("备份并刷入")
        self.flash_button.setObjectName("primary")
        self.flash_button.clicked.connect(lambda: self.start_job())
        buttons.addWidget(self.flash_button)
        root.addLayout(buttons)
        self.refresh_ports()

    def is_busy(self):
        return bool(self.job and self.job.isRunning())

    def refresh_ports(self):
        previous = self.ports.currentData()
        self.ports.clear()
        candidates = [p for p in comports() if p.vid == 0x303A and p.pid == 0x1001]
        if len(candidates) != 1:
            self.ports.addItem("请选择开发板" if candidates else "未检测到原生 USB 开发板", None)
        for port in candidates:
            data = (port.device, port.serial_number or "")
            self.ports.addItem(f"{port.device}  ·  {port.serial_number or 'ESP USB 设备'}", data)
            if previous == data:
                self.ports.setCurrentIndex(self.ports.count() - 1)
        self.flash_button.setEnabled(bool(candidates) and self.bundle_valid)
        self.restore_button.setEnabled(bool(candidates) and self.bundle_valid)
        self.backup_button.setEnabled(bool(candidates) and self.bundle_valid)

    def choose_restore(self):
        path, _ = W.QFileDialog.getOpenFileName(self, "选择这块开发板的备份记录",
            str(self.data_root / "firmware-backups"), "MouseLink 备份 (backup.json)")
        if path:
            self.start_job(path)

    def start_job(self, restore=None, backup_only=False):
        if self.is_busy() or not self.bundle_valid:
            return
        selected = self.ports.currentData()
        if not selected:
            self.status.setText("请先选择本次要刷写的开发板。")
            return
        self.result = None
        self.details.clear()
        self.progress.setRange(0, 0)
        self.status.setText("正在启动刷机工具…")
        for widget in (self.ports, self.refresh_button, self.flash_button, self.restore_button, self.backup_button):
            widget.setEnabled(False)
        self.done_button.setText("完成后返回")
        self.job = FlashJob(*selected, self.data_root, restore, self, backup_only=backup_only)
        self.job.event.connect(self.on_event)
        self.job.finished.connect(self.on_finished)
        self.job.start()

    def on_event(self, item):
        event = item.get("event")
        if event == "phase":
            self.progress.setRange(0, 0)
            self.status.setText(item["message"] + ("\n操作结束后将自动关闭。" if self.close_after else ""))
        elif event == "progress":
            self.progress.setRange(0, 100)
            self.progress.setValue(item["percent"])
        elif event == "backup":
            self.details.appendPlainText(f"原固件备份：{item['path']}")
        elif event == "result":
            self.result = item
            self.status.setText(item["message"])
            self.progress.setRange(0, 100)
            self.progress.setValue(100 if item.get("ok") else 0)
        if item.get("message"):
            self.details.appendPlainText(item["message"])

    def on_finished(self):
        self.done_button.setText("返回")
        self.ports.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.refresh_ports()
        if self.close_after:
            self.accept()

    def reject(self):
        self.close()

    def closeEvent(self, event):
        if self.is_busy():
            self.close_after = True
            self.status.setText("正在备份或刷写固件，操作结束后将自动关闭。请保持 USB 连接。")
            event.ignore()
        else:
            event.accept()
            self.accept()
