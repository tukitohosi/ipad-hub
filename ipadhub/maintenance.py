"""Explicit, asynchronous elevation for the display resolution helper."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal


def _launch_elevated(executable, width, height):
    """May wait for UAC consent; only called on the short-lived launch thread."""
    if os.name != "nt":
        raise OSError("此工具仅支持 Windows。")

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", wintypes.ULONG),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]

    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    initialized = ole.CoInitializeEx(None, 2) in (0, 1)  # STA shell extensions.
    try:
        info = ShellExecuteInfo()
        info.cbSize = ctypes.sizeof(info)
        # Keep the process handle, finish launch on this worker, and let the app
        # present launch errors. This flag does not suppress the UAC consent UI.
        info.fMask = 0x40 | 0x100 | 0x400
        info.lpVerb = "runas"
        info.lpFile = str(executable)
        info.lpParameters = f"--register-resolution {width} {height}"
        info.lpDirectory = str(Path(executable).parent)
        info.nShow = 0
        if not shell.ShellExecuteExW(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not info.hProcess:
            raise OSError("分辨率助手未返回进程句柄，无法确认操作结果。")
        return int(info.hProcess)
    finally:
        if initialized:
            ole.CoUninitialize()


def _poll_process(handle):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    status = kernel.WaitForSingleObject(handle, 0)
    if status == 258:  # WAIT_TIMEOUT: never block the Qt UI.
        return None
    if status != 0:
        raise ctypes.WinError(ctypes.get_last_error())
    code = wintypes.DWORD()
    if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
        raise ctypes.WinError(ctypes.get_last_error())
    return code.value


def _close_process(handle):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CloseHandle(handle)


class _LaunchThread(QThread):
    result = Signal(object, object)

    def __init__(self, executable, width, height, parent):
        super().__init__(parent)
        self.arguments = executable, width, height

    def run(self):
        try:
            self.result.emit(_launch_elevated(*self.arguments), None)
        except Exception as error:
            self.result.emit(None, error)


class ResolutionTask(QObject):
    """Call start only from a user's explicit registration action.

    The caller must retain this object and keep mode-switch/exit blocked until
    finished. A slow helper remains busy; it is never terminated by this class.
    """
    finished = Signal(bool, str)
    progress = Signal(str)
    WAIT_NOTICE_SECONDS = 30

    def __init__(self, executable, width, height, parent=None):
        super().__init__(parent)
        self.executable = Path(executable)
        self.width, self.height = width, height
        self.busy = False
        self._started = False
        self._done = False
        self._handle = None
        self._worker = None
        self._wait_notified = False
        self._poll_error_notified = False
        self._started_at = 0
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll)

    def start(self):
        if self._started:
            return
        self._started = True
        axes = (self.width, self.height)
        if any(type(axis) is not int or not 320 <= axis <= 8192 or axis % 2 for axis in axes):
            self._finish(False, "分辨率的宽和高必须为 320 到 8192 之间的偶数。")
            return
        if self.width * self.height > 8192 * 4096:
            self._finish(False, "该分辨率超过副屏引擎支持的总像素范围。")
            return
        if not self.executable.is_absolute() or not self.executable.is_file():
            self._finish(False, "未找到 iPadHub 副屏助手，请重新安装或检查应用文件。")
            return
        self.busy = True
        self._started_at = time.monotonic()
        self.progress.emit("请在 Windows 权限提示中确认注册 iPad 分辨率。")
        self._worker = _LaunchThread(self.executable, self.width, self.height, self)
        self._worker.result.connect(self._launched)
        self._worker.finished.connect(self._launch_thread_finished)
        self._worker.start()
        self.timer.start()

    def _launched(self, handle, error):
        self._handle = handle
        self._launch_error = error
        # QThread.result can reach the UI just before QThread.run returns.
        # Delay completion until the thread is finished so callers may safely
        # delete the task immediately after the finished signal.

    def _launch_thread_finished(self):
        error = getattr(self, "_launch_error", None)
        if error is not None:
            cancelled = getattr(error, "winerror", None) == 1223
            self._finish(False, "已取消分辨率注册。" if cancelled else f"无法启动分辨率助手：{error}")
        elif self._handle is None:
            self._finish(False, "分辨率助手未返回可等待的进程。")
        else:
            self.progress.emit("正在注册 iPad 原生分辨率，请稍候。")
            self._poll()

    def _poll(self):
        if self._done or not self.busy:
            return
        if not self._wait_notified and time.monotonic() - self._started_at >= self.WAIT_NOTICE_SECONDS:
            self._wait_notified = True
            self.progress.emit("仍在等待 Windows 权限确认或分辨率注册完成；完成前将保持设备工具占用。")
        if self._handle is None or (self._worker is not None and self._worker.isRunning()):
            return
        try:
            code = _poll_process(self._handle)
        except OSError:
            # An uncertain process state is not permission to start a competing
            # mode. Keep the handle and continue polling; do not kill the helper.
            if not self._poll_error_notified:
                self._poll_error_notified = True
                self.progress.emit("暂时无法确认分辨率助手状态，仍在等待；请勿同时启动旧副屏软件。")
            return
        if code is None:
            return
        _close_process(self._handle)
        self._handle = None
        if code == 0:
            self._finish(True, "iPad 原生分辨率已注册，可以重新连接副屏。")
        else:
            detail = {1: "分辨率注册失败，请检查权限及虚拟显示驱动。",
                      2: "分辨率参数不受副屏引擎支持。",
                      9: "副屏仍被旧版 iPad互联或其他后台占用，请退出对应程序后重试。"}
            self._finish(False, detail.get(code, f"分辨率助手未完成操作（退出代码 {code}）。"))

    def _finish(self, success, detail):
        if self._done:
            return
        self._done = True
        self.busy = False
        self.timer.stop()
        self.finished.emit(success, detail)
