"""Bounded JSON-lines transport over a local Windows byte-mode named pipe.

No stdout protocol and no worker threads: poll() uses PIPE_NOWAIT and is safe to
call from the Qt timer. A stalled/disconnected host cannot block device cleanup.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import time


MAX_FRAME_BYTES = 256 * 1024


class ProtocolError(ValueError):
    pass


class JsonLines:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data: bytes) -> list[dict]:
        self.buffer.extend(data)
        result = []
        while b"\n" in self.buffer:
            line, _, tail = self.buffer.partition(b"\n")
            self.buffer = bytearray(tail)
            if not line or len(line) > MAX_FRAME_BYTES:
                raise ProtocolError("Invalid JSON frame length")
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeError, ValueError) as exc:
                raise ProtocolError("Invalid UTF-8 JSON frame") from exc
            if not isinstance(message, dict):
                raise ProtocolError("JSON frame must be an object")
            result.append(message)
        if len(self.buffer) > MAX_FRAME_BYTES:
            raise ProtocolError("JSON frame exceeds 256 KiB")
        return result


def encode_message(message: dict) -> bytes:
    frame = json.dumps(message, ensure_ascii=False, separators=(",", ":"),
                       allow_nan=False).encode("utf-8")
    if len(frame) > MAX_FRAME_BYTES:
        raise ProtocolError("JSON frame exceeds 256 KiB")
    return frame + b"\n"


def kernel_api():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p, ctypes.c_void_p]
    kernel.SetNamedPipeHandleState.restype = wintypes.BOOL
    kernel.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.PeekNamedPipe.restype = wintypes.BOOL
    kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.WriteFile.argtypes = kernel.ReadFile.argtypes
    kernel.WriteFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetNamedPipeServerProcessId.argtypes = [wintypes.HANDLE,
                                                 ctypes.POINTER(wintypes.ULONG)]
    kernel.GetNamedPipeServerProcessId.restype = wintypes.BOOL
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return kernel


class NamedPipeClient:
    def __init__(self, pipe: str, parent_pid: int):
        prefix = "\\\\.\\pipe\\"
        if not pipe.startswith(prefix) or not pipe[len(prefix):] or "\\" in pipe[len(prefix):]:
            raise ValueError("Only a local Windows named pipe is permitted")
        if len(pipe) > 240 or parent_pid <= 0:
            raise ValueError("Invalid pipe name or parent process")
        self.pipe = pipe
        self.parent_pid = parent_pid
        self.kernel = kernel_api()
        self.handle = None
        self.parent_handle = self.kernel.OpenProcess(0x00100000, False, parent_pid)
        if not self.parent_handle:
            raise OSError(ctypes.get_last_error(), "Cannot watch host process")
        self.decoder = JsonLines()
        self.pending = bytearray()
        self.pending_since = None

    def parent_alive(self) -> bool:
        return bool(self.parent_handle and
                    self.kernel.WaitForSingleObject(self.parent_handle, 0) == 258)

    def connect(self, timeout: float = 8.0):
        deadline = time.monotonic() + timeout
        invalid = ctypes.c_void_p(-1).value
        while self.parent_alive() and time.monotonic() < deadline:
            handle = self.kernel.CreateFileW(self.pipe, 0xC0000000, 0, None, 3, 0, None)
            if handle not in (None, invalid):
                self.handle = handle
                server_pid = wintypes.ULONG()
                if (not self.kernel.GetNamedPipeServerProcessId(handle, ctypes.byref(server_pid))
                        or server_pid.value != self.parent_pid):
                    self.close()
                    raise OSError("Named-pipe server is not the expected host process")
                mode = wintypes.DWORD(1)  # PIPE_READMODE_BYTE | PIPE_NOWAIT
                if not self.kernel.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
                    error = ctypes.get_last_error()
                    self.close()
                    raise OSError(error, "Cannot make control pipe nonblocking")
                return
            error = ctypes.get_last_error()
            if error not in (2, 231):  # file not found / all pipe instances busy
                raise OSError(error, "Cannot connect to host pipe")
            time.sleep(.04)
        raise TimeoutError("Host control pipe did not become available")

    def send(self, message: dict):
        if not self.handle:
            raise BrokenPipeError("Control pipe is closed")
        frame = encode_message(message)
        if len(self.pending) + len(frame) > 4 * MAX_FRAME_BYTES:
            raise BrokenPipeError("Host is not consuming engine events")
        if not self.pending:
            self.pending_since = time.monotonic()
        self.pending.extend(frame)
        self.flush()

    def flush(self):
        if not self.handle:
            raise BrokenPipeError("Control pipe is closed")
        if not self.pending:
            return
        count = wintypes.DWORD()
        raw = bytes(self.pending[:65536])
        if not self.kernel.WriteFile(self.handle, raw, len(raw), ctypes.byref(count), None):
            error = ctypes.get_last_error()
            if error != 232:  # ERROR_NO_DATA: nonblocking pipe temporarily full
                raise BrokenPipeError(error, "Cannot write to host pipe")
        if count.value:
            del self.pending[:count.value]
            self.pending_since = time.monotonic() if self.pending else None
        if self.pending_since is not None and time.monotonic() - self.pending_since > 5:
            raise BrokenPipeError("Host stopped reading engine events")

    def poll(self) -> list[dict]:
        if not self.parent_alive():
            raise BrokenPipeError("Host process exited")
        self.flush()
        result = []
        # Bound work per timer tick, so incoming traffic never starves shutdown.
        for _ in range(8):
            available = wintypes.DWORD()
            if not self.kernel.PeekNamedPipe(self.handle, None, 0, None,
                                             ctypes.byref(available), None):
                raise BrokenPipeError(ctypes.get_last_error(), "Host control pipe closed")
            if not available.value:
                break
            size = min(available.value, 65536)
            buffer = ctypes.create_string_buffer(size)
            count = wintypes.DWORD()
            if not self.kernel.ReadFile(self.handle, buffer, size, ctypes.byref(count), None):
                raise BrokenPipeError(ctypes.get_last_error(), "Cannot read host pipe")
            if not count.value:
                break
            result.extend(self.decoder.feed(buffer.raw[:count.value]))
        return result

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        if self.parent_handle:
            self.kernel.CloseHandle(self.parent_handle)
            self.parent_handle = None


class BridgeResourceLock:
    """Shares the original MouseLink resource identity; never stops its process."""
    def __init__(self):
        self.kernel = kernel_api()
        ctypes.set_last_error(0)
        self.handle = self.kernel.CreateMutexW(None, False, "Local\\MouseLink.KVM.Desktop")
        error = ctypes.get_last_error()
        if not self.handle:
            raise OSError(error, "无法检查键鼠设备占用情况。")
        if error == 183:
            self.close()
            raise OSError("MouseLink 或另一个键鼠后台正在运行，请先从其托盘退出后重试。")

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
