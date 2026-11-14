"""Measure Windows pointer travel in physical pixels and map it to iPad HID units."""
import ctypes
from ctypes import wintypes
from collections import deque
import hashlib
import math
from statistics import median
import threading
import time


def _positive(value, maximum=1e7):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 < value <= maximum)


def validate_profile(profile):
    if not isinstance(profile, dict) or profile.get("version") != 1:
        raise ValueError("鼠标校准版本无效，请重新校准。")
    curve = profile.get("curve")
    if (not isinstance(curve, (list, tuple)) or len(curve) != 3
            or any(not isinstance(p, (list, tuple)) or len(p) != 2
                   or not all(_positive(n) for n in p) for p in curve)
            or not curve[0][0] < curve[1][0] < curve[2][0]):
        raise ValueError("鼠标速度曲线无效，请重新采集慢、中、快移动。")
    scales = profile.get("hid_per_px")
    physical = profile.get("mm_per_px")
    for pair in (scales, physical):
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(_positive(v, 32767) for v in pair)):
            raise ValueError("屏幕尺寸无效，请检查毫米与像素。")
    for field in ("mouse", "monitor", "windows"):
        if not isinstance(profile.get(field), str) or not 0 < len(profile[field]) <= 256:
            raise ValueError("校准缺少鼠标或参考显示器信息。")
    if profile.get("orientation") not in ("landscape", "portrait"):
        raise ValueError("请选择 iPad 当前方向。")
    result = {k: profile[k] for k in ("mouse", "monitor", "windows", "orientation")}
    serial = profile.get("board_serial", "")
    if not isinstance(serial, str) or len(serial) > 256:
        raise ValueError("开发板标识无效。")
    result.update(version=1, curve=[list(p) for p in curve], hid_per_px=list(scales),
                  mm_per_px=list(physical), board_serial=serial)
    return result


def build_profile(strokes, pc_pixels, pc_mm, ipad_mm, orientation):
    if not isinstance(strokes, (list, tuple)) or len(strokes) != 3:
        raise ValueError("请完成慢、中、快三组移动。")
    groups = [[s] if isinstance(s, dict) else s for s in strokes]
    if any(not isinstance(group, (list, tuple)) or not group for group in groups):
        raise ValueError("每档至少需要一次有效的单向移动。")
    first = groups[0][0]
    curve = []
    for group in groups:
        for s in group:
            if (not isinstance(s, dict) or not isinstance(first, dict)
                    or not all(s.get(k) and s.get(k) == first.get(k)
                               for k in ("mouse", "monitor", "windows"))):
                raise ValueError("请使用同一鼠标和电脑屏幕完成所有采样。")
            if not _positive(s.get("rate")) or not _positive(s.get("gain")):
                raise ValueError("本次移动数据不足，请重试。")
        curve.append([median(s["rate"] for s in group), median(s["gain"] for s in group)])
    if not (curve[0][0] * 1.1 < curve[1][0] and curve[1][0] * 1.1 < curve[2][0]):
        raise ValueError("三次移动速度过于接近，请分别慢移、正常移动和快移。")
    for pair in (pc_pixels, pc_mm, ipad_mm):
        if (not isinstance(pair, (list, tuple)) or len(pair) != 2
                or not all(_positive(v, 100000) for v in pair)):
            raise ValueError("请填写两块屏幕可视区域的宽、高。")
    mm_per_px = [mm / pixels for mm, pixels in zip(pc_mm, pc_pixels)]
    return validate_profile(dict(version=1, curve=curve, mm_per_px=mm_per_px,
        hid_per_px=[32767.0 * mm / span for mm, span in zip(mm_per_px, ipad_mm)],
        orientation=orientation, **{k: first[k] for k in ("mouse", "monitor", "windows")}))


class PhysicalMotion:
    def __init__(self, profile):
        self.profile = validate_profile(profile)
        self._recent = deque()
        self._distance = 0.0

    def project(self, dx, dy, at, trim):
        # A short trailing measurement window stabilizes counts/second across
        # polling rates; every incoming delta still moves the pointer immediately.
        period = .024
        while self._recent and at - self._recent[0][0] >= period:
            self._distance -= self._recent.popleft()[1]
        if not self._recent:
            self._distance = 0.0
        length = math.hypot(dx, dy)
        self._recent.append((at, length))
        self._distance += length
        rate = self._distance / period
        curve = self.profile["curve"]
        gain = curve[-1][1]
        if rate <= curve[0][0]:
            gain = curve[0][1]
        else:
            for (lo, a), (hi, b) in zip(curve, curve[1:]):
                if rate <= hi:
                    gain = a + (b - a) * (rate - lo) / (hi - lo)
                    break
        x, y = self.profile["hid_per_px"]
        mx, my = self.profile["mm_per_px"]
        return dx * gain * x * trim, dy * gain * y * trim, abs(dx * mx) > abs(dy * my)


