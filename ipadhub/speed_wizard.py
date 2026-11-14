"""Measure the user's two screens and the Windows pointer response on one mouse."""
import ctypes
from ctypes import wintypes
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout,
                               QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .runtime import bridge_modules
from .monitor_size import active_screen_mm
from .ipad_screens import IPAD_SCREENS


def calibration_backend():
    directory = str(bridge_modules())
    if directory not in sys.path:
        sys.path.insert(0, directory)
    import pointer_calibration
    return pointer_calibration


class _MonitorInfo(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("rect", wintypes.RECT), ("work", wintypes.RECT),
                ("flags", wintypes.DWORD), ("device", wintypes.WCHAR * 32)]


def physical_screen(hwnd):
    """Resolve the monitor containing the dialog in physical desktop coordinates."""
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.SetThreadDpiAwarenessContext.argtypes = [wintypes.HANDLE]
    user.SetThreadDpiAwarenessContext.restype = wintypes.HANDLE
    user.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user.MonitorFromWindow.restype = wintypes.HANDLE
    user.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
    user.GetMonitorInfoW.restype = wintypes.BOOL
    previous = user.SetThreadDpiAwarenessContext(-4)  # Per-monitor DPI aware v2.
    if not previous:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        monitor = user.MonitorFromWindow(hwnd, 0)
        info = _MonitorInfo()
        info.size = ctypes.sizeof(info)
        if not monitor or not user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise OSError("请把校准窗口移到要使用的电脑屏幕，再试一次。")
        rect = info.rect
        return info.device, (rect.right - rect.left, rect.bottom - rect.top)
    finally:
        user.SetThreadDpiAwarenessContext(previous)


