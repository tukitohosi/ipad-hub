"""Single-owner engine supervisor using local, versioned JSON pipes."""
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtNetwork import QLocalServer

from .model import Mode, State

MAX_FRAME = 256 * 1024


def resource_root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))


def bridge_modules():
    return resource_root() / ("bridge" if getattr(sys, "frozen", False) else "vendor/mouselink/open_bridge")


def engine_command(mode):
    if mode == Mode.DISPLAY:
        path = resource_root() / ("engines/iPadHubDisplay.exe" if getattr(sys, "frozen", False)
                                  else "build/ipadhub-display/Release/iPadHubDisplay.exe")
        return [str(path)]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--engine", "bridge"]
    return [sys.executable, "-m", "ipadhub.engines.bridge"]


class EngineProcess(QObject):
    message = Signal(dict)
    finished = Signal(bool, str)
    fault = Signal(str)

    def __init__(self, mode, storage, parent=None, command=None):
        super().__init__(parent)
        self.mode, self.storage = mode, storage
        self.session = uuid4().hex
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)
        self.pipe_name = "iPadHub.Engine." + self.session
        self.socket = None
        self.buffer = b""
        self.clean_release = False
        self.ready = False
        self._finished = False
        self.pending_messages = []
        self.process = QProcess(self)
        self.process.finished.connect(self._exited)
        self.process.errorOccurred.connect(self._process_error)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._log)
        self.command = command or engine_command(mode)
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self._handshake_timeout)

    def start(self):
        if not self.server.listen(self.pipe_name):
            self._finish(True, "无法建立本机引擎通信：" + self.server.errorString())
            return
        directory = self.storage.display_dir if self.mode == Mode.DISPLAY else self.storage.bridge_dir
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUTF8", "1")
        environment.insert("IPAD_CONNECT_CONFIG_DIR", str(self.storage.display_dir))
        environment.insert("MOUSELINK_DATA_DIR", str(self.storage.bridge_dir))
        self.process.setProcessEnvironment(environment)
        self.process.setWorkingDirectory(str(resource_root()))
        args = self.command[1:] + ["--pipe", "\\\\.\\pipe\\" + self.pipe_name,
                "--session", self.session, "--parent-pid", str(os.getpid()), "--data-dir", str(directory)]
        self.process.start(self.command[0], args)
        self.deadline.start(15000)

    def _accept(self):
        while self.server.hasPendingConnections():
            peer = self.server.nextPendingConnection()
            if self.socket is not None:
                peer.abort()
                peer.deleteLater()
                continue
            self.socket = peer
            peer.readyRead.connect(self._read)
            peer.disconnected.connect(self._disconnected)
            self._read()

    def _read(self):
        if self.socket is None:
            return
        self.buffer += bytes(self.socket.readAll())
        while b"\n" in self.buffer:
            raw, self.buffer = self.buffer.split(b"\n", 1)
            if len(raw) > MAX_FRAME:
                self._protocol_error("后台消息超过长度限制。")
                return
            try:
                item = json.loads(raw)
                if not isinstance(item, dict) or item.get("v") != 1 or item.get("session") != self.session:
                    raise ValueError("incorrect protocol/session")
                if item.get("type") not in ("event", "response"):
                    raise ValueError("incorrect message type")
                if not isinstance(item.get("payload"), dict):
                    raise ValueError("incorrect payload")
            except (ValueError, UnicodeDecodeError):
                self._protocol_error("后台通信内容无效，已要求它断开并回收资源。")
                return
            if item.get("event") == "hello":
                if item["payload"].get("engine") != self.mode.value:
                    self._protocol_error("后台类型与当前用途不一致。")
                    return
                self.ready = True
                self.deadline.stop()
                for pending in self.pending_messages:
                    self._write(pending)
                self.pending_messages.clear()
            elif item.get("event") == "released":
                self.clean_release = item.get("payload", {}).get("clean") is True
            self.message.emit(item)
        if len(self.buffer) > MAX_FRAME:
            self._protocol_error("后台通信未在长度限制内结束。")

    def send(self, command, payload=None):
        request = uuid4().hex
        item = dict(v=1, session=self.session, request=request, command=command, payload=payload or {})
        if command in ("stop", "shutdown") and not self.ready:
            self.pending_messages.clear()
        if self.ready:
            self._write(item)
        else:
            self.pending_messages.append(item)
        return request

    def _write(self, item):
        if self.socket is not None:
            self.socket.write((json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            self.socket.flush()

    def _log(self):
        data = bytes(self.process.readAllStandardOutput())
        if data:
            try:
                with (self.storage.logs_dir / f"{self.mode.value}-process.log").open("ab") as f:
                    f.write(data)
            except OSError:
                pass

    def _protocol_error(self, detail):
        self.fault.emit(detail)
        if self.socket is not None:
            self.socket.abort()  # Engines treat pipe loss as a full shutdown request.

    def _handshake_timeout(self):
        self.server.close()
        self._protocol_error("后台启动超时。停止完成前不能启动另一用途。")

    def _disconnected(self):
        if not self.clean_release and not self._finished:
            self.fault.emit("与后台的通信已中断，正在等待它回收资源。")

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            # No process existed, so it cannot own hardware resources.
            self._finish(True, "后台无法启动：" + self.process.errorString())

    def _exited(self, code, status):
        if self.socket is not None:
            self._read()
        self._log()
        clean = self.clean_release and code == 0 and status == QProcess.ExitStatus.NormalExit
        self._finish(clean, "" if clean else f"后台异常退出（{code}）。请检查资源状态后再恢复。")

    def _finish(self, clean, detail):
        if self._finished:
            return
        self._finished = True
        self.deadline.stop()
        self.server.close()
        self.finished.emit(clean, detail)


class RuntimeController(QObject):
    changed = Signal()
    devices = Signal(object, list)
    event = Signal(object, str, dict)
    stopped = Signal()

    def __init__(self, storage, parent=None, factory=EngineProcess):
        super().__init__(parent)
        self.storage, self.factory = storage, factory
        self.engine = None
        self.owner = None
        self.selected = Mode.DISPLAY
        self.pending = None
        self.state = State.READY
        self.detail = "选择用途后点击刷新设备，或开始连接。"
        self.snapshot = {}
        self.busy_tool = False
        self.release_blocked = False
        self.quitting = False
        self.stop_timer = QTimer(self)
        self.stop_timer.setSingleShot(True)
        self.stop_timer.timeout.connect(self._stop_timeout)
        self._requests = {}

    def request(self, mode, command, payload=None):
        if self.busy_tool or self.release_blocked or self.quitting:
            return False
        if self.state == State.STOPPING:
            return False
        self.selected = mode
        action = (mode, command, payload or {})
        if self.engine is not None and self.owner != mode:
            self.pending = action
            self._begin_stop()
        elif self.engine is None:
            self._launch(action)
        else:
            self._send(command, payload)
        return True

    def _launch(self, action):
        mode, command, payload = action
        self.owner, self.selected = mode, mode
        self.state = State.CONNECTING
        self.detail = "正在启动后台并检查连接条件…"
        engine = self.factory(mode, self.storage, self)
        self.engine = engine
        engine.message.connect(lambda item, source=engine: self._message(source, item))
        engine.fault.connect(lambda text, source=engine: self._fault(source, text))
        engine.finished.connect(lambda clean, detail, source=engine: self._finished(source, clean, detail))
        # Queue commands before start: failed-to-start can synchronously finish.
        self._send(command, payload)
        engine.start()
        self.changed.emit()

    def _send(self, command, payload=None):
        if not self.engine:
            return
        request = self.engine.send(command, payload)
        self._requests[request] = command
        if command in ("start", "discover"):
            self.state = State.CONNECTING
            self.detail = "正在连接，请保持 iPad 接收端在前台。" if self.owner == Mode.DISPLAY else "正在确认开发板和蓝牙就绪。"
        self.changed.emit()

    def stop(self):
        if self.busy_tool:
            return
        self.pending = None
        if self.engine is None:
            if not self.release_blocked:
                self.state = State.READY
                self.stopped.emit()
        else:
            self._begin_stop()
        self.changed.emit()

    def _begin_stop(self):
        self.state = State.STOPPING
        self.detail = "正在释放输入、连接和当前后台资源…"
        self.engine.send("shutdown")
        self.stop_timer.start(35000)
        self.changed.emit()

    def _message(self, source, item):
        if source is not self.engine:
            return
        payload = item.get("payload", {})
        event = item.get("event", "")
        if item.get("type") == "response":
            command = self._requests.pop(item.get("request"), "")
            if not item.get("ok", False) and self.state != State.STOPPING:
                self.state = State.FAILED
                self.detail = item.get("error") or payload.get("detail") or "后台未能完成操作。"
            self.event.emit(self.owner, "response:" + command, item)
        elif event == "devices":
            self.devices.emit(self.owner, payload.get("devices", []))
        elif event == "status":
            self.snapshot = payload
            if self.state != State.STOPPING and not self.release_blocked:
                self.state = {"idle": State.READY, "ready": State.READY, "discovering": State.CONNECTING,
                              "connecting": State.CONNECTING, "active": State.ACTIVE, "stopping": State.STOPPING,
                              "failed": State.FAILED, "missing": State.MISSING}.get(payload.get("state"), State.FAILED)
                self.detail = payload.get("detail") or self.state.value
        self.event.emit(self.owner, event, payload)
        self.changed.emit()

    def _fault(self, source, detail):
        if source is not self.engine:
            return
        self.release_blocked = True
        self.pending = None
        self.state = State.FAILED
        self.detail = detail
        self.changed.emit()

    def _stop_timeout(self):
        self.release_blocked = True
        self.pending = None
        self.state = State.FAILED
        self.detail = "资源释放未在预期时间内完成。已禁止切换，请重试停止；不要同时开启旧程序。"
        self.changed.emit()

    def _finished(self, source, clean, detail):
        if source is not self.engine:
            return
        self.stop_timer.stop()
        self.engine = None
        self._requests.clear()
        self.snapshot = {}
        self.owner = None
        source.deleteLater()
        action, self.pending = self.pending, None
        if not clean:
            self.release_blocked = True
            self.state = State.FAILED
            self.detail = detail
        else:
            self.release_blocked = False
            self.state = State.FAILED if detail else State.READY
            self.detail = detail or "已停止，资源已释放。"
            if action is not None and not self.quitting and not detail:
                self._launch(action)
            else:
                self.stopped.emit()
        self.changed.emit()

    def recover(self):
        if self.engine is not None:
            self.pending = None
            self._begin_stop()
            return True
        return False

    def quit(self):
        if self.busy_tool:
            return False
        self.quitting = True
        self.pending = None
        if self.engine is None:
            self.stopped.emit()
        else:
            self._begin_stop()
        return True
