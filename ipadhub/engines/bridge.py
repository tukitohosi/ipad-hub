"""iPadHub's headless MouseLink engine. No legacy Window or tray is created."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid

from PySide6 import QtCore

from .pipe_client import BridgeResourceLock, NamedPipeClient, ProtocolError


def vendor_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "bridge"
    return Path(__file__).resolve().parents[2] / "vendor" / "mouselink" / "open_bridge"


def load_worker_support():
    # The same modules are explicitly included in the frozen application.
    directory = str(vendor_directory())
    if directory not in sys.path:
        sys.path.insert(0, directory)
    from desktop_app import Worker
    from serial.tools.list_ports import comports
    return Worker, comports


def read_settings(path: Path) -> tuple[dict, str]:
    value = {}
    warning = ""
    if path.exists():
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(value, dict):
                raise ValueError("settings must be an object")
        except (OSError, ValueError) as exc:
            warning = f"设置无法读取，使用默认值：{exc}"
            # Only the new application's data is touched; retain the bad input.
            try:
                path.replace(path.with_name(f"settings.corrupt-{time.time_ns()}.json"))
            except OSError:
                pass
            value = {}
    mode = value.get("mode", "free")
    value["hotkey_return_enabled"] = (True if mode == "mixed" else False if mode == "edge"
                                       else value.get("hotkey_return_enabled") is True)
    value["mode"] = "locked" if mode == "locked" else "free"
    value["ipad_side"] = "left" if value.get("ipad_side") == "left" else "right"
    try:
        value["speed"] = max(25, min(150, int(value.get("speed", 50))))
    except (TypeError, ValueError, OverflowError):
        value["speed"] = 50
    if not isinstance(value.get("calibration"), dict):
        value["calibration"] = {}
    return value, warning


class BridgeEngine(QtCore.QObject):
    """Small request/state adapter; all hardware behavior remains in Worker."""
    def __init__(self, session, data_dir, send, quit_callback, worker_factory,
                 ports_factory, lock_factory=BridgeResourceLock,
                 clock=time.monotonic, start_timeout=20.0, stop_timeout=15.0):
        super().__init__()
        self.session = session
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings_path = self.data_dir / "settings.json"
        self.settings, self.settings_warning = read_settings(self.settings_path)
        self.send = send
        self.quit_callback = quit_callback
        self.worker_factory = worker_factory
        self.ports_factory = ports_factory
        self.lock_factory = lock_factory
        self.clock = clock
        self.start_timeout = start_timeout
        self.stop_timeout = stop_timeout
        self.worker = None
        self.resource_lock = None
        self.pending_start = None
        self.start_submitted = False
        self.start_deadline = 0.0
        self.start_port = ""
        self.failure = ""
        self.stopping = False
        self.stop_failed = False
        self.stop_requests = []
        self.stop_deadline = 0.0
        self.released = False
        self.transport_lost = False
        self.calibration_pending = None
        self.last_calibration = None
        self.completed_requests = {}
        self.inflight = set()
        self.last_status = None
        self.last_status_at = -1.0
        self.last_devices = None
        self.last_devices_at = -1.0

    def event(self, name, payload):
        self._send({"v": 1, "session": self.session, "request": "", "type": "event",
                    "event": name, "payload": payload})

    def _send(self, message):
        if self.transport_lost:
            return
        try:
            self.send(message)
        except (OSError, ValueError) as exc:
            print(f"[IPC] {exc}")
            self.disconnected(str(exc))

    def response(self, request, ok=True, payload=None, error=""):
        self.inflight.discard(request)
        message = {"v": 1, "session": self.session, "request": request,
                   "type": "response", "ok": ok, "payload": payload or {}, "error": error}
        self.completed_requests[request] = message
        if len(self.completed_requests) > 256:
            self.completed_requests.pop(next(iter(self.completed_requests)))
        self._send(message)

    def hello(self):
        self.event("hello", {"engine": "bridge", "version": "0.1.0"})
        self.publish_status(force=True)

    def _ensure_worker(self):
        if self.worker is not None:
            return
        self.resource_lock = self.lock_factory()
        try:
            self.worker = self.worker_factory(native_edges=True, trial_seconds=0)
            self.worker.finished.connect(self._worker_finished)
            self.worker.start_handled.connect(self._start_handled)
            self.worker.calibration_done.connect(self._calibration_done)
            self.worker.start()
        except Exception:
            self.worker = None
            self.resource_lock.close()
            self.resource_lock = None
            raise

    def devices(self):
        return [{"id": f"bridge:{port.serial_number or port.device}",
                 "name": str(port.description or "ESP32-C3"), "port": str(port.device),
                 "serial": str(port.serial_number or ""), "vid": port.vid, "pid": port.pid}
                for port in self.ports_factory()
                if port.vid == 0x303A and port.pid == 0x1001]

    def public_settings(self):
        return {"mode": self.settings["mode"], "speed": self.settings["speed"] / 100,
                "ipad_side": self.settings["ipad_side"],
                "hotkey_return_enabled": self.settings["hotkey_return_enabled"],
                "calibration": self.settings["calibration"]}

    def _save(self, settings):
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2, allow_nan=False),
                             encoding="utf-8")
        temporary.replace(self.settings_path)
        self.settings = settings

    def configure(self, payload):
        if self.pending_start or (self.worker and self.worker.busy):
            raise ValueError("操作正在进行，请完成或取消后再修改设置。")
        values = self.settings.copy()
        if "mode" in payload:
            if payload["mode"] not in ("free", "locked"):
                raise ValueError("模式必须是 free 或 locked。")
            values["mode"] = payload["mode"]
        if "ipad_side" in payload:
            if payload["ipad_side"] not in ("left", "right"):
                raise ValueError("iPad 位置必须是 left 或 right。")
            values["ipad_side"] = payload["ipad_side"]
        if "speed" in payload:
            speed = payload["speed"]
            if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed):
                raise ValueError("鼠标速度必须为有效数字。")
            if not .25 <= speed <= 1.5:
                raise ValueError("鼠标速度应在 0.25 至 1.5 之间。")
            values["speed"] = round(speed * 100)
        if "hotkey_return_enabled" in payload:
            if not isinstance(payload["hotkey_return_enabled"], bool):
                raise ValueError("紧急返回选项必须是布尔值。")
            values["hotkey_return_enabled"] = payload["hotkey_return_enabled"]
        if values != self.settings or not self.settings_path.exists():
            self._save(values)
            if self.worker and self.worker.running_bridge:
                self.worker.request_side_change(*self._start_values())
        return self.public_settings()

    def _start_values(self):
        mode = ("locked" if self.settings["mode"] == "locked" else
                "mixed" if self.settings["hotkey_return_enabled"] else "edge")
        return mode, self.settings["speed"] / 100, self.settings["ipad_side"]

    def handle(self, message):
        if message.get("session") != self.session:
            return  # Never execute a delayed request from a different session.
        request = message.get("request")
        if (message.get("v") != 1 or not isinstance(request, str) or not request
                or len(request) > 128 or not isinstance(message.get("command"), str)
                or not isinstance(message.get("payload", {}), dict)):
            self.disconnected("无效的后台控制消息。")
            return
        if request in self.completed_requests:
            self._send(self.completed_requests[request])
            return
        if request in self.inflight:
            return
        self.inflight.add(request)
        command, payload = message["command"], message.get("payload", {})
        if self.stopping and command not in ("status", "stop", "shutdown"):
            self.response(request, False, error="正在释放键鼠资源，请等待后台退出。")
            return
        try:
            if command == "status":
                self.response(request, payload=self.snapshot())
            elif command == "discover":
                self.failure = ""
                self._ensure_worker()
                devices = self.devices()
                self.event("devices", {"devices": devices})
                self.response(request, payload={"devices": devices, "settings": self.public_settings()})
            elif command == "configure":
                self.response(request, payload={"settings": self.configure(payload)})
            elif command == "start":
                if self.pending_start or (self.worker and (self.worker.busy or self.worker.running_bridge)):
                    raise ValueError("键鼠功能已经启动或正在启动。")
                port = payload.get("port", "")
                if not isinstance(port, str) or len(port) > 128:
                    raise ValueError("无效的开发板端口。")
                self.configure(payload)
                self.failure = ""
                self._ensure_worker()
                self.start_port = port
                self.pending_start = request
                self.start_submitted = False
                self.start_deadline = self.clock() + self.start_timeout
                self.publish_status(force=True)
            elif command in ("stop", "shutdown"):
                self.stop_requests.append(request)
                self.shutdown()
            elif command == "calibrate":
                self._calibrate(request, payload)
            else:
                raise ValueError(f"不支持的后台命令：{command}")
        except (OSError, ValueError, RuntimeError) as exc:
            self.failure = str(exc)
            self.response(request, False, error=self.failure)
            self.publish_status(force=True)

    def _calibrate(self, request, payload):
        orientation = payload.get("orientation")
        if orientation not in ("portrait", "landscape"):
            raise ValueError("检查方向必须是 portrait 或 landscape。")
        if "confirmed" in payload:
            if not isinstance(payload["confirmed"], bool):
                raise ValueError("检查确认必须是布尔值。")
            if (not self.last_calibration or self.last_calibration[0] != orientation or
                    not self.worker or self.last_calibration[1] != self.worker.device_serial):
                raise ValueError("请先完成当前设备的光标检查。")
            profile = self.settings["calibration"].copy()
            if profile.get("device_serial") != self.worker.device_serial:
                profile = {"device_serial": self.worker.device_serial}
            profile[orientation] = payload["confirmed"]
            self._save(dict(self.settings, calibration=profile))
            self.last_calibration = None
            self.response(request, payload={"settings": self.public_settings()})
            return
        if self.pending_start or self.calibration_pending or (self.worker and self.worker.running_bridge):
            raise ValueError("请停止键鼠控制后再检查光标位置。")
        # Calibrate against the controls the user can currently see, including
        # unsaved placement changes. A save/validation failure must not move HID.
        self.configure(payload)
        self._ensure_worker()
        snapshot = self.worker.snapshot()
        if not (snapshot.get("usb") and snapshot.get("ble") and snapshot.get("ready")):
            raise ValueError("请连接开发板并完成 iPad 蓝牙配对后再检查。")
        self.last_calibration = None
        self.calibration_pending = (request, orientation)
        if not self.worker.request_calibration(orientation, self.settings["ipad_side"]):
            self.calibration_pending = None
            raise ValueError("设备状态已变化，请连接后重新检查。")
        self.response(request, payload={"accepted": True, "orientation": orientation})

    @QtCore.Slot(str, bool, str)
    def _calibration_done(self, orientation, success, detail):
        if self.calibration_pending is None:
            return
        self.calibration_pending = None
        if success and not self.stopping:
            self.last_calibration = (orientation, self.worker.device_serial)
        self.event("calibration", {"orientation": orientation, "success": bool(success),
                                   "detail": detail, "confirmation_required": bool(success),
                                   "device_serial": self.worker.device_serial})

    @QtCore.Slot(object)
    def _start_handled(self, token):
        if (self.pending_start == token and self.start_submitted and self.worker
                and not self.worker.running_bridge):
            self._fail_start(self.worker.error or "键鼠启动未完成，请检查开发板和蓝牙连接。")

    def _fail_start(self, detail):
        request = self.pending_start
        self.pending_start = None
        self.start_submitted = False
        self.failure = detail
        if self.worker:
            self.worker.stop_bridge()
        if request:
            self.response(request, False, error=detail)

    def snapshot(self):
        worker = self.worker
        values = worker.snapshot() if worker else {}
        running = bool(worker and worker.running_bridge)
        ready = all(values.get(key) for key in ("usb", "ble", "ready"))
        if self.stopping:
            state = "failed" if self.stop_failed else "stopping"
            detail = ("键鼠资源尚未释放，已禁止切换；请重试停止或退出。" if self.stop_failed else
                      "正在释放按键、输入监听、鼠标约束和串口。")
        elif self.pending_start:
            state, detail = "connecting", "正在等待开发板与 iPad 蓝牙定位通道就绪。"
        elif worker and worker.busy:
            state, detail = "connecting", worker.phase or "正在检查光标位置。"
        elif running:
            state = "active" if ready else "connecting"
            detail = ("正在控制 iPad；按 Ctrl + Alt + Esc 可紧急返回。" if values.get("remote") else
                      "键鼠桥已开启；将鼠标移至 iPad 一侧的电脑边缘。") if ready else "连接已变化，正在等待设备重新就绪。"
        elif self.failure:
            state, detail = "failed", self.failure
        elif ready:
            state, detail = "ready", "开发板和 iPad 已就绪，可以开启键鼠控制。"
        elif worker and worker.error:
            state, detail = "failed", worker.error
        elif worker and values.get("usb"):
            state, detail = "missing", "请在 iPad 蓝牙设置中连接开发板，并确认定位固件可用。"
        elif worker:
            state, detail = "missing", "请使用 USB 连接 ESP32-C3 开发板。"
        else:
            state, detail = "idle", "请选择键鼠模式以检查开发板。"
        return {**values, "state": state, "detail": detail, "running": running,
                "usb": bool(values.get("usb")), "ble": bool(values.get("ble")),
                "ready": bool(values.get("ready")), "remote": bool(values.get("remote")),
                "port": worker.port if worker else "", "busy": bool(worker and worker.busy),
                "device_serial": worker.device_serial if worker else "",
                "released": self.released, "settings": self.public_settings(),
                "settings_warning": self.settings_warning}

    def publish_status(self, force=False):
        status = self.snapshot()
        now = self.clock()
        if force or status != self.last_status or now - self.last_status_at >= 2:
            self.last_status, self.last_status_at = status, now
            self.event("status", status)

    def tick(self):
        if self.released:
            return
        now = self.clock()
        if self.pending_start and not self.stopping:
            status = self.worker.snapshot()
            ready = all(status.get(key) for key in ("usb", "ble", "ready"))
            if self.start_port and self.worker.port and self.start_port != self.worker.port:
                self._fail_start("开发板端口已变化，请重新选择设备。")
            elif self.start_submitted and self.worker.running_bridge and ready:
                request = self.pending_start
                self.pending_start = None
                self.start_submitted = False
                self.response(request, payload=self.snapshot())
            elif now >= self.start_deadline:
                self._fail_start(self.worker.error or "等待设备就绪超时；请检查 USB、iPad 蓝牙与固件。")
            elif ready and not self.start_submitted:
                self.start_submitted = True
                if not self.worker.request_start(*self._start_values(), self.pending_start):
                    self._fail_start(self.worker.error or "设备连接已变化，请重试。")
        if self.stopping:
            if self.worker is None or self.worker.isFinished():
                self._finish_shutdown()
                return
            if now >= self.stop_deadline and not self.stop_failed:
                self.stop_failed = True
                for request in self.stop_requests:
                    self.response(request, False, payload={"released": False},
                                  error="键鼠后台仍在停止，尚不能切换模式。")
                self.stop_requests.clear()
        elif now - self.last_devices_at >= 2:
            self.last_devices_at = now
            try:
                devices = self.devices()
                if devices != self.last_devices:
                    self.last_devices = devices
                    self.event("devices", {"devices": devices})
            except OSError as exc:
                self.failure = str(exc)
        self.publish_status()

    def shutdown(self):
        if self.released:
            return
        first = not self.stopping
        self.stopping = True
        self.stop_failed = False
        self.stop_deadline = self.clock() + self.stop_timeout
        if first and self.pending_start:
            self._fail_start("启动已取消。")
        self.publish_status(force=True)
        if self.worker is not None:
            self.worker.request_shutdown()
            if self.worker.isFinished():
                self._finish_shutdown()
        else:
            self._finish_shutdown()

    def disconnected(self, detail="主界面连接已断开。"):
        if self.transport_lost:
            return
        self.transport_lost = True
        print(f"[IPC] {detail}; stopping bridge")
        self.shutdown()

    @QtCore.Slot()
    def _worker_finished(self):
        if self.stopping:
            self._finish_shutdown()
        else:
            # Unexpected Worker termination must not look like a usable session.
            self.failure = "键鼠后台异常结束，请重新进入键鼠模式。"
            if self.pending_start:
                self._fail_start(self.failure)
            self.shutdown()

    def _finish_shutdown(self):
        if self.released:
            return
        # finished is emitted just before QThread fully exits; confirm its join.
        if self.worker and not self.worker.wait(100):
            return
        self.released = True
        if self.resource_lock:
            self.resource_lock.close()
            self.resource_lock = None
        for request in self.stop_requests:
            self.response(request, payload={"released": True})
        self.stop_requests.clear()
        self.event("released", {"engine": "bridge", "released": True, "clean": True})
        self.quit_callback()


def run(argv=None):
    parser = argparse.ArgumentParser(description="iPadHub headless keyboard/mouse engine")
    parser.add_argument("--pipe", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        uuid.UUID(args.session)
    except ValueError:
        parser.error("--session must be a UUID")
    data_dir = args.data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MOUSELINK_DATA_DIR"] = str(data_dir)
    # The original core uses print; keep every diagnostic out of the control pipe.
    log_file = data_dir / "bridge.log"
    if log_file.exists() and log_file.stat().st_size > 2_000_000:
        log_file.replace(data_dir / "bridge.previous.log")
    previous_stdout, previous_stderr = sys.stdout, sys.stderr
    log = log_file.open("a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = log
    transport = None
    engine = None
    try:
        transport = NamedPipeClient(args.pipe, args.parent_pid)
        transport.connect()
        worker_factory, ports_factory = load_worker_support()
        app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication(sys.argv[:1])
        app.setApplicationName("iPadHub Bridge Engine")
        quitting = False
        def finish():
            nonlocal quitting
            quitting = True
            # A small drain grace period, bounded even if the host is gone.
            QtCore.QTimer.singleShot(120, app.quit)
        engine = BridgeEngine(args.session, data_dir, transport.send, finish,
                              worker_factory, ports_factory)
        timer = QtCore.QTimer()
        def poll():
            try:
                for message in transport.poll():
                    if not quitting:
                        engine.handle(message)
            except (OSError, ValueError) as exc:
                engine.disconnected(str(exc))
            engine.tick()
        timer.timeout.connect(poll)
        timer.start(40)
        engine.hello()
        result = app.exec()
        timer.stop()
        return result
    except Exception as exc:
        print(f"[FATAL] {type(exc).__name__}: {exc}")
        return 1
    finally:
        if engine and engine.worker and not engine.worker.isFinished():
            engine.worker.request_shutdown()
            # Keep the process and resource lock alive until genuine cleanup.
            engine.worker.wait()
        if engine and engine.resource_lock:
            engine.resource_lock.close()
        if transport:
            transport.close()
        sys.stdout, sys.stderr = previous_stdout, previous_stderr
        log.close()


if __name__ == "__main__":
    raise SystemExit(run())