def message(text="", role="muted"):
    label = QLabel(text)
    label.setProperty("role", role)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class SpeedWizard(QDialog):
    speeds = ("慢速", "平常速度", "快速")

    def __init__(self, parent=None, backend=None, screen_reader=physical_screen,
                 dimension_reader=active_screen_mm, previous_geometry=None):
        super().__init__(parent)
        self.backend = backend if backend is not None else calibration_backend()
        self.screen_reader = screen_reader
        self.dimension_reader = dimension_reader
        self.detected_screen = None
        self.geometry_settings = {}
        self.profile = None
        self.sampler = None
        self.reference = None
        self.samples = []
        self.setWindowTitle("匹配电脑鼠标速度")
        self.resize(540, 415)
        outer = QVBoxLayout(self)
        outer.setSpacing(12)
        self.pages = QStackedWidget()
        outer.addWidget(self.pages)

        geometry = QWidget()
        page = QVBoxLayout(geometry)
        page.addWidget(message("选择 iPad 型号和摆放方向", "subtitle"))
        self.screen_status = message()
        page.addWidget(self.screen_status)
        self.model = QComboBox()
        self.model.setAccessibleName("iPad 型号")
        self.model.addItem("请选择 iPad 型号")
        for item in IPAD_SCREENS:
            self.model.addItem(item.label, (item.width_mm, item.height_mm))
        self.model.addItem("其他型号 · 手动填写")
        self.direction = QComboBox()
        self.direction.addItems(("横屏", "竖屏"))
        self.direction.setAccessibleName("iPad 摆放方向")
        selection = QHBoxLayout()
        selection.addWidget(self.model, 1)
        selection.addWidget(self.direction)
        page.addLayout(selection)
        page.addWidget(message("型号可在 iPad「设置 → 通用 → 关于本机」查看。自动填入的可视区域尺寸可以修改。", "hint"))
        fields = QGridLayout()
        fields.addWidget(QLabel("宽（毫米）"), 0, 1)
        fields.addWidget(QLabel("高（毫米）"), 0, 2)
        self.sizes = []
        for row, device in enumerate(("电脑", "iPad"), 1):
            fields.addWidget(QLabel(device), row, 0)
            pair = []
            for col, axis in enumerate(("宽", "高"), 1):
                field = QDoubleSpinBox()
                field.setRange(0, 5000)
                field.setDecimals(1)
                field.setSpecialValueText("请输入")
                field.setAccessibleName(f"{device}屏幕{axis}，毫米")
                fields.addWidget(field, row, col)
                pair.append(field)
            self.sizes.append(pair)
        page.addLayout(fields)
        self.model.currentIndexChanged.connect(self.fill_ipad)
        self.direction.currentIndexChanged.connect(self.fill_ipad)
        self.restore_geometry_settings(previous_geometry)
        page.addStretch()
        self.pages.addWidget(geometry)

        motion = QWidget()
        page = QVBoxLayout(motion)
        self.step = message("", "subtitle")
        page.addWidget(self.step)
        page.addWidget(message("在同一只鼠标和同一块电脑屏幕上采样。点击采样后有 3 秒把指针放好；看到「开始移动」后水平向一侧划动，停在屏内。3 秒后自动结束。也可以在起点按 F8 立即开始，再按 F8 结束。"))
        self.capture = QPushButton("开始采样")
        self.capture.clicked.connect(self.prepare_sample)
        page.addWidget(self.capture)
        self.retry = QPushButton("重新采集三档")
        self.retry.clicked.connect(self.reset_samples)
        page.addWidget(self.retry)
        page.addStretch()
        self.pages.addWidget(motion)
        self.status = message("", "hint")
        self.status.setAccessibleName("校准进度")
        outer.addWidget(self.status)
        actions = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        self.previous = QPushButton("上一步")
        self.previous.clicked.connect(self.go_back)
        self.next = QPushButton("下一步")
        self.next.clicked.connect(self.advance)
        actions.addWidget(cancel)
        actions.addStretch()
        actions.addWidget(self.previous)
        actions.addWidget(self.next)
        for b in (cancel, self.previous, self.next, self.capture, self.retry):
            b.setAutoDefault(False)
        outer.addLayout(actions)
        self.previous.hide()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.timer_elapsed)
        self.shortcut = QShortcut(QKeySequence("F8"), self, activated=self.keyboard_sample)

    def restore_geometry_settings(self, settings):
        if not isinstance(settings, dict):
            return
        label = settings.get("model")
        index = self.model.findText(label) if isinstance(label, str) else -1
        if index > 0:
            self.model.setCurrentIndex(index)
        if settings.get("orientation") == "portrait":
            self.direction.setCurrentIndex(1)
        size = settings.get("ipad_mm")
        if (index > 0 and isinstance(size, list) and len(size) == 2
                and all(isinstance(x, (int, float)) and 0 < x <= 5000 for x in size)):
            for field, value in zip(self.sizes[1], size):
                field.setValue(value)

    def fill_ipad(self):
        size = self.model.currentData()
        if size:
            long_side, short_side = sorted(size, reverse=True)
            width, height = (long_side, short_side) if self.direction.currentIndex() == 0 else (short_side, long_side)
        elif self.sender() is self.direction:
            width, height = reversed([field.value() for field in self.sizes[1]])
        else:
            width, height = 0, 0
        for field, value in zip(self.sizes[1], (width, height)):
            field.setValue(value)

    def showEvent(self, event):
        super().showEvent(event)
        if self.windowHandle() is not None and not getattr(self, "_watching_screen", False):
            self.windowHandle().screenChanged.connect(self.detect_screen)
            self._watching_screen = True
        self.detect_screen()

    def detect_screen(self, *_):
        try:
            current = self.screen_reader(int(self.winId()))
        except (OSError, ValueError):
            self.screen_status.setText("请将窗口移到平时跨出的电脑显示器，手动填写其可视区域宽、高。")
            return
        if current == self.detected_screen:
            return
        self.detected_screen = current
        name, pixels = current
        try:
            size = self.dimension_reader(name, pixels)
        except OSError:
            size = None
        for field, value in zip(self.sizes[0], size or (0, 0)):
            field.setValue(value)
        display = name.rsplit("\\", 1)[-1]
        if size:
            self.screen_status.setText(f"已识别电脑屏幕 {display}（{pixels[0]} × {pixels[1]} 像素），已填入屏幕尺寸。")
        else:
            self.screen_status.setText(f"电脑屏幕 {display} 未提供物理尺寸，可手动填写发光区域的宽和高。")

    def advance(self):
        if self.pages.currentIndex() == 1:
            if self.profile is not None:
                self.accept()
            return
        self.pc_mm, self.ipad_mm = ([field.value() for field in pair] for pair in self.sizes)
        if min(*self.pc_mm, *self.ipad_mm) <= 0:
            self.status.setText("请选择 iPad 型号，或补全表中尚缺的可视区域尺寸。")
            return
        try:
            self.reference, self.pc_pixels = self.screen_reader(int(self.winId()))
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.geometry_settings = {"model": self.model.currentText(),
                                  "orientation": "landscape" if self.ipad_mm[0] >= self.ipad_mm[1] else "portrait",
                                  "ipad_mm": list(self.ipad_mm)}
        self.pages.setCurrentIndex(1)
        self.previous.show()
        self.next.setText("保存并使用")
        self.reset_samples()

    def go_back(self):
        self.cancel_capture()
        self.profile = None
        self.samples.clear()
        self.pages.setCurrentIndex(0)
        self.status.clear()
        self.previous.hide()
        self.next.setText("下一步")
        self.next.setEnabled(True)

    def reset_samples(self):
        self.cancel_capture()
        self.profile = None
        self.samples.clear()
        self.status.clear()
        self.update_controls()

    def update_controls(self):
        complete = self.profile is not None
        busy = self.timer.isActive() or self.sampler is not None
        self.previous.setEnabled(not busy)
        self.next.setEnabled(complete and not busy)
        self.retry.setVisible(bool(self.samples))
        self.retry.setEnabled(not busy)
        self.capture.setEnabled(not (complete or busy))
        self.step.setText("三档采样完成" if complete else f"{len(self.samples) + 1} / 3　{self.speeds[len(self.samples)]}")

    def prepare_sample(self):
        self.status.setText("准备采样。请将指针放在该电脑屏幕内，留出水平移动的空间，然后停住。")
        self.timer.start(3000)
        self.update_controls()

    def keyboard_sample(self):
        if self.pages.currentIndex() != 1 or self.profile is not None:
            return
        self.timer.stop()
        if self.sampler is None:
            self.begin_sample()
        else:
            self.end_sample()

    def timer_elapsed(self):
        if self.sampler is None:
            self.begin_sample()
        else:
            self.end_sample()

    def begin_sample(self):
        try:
            if self.screen_reader(int(self.winId())) != (self.reference, self.pc_pixels):
                raise ValueError("校准窗口所在的显示器已改变，请返回上一步重新选择。")
            self.sampler = self.backend.WindowsStrokeSampler()
            self.sampler.start()
            self.status.setText(f"开始移动。请以{self.speeds[len(self.samples)]}水平向一侧划动一次。")
            self.timer.start(3000)
        except (OSError, ValueError, RuntimeError) as error:
            self.cancel_capture()
            self.status.setText(str(error))
        self.update_controls()

    def end_sample(self):
        sampler, self.sampler = self.sampler, None
        try:
            point = sampler.finish()
            if (point["monitor"].casefold() != self.reference.casefold()
                    or self.screen_reader(int(self.winId())) != (self.reference, self.pc_pixels)):
                raise ValueError("请在刚才测量的那块电脑屏幕上采样。")
            if self.samples:
                first = self.samples[0]
                if point["mouse"] != first["mouse"] or point["windows"] != first["windows"]:
                    raise ValueError("输入设备或 Windows 指针设置发生变化，请重新采集三档。")
                if point["rate"] <= self.samples[-1]["rate"] * 1.1:
                    raise ValueError("此划动需要比上一档更快，请重新采样。")
            samples = [*self.samples, point]
            if len(samples) == 3:
                orientation = "landscape" if self.ipad_mm[0] >= self.ipad_mm[1] else "portrait"
                self.profile = self.backend.build_profile(samples, self.pc_pixels, self.pc_mm, self.ipad_mm, orientation)
            self.samples = samples
            self.status.setText("保存后将以 100% 作为匹配电脑的速度，可继续微调。"
                                if self.profile else "已记录。请准备下一档。")
        except (OSError, ValueError, RuntimeError) as error:
            self.status.setText(str(error))
        finally:
            sampler.cancel()
        self.update_controls()

    def cancel_capture(self):
        self.timer.stop()
        if self.sampler is not None:
            self.sampler.cancel()
            self.sampler = None

    def done(self, result):
        self.cancel_capture()
        super().done(result)