def windows_pointer_signature():
    api = ctypes.windll.user32
    speed = wintypes.UINT()
    settings = (ctypes.c_int * 3)()
    if (not api.SystemParametersInfoW(0x70, 0, ctypes.byref(speed), 0)
            or not api.SystemParametersInfoW(3, 0, settings, 0)):
        raise OSError("无法读取 Windows 指针速度设置。")
    return ",".join(map(str, (speed.value, *settings)))


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("rect", wintypes.RECT), ("work", wintypes.RECT),
                ("flags", wintypes.DWORD), ("device", wintypes.WCHAR * 32)]


def physical_cursor():
    point = wintypes.POINT()
    if not ctypes.windll.user32.GetPhysicalCursorPos(ctypes.byref(point)):
        raise OSError("无法读取 Windows 物理光标坐标。")
    return point.x, point.y


def reference_monitor(point):
    api = ctypes.windll.user32
    api.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    api.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    api.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    api.MonitorFromPoint.restype = wintypes.HANDLE
    api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    previous = api.SetThreadDpiAwarenessContext(-4)
    if not previous:
        raise OSError("无法读取显示器的物理像素坐标。")
    try:
        info = _MonitorInfo()
        info.size = ctypes.sizeof(info)
        handle = api.MonitorFromPoint(wintypes.POINT(*point), 0)
        if not handle or not api.GetMonitorInfoW(handle, ctypes.byref(info)):
            raise OSError("无法确定参考显示器。")
        r = info.rect
        return info.device, (r.left, r.top, r.right, r.bottom)
    finally:
        if previous:
            api.SetThreadDpiAwarenessContext(previous)


def raw_device_id(handle):
    api = ctypes.windll.user32
    api.GetRawInputDeviceInfoW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                          wintypes.LPVOID, ctypes.POINTER(wintypes.UINT)]
    count = wintypes.UINT()
    info = api.GetRawInputDeviceInfoW
    if info(handle, 0x20000007, None, ctypes.byref(count)) == -1 or not 0 < count.value < 32768:
        return ""
    name = ctypes.create_unicode_buffer(count.value + 1)
    if info(handle, 0x20000007, name, ctypes.byref(count)) == -1 or not name.value:
        return ""
    return hashlib.sha256(name.value.casefold().encode("utf-8")).hexdigest()[:24]


class WindowsStrokeSampler:
    """Listen only during an explicit local, single-direction calibration stroke."""
    def __init__(self):
        self.capture = None
        self._lock = threading.Lock()

    def start(self):
        from bridge import RawMouseCapture
        if self.capture:
            raise RuntimeError("鼠标采样已开始。")
        self.origin = physical_cursor()
        self.monitor, self.rect = reference_monitor(self.origin)
        self.windows = windows_pointer_signature()
        self.mouse = self.error = ""
        self.first = self.last = None
        self.dx = self.dy = self.travel = 0.0
        self.capture = RawMouseCapture(None, device_move=self._record)
        if not self.capture.start():
            self.cancel()
            raise RuntimeError("无法开始读取鼠标，请关闭其他校准窗口后重试。")

    def _record(self, dx, dy, device):
        at = time.perf_counter()
        with self._lock:
            if self.error:
                return
            if not device or (self.mouse and self.mouse != device):
                self.error = "采样期间检测到其它输入设备，请只使用同一实体鼠标。"
                return
            self.mouse = device
            try:
                x, y = physical_cursor()
            except OSError as exc:
                self.error = str(exc)
                return
            l, t, r, b = self.rect
            if not (l+2 < x < r-3 and t+2 < y < b-3):
                self.error = "光标触边或离开参考显示器，请在屏幕中央重试。"
            if self.first is None:
                self.first = at
            self.last = at
            self.dx += dx
            self.dy += dy
            self.travel += math.hypot(dx, dy)

    def finish(self):
        if self.capture is None:
            raise RuntimeError("请先开始本档采样。")
        capture = self.capture
        try:
            end = physical_cursor()
        finally:
            self.cancel()
        if capture.failure:
            raise RuntimeError("鼠标采样中断，请重新采集。")
        if self.error:
            raise ValueError(self.error)
        if reference_monitor(end)[0] != self.monitor or windows_pointer_signature() != self.windows:
            raise ValueError("参考显示器或 Windows 指针设置发生变化，请重新采集。")
        px, py = end[0]-self.origin[0], end[1]-self.origin[1]
        if (self.first is None or self.last - self.first < .035 or abs(px) < 40
                or px * self.dx <= 0 or abs(self.dx) < .90 * self.travel
                or abs(py) > abs(px)*.25):
            raise ValueError("请在屏幕中央水平、单向、连续移动至少 40 像素，然后停下。")
        return dict(rate=self.travel / (self.last-self.first), gain=abs(px/self.dx),
                    mouse=self.mouse, monitor=self.monitor, windows=self.windows)

    def cancel(self):
        capture, self.capture = self.capture, None
        if capture is not None:
            capture.stop()
