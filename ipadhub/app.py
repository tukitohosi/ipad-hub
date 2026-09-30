"""Shared PySide6 shell with explicit live and device-free preview entrypoints."""
import argparse
import ctypes
import hashlib
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QFrame, QHBoxLayout, QLabel, QMainWindow, QMenu, QProgressBar, QPushButton,
    QListView, QMessageBox, QScrollArea, QSlider, QStackedWidget, QSystemTrayIcon, QVBoxLayout, QWidget,
)

from .model import Mode, PreviewSession, State
from .controls import SegmentedChoice, icon


NAMES = {Mode.DISPLAY: "iPad 副屏", Mode.BRIDGE: "键鼠控制"}
SCENARIOS = ["正常连接", "缺少条件", "连接失败", "停止失败", "长设备名"]


def label(text, role="body"):
    w = QLabel(text)
    w.setProperty("role", role)
    w.setWordWrap(True)
    w.setTextFormat(Qt.TextFormat.PlainText)
    return w


def button(text, callback=None, primary=False, glyph=None):
    w = QPushButton(text)
    w.setCursor(Qt.CursorShape.PointingHandCursor)
    w.setMinimumHeight(44)
    w.setProperty("primary", primary)
    if glyph:
        w.setProperty("glyph", glyph)
        w.setIconSize(QSize(18, 18))
    if callback:
        w.clicked.connect(callback)
    return w


def card():
    w = QFrame()
    w.setObjectName("card")
    layout = QVBoxLayout(w)
    layout.setContentsMargins(24, 22, 24, 22)
    layout.setSpacing(16)
    return w, layout


def combo(items):
    w = QComboBox()
    view = QListView()
    view.setSpacing(3)
    view.setFrameShape(QFrame.Shape.NoFrame)
    view.setUniformItemSizes(True)
    w.setView(view)
    w.addItems(items)
    w.setMinimumHeight(46)
    w.setMinimumWidth(160)
    w.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    w.setMinimumContentsLength(12)
    w.setCursor(Qt.CursorShape.PointingHandCursor)
    w.setMaxVisibleItems(8)
    w.currentTextChanged.connect(w.setToolTip)
    w.setToolTip(w.currentText())
    return w


class DeviceSketch(QWidget):
    def __init__(self, bridge=False):
        super().__init__()
        self.bridge = bridge
        self.dark = False
        self.setMinimumHeight(110)
        self.setMaximumHeight(125)
        self.setAccessibleName("电脑键鼠控制 iPad 示意图" if bridge else "电脑扩展至 iPad 示意图")

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        factor = min(1.0, (self.width() - 8) / 286)
        p.translate((self.width() - 286 * factor) / 2, (self.height() - 112 * factor) / 2)
        p.scale(factor, factor)
        ink = QColor("#a7a5ff" if self.dark else "#6761d9")
        bg = QColor("#32344b" if self.dark else "#eeedfc")
        p.setPen(ink)
        p.setBrush(bg)
        p.drawRoundedRect(8, 10, 143, 82, 9, 9)
        p.drawLine(79, 93, 79, 104)
        p.drawLine(58, 104, 100, 104)
        p.drawRoundedRect(196, 4, 78, 103, 10, 10)
        p.drawLine(164, 50, 183, 50)
        p.drawLine(178, 45, 183, 50)
        p.drawLine(178, 55, 183, 50)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ink)
        if self.bridge:
            p.drawRoundedRect(25, 65, 96, 14, 3, 3)
            p.drawRoundedRect(126, 60, 12, 20, 6, 6)
            p.drawEllipse(220, 42, 28, 28)
        else:
            p.drawRoundedRect(19, 21, 120, 14, 3, 3)
            p.drawRoundedRect(19, 42, 51, 37, 3, 3)
            p.drawRoundedRect(77, 42, 62, 37, 3, 3)
            p.drawRoundedRect(206, 17, 58, 12, 3, 3)
            p.drawRoundedRect(206, 36, 58, 57, 3, 3)


class HubWindow(QMainWindow):
    def __init__(self, preview=True):
        super().__init__()
        self.preview_mode = preview
        self.session = PreviewSession()
        self.theme_choice = "跟随系统"
        self.setWindowTitle("iPadHub · 交互预览")
        self.setMinimumSize(820, 520)
        self.resize(1180, 850)
        self.setWindowIcon(self.make_icon())
        self._quitting = False
        self.mode_controls = {}
        self.sketches = []
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(202)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 28, 20, 24)
        side.setSpacing(10)
        side.addWidget(label("iPadHub", "brand"))
        side.addWidget(label("电脑与 iPad，连在一起", "muted"))
        side.addSpacing(30)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        for i, name in enumerate(["首页", "iPad 副屏", "键鼠控制", "设备工具", "设置"]):
            b = button(name, lambda checked=False, n=i: self.navigate(n), glyph=["home", "display", "keyboard", "tools", "settings"][i])
            b.setObjectName("nav")
            b.setCheckable(True)
            b.toggled.connect(lambda _: self.update_button_icons())
            b.setAccessibleName(f"{name}，Alt+{i + 1}")
            self.nav_group.addButton(b, i)
            side.addWidget(b)
            QShortcut(QKeySequence(f"Alt+{i + 1}"), self, activated=lambda n=i: self.navigate(n))
        side.addStretch()
        side.addWidget(label("一个界面\n两种使用方式", "muted"))
        side.addSpacing(12)
        side.addWidget(label("0.1.0 · 界面审核版", "micro"))
        outer.addWidget(sidebar)
        content = QWidget()
        main = QVBoxLayout(content)
        main.setContentsMargins(28, 24, 28, 18)
        main.setSpacing(16)
        preview = QFrame()
        self.preview_bar = preview
        preview.setObjectName("preview")
        pl = QHBoxLayout(preview)
        pl.setContentsMargins(14, 10, 14, 10)
        badge = label("PREVIEW" if self.preview_mode else "IPADHUB", "badge")
        pl.addWidget(badge)
        self.top_hint = label("交互预览 · 仅模拟，不操作真实设备" if self.preview_mode else "选择一种用途，连接你的 iPad", "hint")
        pl.addWidget(self.top_hint, 1)
        self.scenario_box = combo(SCENARIOS)
        self.scenario_box.setObjectName("scenario")
        self.scenario_box.setFixedWidth(158)
        self.scenario_box.setAccessibleName("预览情景")
        self.scenario_box.currentTextChanged.connect(self.change_scenario)
        pl.addWidget(self.scenario_box)
        self.scenario_box.setVisible(self.preview_mode)
        main.addWidget(preview)
        self.pages = QStackedWidget()
        for page in (self.home_page(), self.mode_page(Mode.DISPLAY), self.mode_page(Mode.BRIDGE),
                     self.tools_page(), self.settings_page()):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
        main.addWidget(self.pages, 1)
        self.footer = label("预览已就绪 · 从首页选择一种用途", "micro")
        main.addWidget(self.footer)
        outer.addWidget(content, 1)
        self.create_tray()
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.quit_app)
        QShortcut(QKeySequence("Escape"), self, activated=self.stop_session)
        QApplication.styleHints().colorSchemeChanged.connect(lambda _: self.apply_theme())
        self.navigate(0)
        self.apply_theme()
        self.refresh()

    @staticmethod
    def make_icon():
        pix = QPixmap(64, 64)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#635bd9"))
        p.drawRoundedRect(2, 2, 60, 60, 16, 16)
        p.setPen(QColor("white"))
        p.setFont(QFont("Segoe UI", 32, QFont.Weight.Bold))
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "H")
        p.end()
        return QIcon(pix)

    def page(self, eyebrow, title, subtitle):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 0, 10, 0)
        l.setSpacing(16)
        l.addWidget(label(eyebrow, "eyebrow"))
        l.addWidget(label(title, "title"))
        l.addWidget(label(subtitle, "muted"))
        return w, l

    def home_page(self):
        w, l = self.page("欢迎使用 IPADHUB", "今天，想怎样使用 iPad？", "选择一种用途。应用会带你完成准备，并管理连接与安全切换。")
        row = QHBoxLayout()
        row.setSpacing(18)
        for mode, title, desc, needs, action, index in (
            (Mode.DISPLAY, "将 iPad 用作副屏", "扩展 Windows 桌面，让资料、聊天或工具拥有另一块屏幕。", "需要：iPad 打开 OpenDisplay\nWindows 已配置虚拟显示驱动", "设置副屏  →", 1),
            (Mode.BRIDGE, "使用电脑键鼠操作 iPad", "沿用电脑的鼠标和键盘，直接操作 iPad 上的原生应用。", "需要：ESP32-C3 开发板\niPad 与开发板完成蓝牙配对", "设置键鼠  →", 2),
        ):
            c, cl = card()
            sketch = DeviceSketch(mode == Mode.BRIDGE)
            self.sketches.append(sketch)
            cl.addWidget(sketch)
            cl.addWidget(label(title, "cardtitle"))
            cl.addWidget(label(desc, "muted"))
            cl.addSpacing(4)
            cl.addWidget(label(needs, "hint"))
            cl.addStretch()
            cl.addWidget(button(action.replace("  →", ""), lambda checked=False, n=index: self.navigate(n), True, "arrow"))
            row.addWidget(c, 1)
        l.addLayout(row)
        c, cl = card()
        cl.addWidget(label("一次专注于一种用途", "subtitle"))
        cl.addWidget(label("切换前会先停止当前功能，确认资源释放后再开始下一项。每次启动都从首页开始，由你决定何时连接。", "muted"))
        l.addWidget(c)
        l.addStretch()
        return w

    def mode_page(self, mode):
        display = mode == Mode.DISPLAY
        w, l = self.page("扩展你的工作空间" if display else "熟悉的键鼠，更自由的操作",
                         "将 iPad 用作副屏" if display else "使用电脑键鼠操作 iPad",
                         "请先在 iPad 上打开 OpenDisplay，并让接收端保持前台。" if display else "连接开发板，并在 iPad 的蓝牙设置中与开发板配对。")
        c, cl = card()
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(20)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        device = combo(["演示设备 · iPad Pro（尚未实际发现）"] if display else ["演示开发板 · ESP32-C3（尚未实际检测）"])
        form.addRow("iPad 设备" if display else "开发板", device)
        if display:
            self.transport_choice = SegmentedChoice(["自动", "USB", "Wi-Fi"], glyphs=["auto", "usb", "wifi"])
            self.transport_choice.setAccessibleName("连接方式")
            self.quality_choice = SegmentedChoice(["均衡", "清晰", "流畅"], captions=["日常办公", "阅读与设计", "动态画面"])
            self.quality_choice.setAccessibleName("画质预设")
            form.addRow("连接方式", self.transport_choice)
            form.addRow("画质预设", self.quality_choice)
        else:
            self.board_status = label("模拟：USB 可用 · 蓝牙已配对", "hint")
            form.addRow("连接状态", self.board_status)
            self.position_choice = SegmentedChoice(["电脑左侧", "电脑右侧"], glyphs=["left", "arrow"])
            self.position_choice.setCurrentText("电脑右侧")
            form.addRow("iPad 位置", self.position_choice)
        cl.addLayout(form)
        advanced = button("高级设置" if display else "更多键鼠设置", glyph="chevron")
        advanced.setCheckable(True)
        advanced.setObjectName("disclosure")
        details = QWidget()
        df = QFormLayout(details)
        df.setContentsMargins(0, 8, 0, 4)
        if display:
            self.resolution_choice = combo(["跟随 iPad", "1920 × 1080", "2560 × 1600"] if self.preview_mode else ["跟随 iPad 原生尺寸"])
            self.priority_choice = combo(["优先上次连接的设备", "每次手动选择"])
            df.addRow("分辨率", self.resolution_choice)
            df.addRow("设备优先级", self.priority_choice)
            df.addRow(button("连接诊断", lambda: self.show_info("连接诊断（模拟）", "接收端：等待用户在 iPad 上打开\n传输方式：自动\n虚拟显示驱动：模拟已具备\n\n电脑端就绪不代表 iPad 接收端已准备完成。"), glyph="tools"))
        else:
            speed_row = QWidget()
            sl = QHBoxLayout(speed_row)
            sl.setContentsMargins(0, 0, 0, 0)
            speed = QSlider(Qt.Orientation.Horizontal)
            self.speed_control = speed
            speed.setRange(25, 200)
            speed.setValue(100)
            speed.setAccessibleName("键鼠速度百分比")
            value = label("100%", "hint")
            speed.valueChanged.connect(lambda n: value.setText(f"{n}%"))
            sl.addWidget(speed, 1)
            sl.addWidget(value)
            df.addRow("移动速度", speed_row)
            self.bridge_mode_choice = SegmentedChoice(["自由模式", "锁定模式"], captions=["从屏幕边缘进入", "固定在 iPad"])
            df.addRow("控制模式", self.bridge_mode_choice)
            df.addRow(button("可选校准", lambda: self.show_info("可选校准（模拟）", "日常使用不要求重新校准。\n正式接入后可沿用旧参数，或按向导调整边缘映射。"), glyph="settings"))
        details.hide()
        advanced.toggled.connect(details.setVisible)
        advanced.toggled.connect(lambda on: advanced.setProperty("glyph", "chevron-up" if on else "chevron"))
        advanced.toggled.connect(lambda _: self.update_button_icons())
        cl.addWidget(advanced)
        cl.addWidget(details)
        l.addWidget(c)
        status, st = card()
        status_title = label("可用", "status")
        status_detail = label("", "muted")
        progress = QProgressBar()
        progress.setRange(0, 0)
        progress.setMaximumHeight(5)
        progress.setTextVisible(False)
        st.addWidget(status_title, 0, Qt.AlignmentFlag.AlignLeft)
        st.addWidget(status_detail)
        st.addWidget(progress)
        actions = QHBoxLayout()
        start = button("连接副屏" if display else "开始键鼠控制", lambda: self.start_session(mode), True, "play")
        stop = button("停止", self.stop_session, glyph="stop")
        stop.setObjectName("stopButton")
        recovery = button("重试资源回收", self.recover_session, glyph="refresh")
        recovery.hide()
        actions.addWidget(start)
        actions.addWidget(stop)
        actions.addWidget(recovery)
        actions.addStretch()
        st.addLayout(actions)
        l.addWidget(status)
        l.addWidget(label("预览只演示连接流程。真实功能接入后，才会创建副屏或接管键鼠。" if self.preview_mode else
                          ("OpenDisplay 需在 iPad 上保持前台。连接成功后才会创建副屏。" if display else "紧急返回：Ctrl + Alt + Esc。停止后完整释放键鼠与串口。"), "micro"))
        l.addStretch()
        self.mode_controls[mode] = dict(title=status_title, detail=status_detail, progress=progress,
                                        start=start, stop=stop, recovery=recovery, device=device,
                                        form=form, advanced=df)
        return w

    def tools_page(self):
        w, l = self.page("为需要的时候准备", "设备工具", "固件工具保留备份、刷写、恢复与诊断能力。日常使用无需重新刷机。")
        c, cl = card()
        cl.addWidget(label("ESP32-C3 固件与诊断", "cardtitle"))
        cl.addWidget(label("当前是交互预览。以下按钮仅演示界面；不会访问串口、写入固件或创建真实备份。" if self.preview_mode else "设备工具与连接模式互斥。刷写或恢复前会先备份；操作期间请保持 USB 连接。", "muted"))
        self.tool_buttons = []
        for title, info in [("备份固件", "正式版会先读取现有固件，并保存校验信息。"), ("刷写固件", "正式版会先备份，刷写完成后检查结果。"),
                            ("恢复备份", "正式版可选择已有备份，校验后恢复。"), ("设备诊断", "正式版会显示串口、开发板和蓝牙状态。")]:
            row = QHBoxLayout()
            row.addWidget(label(info if self.preview_mode else info.replace("正式版会", "").replace("正式版可", ""), "hint"), 1)
            b = button(f"演示{title}" if self.preview_mode else title, lambda checked=False, t=title: self.demo_tool(t), glyph={"备份固件":"backup", "刷写固件":"flash", "恢复备份":"refresh", "设备诊断":"tools"}[title])
            self.tool_buttons.append(b)
            row.addWidget(b)
            cl.addLayout(row)
        self.tool_status = label("先停止正在使用的功能，再运行设备工具。", "muted")
        cl.addWidget(self.tool_status)
        l.addWidget(c)
        l.addStretch()
        return w

    def settings_page(self):
        w, l = self.page("按你的习惯使用", "设置", "此处的预览设置仅保存在内存中，关闭预览后恢复默认。" if self.preview_mode else "设置保存在 iPadHub 独立目录，不覆盖旧软件。")
        c, cl = card()
        cl.addWidget(label("外观与使用习惯", "subtitle"))
        f = QFormLayout()
        f.setVerticalSpacing(24)
        f.setHorizontalSpacing(28)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.theme_box = SegmentedChoice(["跟随系统", "浅色", "深色"], glyphs=["display", "sun", "moon"])
        self.theme_box.currentTextChanged.connect(self.set_theme)
        f.addRow("外观", self.theme_box)
        f.addRow("启动页面", label("首页 · 不自动连接或接管键鼠", "hint"))
        f.addRow("关闭窗口", label("收起到托盘；从托盘菜单选择“退出”", "hint"))
        f.addRow("新应用数据", label("%LOCALAPPDATA%\\iPadHub" + ("（正式接入后使用）" if self.preview_mode else ""), "hint"))
        cl.addLayout(f)
        l.addWidget(c)
        c, cl = card()
        cl.addWidget(label("迁移与共存", "subtitle"))
        cl.addWidget(label("正式版首次运行将复制旧设置和固件备份，保留旧软件及其原数据。当前预览不执行迁移。副屏设备与开发板分别管理。" if self.preview_mode else "首次运行只复制旧设置与固件备份，不删除旧应用或数据。副屏设备与开发板身份分别管理。", "muted"))
        cl.addWidget(label("键盘：Alt + 1–5 切换页面 · Esc 取消连接 / 停止 · Ctrl + Q 退出", "hint"))
        cl.addWidget(button("重置模拟状态" if self.preview_mode else "打开 iPadHub 数据目录", self.reset_preview, glyph="refresh"))
        l.addWidget(c)
        l.addStretch()
        return w

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        self.nav_group.button(index).setChecked(True)
        self.nav_group.button(index).setFocus(Qt.FocusReason.OtherFocusReason)

    def change_scenario(self, name):
        # Scenarios never bypass release: a failed stop must still recover first.
        self.session.scenario = name
        for mode, controls in self.mode_controls.items():
            dev = controls["device"]
            text = ("演示设备 · 设计工作室的超长中文名称 iPad Pro 第六代（Wi-Fi / USB，会议室备用）" if mode == Mode.DISPLAY else
                    "演示开发板 · 工作台右侧 ESP32-C3 长名称蓝牙键鼠桥（已配对的 iPad）") if name == "长设备名" else (
                    "演示设备 · iPad Pro（尚未实际发现）" if mode == Mode.DISPLAY else "演示开发板 · ESP32-C3（尚未实际检测）")
            dev.setItemText(0, text)
            dev.setToolTip(text)
        self.refresh()

    def start_session(self, mode):
        token = self.session.request_start(mode)
        self.refresh()
        if token is not None:
            self.schedule(token)

    def stop_session(self):
        token = self.session.stop()
        self.refresh()
        if token is not None:
            self.schedule(token)

    def recover_session(self):
        token = self.session.recover()
        self.scenario_box.setCurrentText(self.session.scenario)
        self.refresh()
        if token is not None:
            self.schedule(token)

    def schedule(self, token):
        QTimer.singleShot(1000, lambda: self.advance(token))

    def advance(self, token):
        if self.session.advance(token):
            self.refresh()
            if self.session.state == State.CONNECTING:
                self.schedule(token)

    def refresh(self):
        s = self.session
        busy = s.state in (State.CONNECTING, State.STOPPING)
        for mode, c in self.mode_controls.items():
            relevant = mode in (s.owner, s.selected, s.pending)
            state = s.state if relevant else State.READY
            detail = "模拟条件已具备。点击下方按钮体验连接流程。"
            if s.busy_tool:
                detail = "设备工具正在演示操作，完成前暂时无法启动或切换用途。"
            elif s.release_blocked:
                state = State.FAILED
                detail = f"{NAMES[s.owner]}资源释放失败（模拟）。已阻止启动另一用途，请先重试资源回收。"
            elif state == State.STOPPING:
                detail = "正在释放输入、连接和当前功能占用的资源；完成前不会启动另一用途。（模拟）"
            elif state == State.CONNECTING:
                detail = "正在等待 iPad 接收端确认（模拟），请保持 OpenDisplay 在前台。" if mode == Mode.DISPLAY else "正在确认开发板与蓝牙就绪（模拟）；此预览不会接管你的键鼠。"
            elif state == State.ACTIVE:
                detail = "副屏画面传输中（模拟）。" if mode == Mode.DISPLAY else "键鼠控制中（模拟）。正式版将保留紧急返回电脑的能力。"
            elif state == State.MISSING:
                detail = "尚未收到接收端确认（模拟）。请在 iPad 上打开 OpenDisplay，并检查显示驱动。" if mode == Mode.DISPLAY else "尚未检测到就绪开发板（模拟）。请连接 USB，并检查 iPad 蓝牙配对。"
            elif state == State.FAILED:
                detail = "连接未完成（模拟）。检查设备连接后，切换预览情景为“正常连接”并重试。"
            elif s.owner and s.owner != mode:
                detail = f"{NAMES[s.owner]}正在使用。继续后会先停止它，确认资源释放后再启动此用途。"
            c["title"].setText(f"{state.value}  ·  模拟状态")
            c["title"].setProperty("tone", state.name.lower())
            c["title"].style().unpolish(c["title"])
            c["title"].style().polish(c["title"])
            c["detail"].setText(detail)
            c["progress"].setVisible(busy and relevant)
            c["start"].setEnabled(not busy and not s.busy_tool and not s.release_blocked and not (s.owner == mode and s.state == State.ACTIVE))
            c["start"].setText(("停止当前功能并连接副屏" if mode == Mode.DISPLAY else "停止当前功能并开始键鼠") if s.owner and s.owner != mode else ("连接副屏" if mode == Mode.DISPLAY else "开始键鼠控制"))
            c["stop"].setText("取消" if s.state == State.CONNECTING or s.pending else "停止")
            c["stop"].setEnabled(s.owner is not None and not s.busy_tool and not s.release_blocked and (s.state != State.STOPPING or s.pending is not None))
            c["recovery"].setVisible(s.release_blocked)
        if hasattr(self, "board_status"):
            self.board_status.setText("模拟：开发板未就绪 · 请检查 USB 和蓝牙" if s.scenario == "缺少条件" else "模拟：USB 可用 · 蓝牙已配对")
        if hasattr(self, "tool_buttons"):
            for b in self.tool_buttons:
                b.setEnabled(s.owner is None and not s.busy_tool and not s.release_blocked)
        active = NAMES[s.owner] if s.owner else "无活动用途"
        if hasattr(self, "footer"):
            self.footer.setText(f"{active} · {s.state.value}（模拟）  |  第一版一次使用一种功能")

    def demo_tool(self, title):
        if not self.session.begin_tool():
            return
        token = self.session.epoch
        self.tool_status.setText(f"正在演示{title}流程… 期间禁止开始或切换用途。")
        self.refresh()
        def finish():
            if self.session.finish_tool(token):
                self.tool_status.setText(f"{title}演示结束。未读取设备，未写入固件或备份文件。")
                self.refresh()
        QTimer.singleShot(2400, finish)

    def reset_preview(self):
        self.session.reset()
        self.scenario_box.setCurrentText("正常连接")
        self.tool_status.setText("先停止正在使用的功能，再运行设备工具。")
        self.refresh()

    def show_info(self, title, text):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumWidth(440)
        l = QVBoxLayout(dialog)
        l.setContentsMargins(24, 24, 24, 24)
        l.addWidget(label(text))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("知道了")
        buttons.accepted.connect(dialog.accept)
        l.addWidget(buttons)
        dialog.exec()

    def create_tray(self):
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("iPadHub · 交互预览")
        menu = QMenu(self)
        show = QAction("打开 iPadHub", self)
        show.triggered.connect(self.show_window)
        stop = QAction("停止当前模拟功能", self)
        stop.triggered.connect(self.stop_session)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(show)
        menu.addAction(stop)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()

    def show_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if self._quitting:
            event.accept()
        elif self.tray.isVisible():
            event.ignore()
            self.hide()
            self.tray.showMessage("iPadHub 已收起", "从托盘打开窗口，或选择“退出”。")
        else:
            event.ignore()
            self.showMinimized()

    def quit_app(self):
        self._quitting = True
        self.session.reset()  # Preview owns no external resources.
        self.tray.hide()
        QApplication.quit()

    def set_theme(self, name):
        self.theme_choice = name
        self.apply_theme()

    def update_button_icons(self):
        dark = getattr(self, "dark", False)
        for b in self.findChildren(QPushButton):
            glyph = b.property("glyph")
            if not glyph or b.objectName() == "segment":
                continue
            color = "#b5bdd0" if dark else "#70798f"
            if b.property("primary"):
                color = "#ffffff"
            elif b.isChecked():
                color = "#c9bdff" if dark else "#6851d9"
            elif b.objectName() == "stopButton":
                color = "#df91a4" if dark else "#bb6378"
            b.setIcon(icon(glyph, color))

    def apply_theme(self):
        dark = self.theme_choice == "深色" or (self.theme_choice == "跟随系统" and QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        self.dark = dark
        bg, surface, text, muted, border, tint = (("#161922", "#202430", "#eef0f9", "#a3acc2", "#353b4d", "#302b48") if dark else
                                                   ("#f5f6fb", "#ffffff", "#232941", "#66738b", "#e7eaf2", "#f0edfd"))
        field = "#272c3a" if dark else "#f6f7fb"
        hover = "#34384c" if dark else "#ebeef7"
        accent = "#c4b5ff" if dark else "#6850d5"
        selected = "#44385f" if dark else "#f1edff"
        disabled = "#777e92" if dark else "#a7afc0"
        arrow = (Path(__file__).resolve().parent / "assets" / ("chevron-dark.svg" if dark else "chevron-light.svg")).as_posix()
        self.setStyleSheet(f"""
            QWidget {{ color: {text}; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; }}
            QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {bg}; }}
            QFrame#sidebar, QFrame#card {{ background: {surface}; }}
            QFrame#sidebar {{ border-right: 1px solid {border}; }}
            QFrame#card {{ border: 1px solid {border}; border-radius: 18px; }}
            QFrame#preview {{ background: {surface}; border: 1px solid {border}; border-radius: 13px; }}
            QLabel {{ background: transparent; }}
            QLabel[role='brand'] {{ font-size: 28px; font-weight: 700; letter-spacing: -1px; }}
            QLabel[role='title'] {{ font-size: 28px; font-weight: 700; }}
            QLabel[role='eyebrow'] {{ font-size: 11px; font-weight: 600; color: {accent}; }}
            QLabel[role='cardtitle'] {{ font-size: 19px; font-weight: 600; }}
            QLabel[role='subtitle'] {{ font-size: 16px; font-weight: 600; }}
            QLabel[role='muted'] {{ color: {muted}; font-size: 13px; }}
            QLabel[role='micro'] {{ color: {muted}; font-size: 11px; }}
            QLabel[role='hint'] {{ color: {muted}; font-size: 12px; }}
            QLabel[role='badge'] {{ color: {accent}; background: {tint}; border-radius: 6px; padding: 5px 8px; font-size: 10px; font-weight: 700; }}
            QLabel[role='status'] {{ background: {'#203d36' if dark else '#edf8f2'}; color: {'#9ed4bc' if dark else '#397f62'}; border-radius: 12px; padding: 6px 11px; font-size: 12px; font-weight: 600; }}
            QLabel[role='status'][tone='failed'], QLabel[role='status'][tone='missing'] {{ background: {'#493527' if dark else '#fff4e7'}; color: {'#e9c39b' if dark else '#a86b31'}; }}
            QLabel[role='status'][tone='connecting'], QLabel[role='status'][tone='stopping'] {{ background: {tint}; color: {accent}; }}
            QPushButton {{ background: {field}; border: 1px solid transparent; border-radius: 11px; padding: 8px 18px; font-weight: 500; }}
            QPushButton:hover {{ background: {hover}; }}
            QPushButton:pressed {{ background: {tint}; border-color: #a393e9; }}
            QPushButton:focus, QComboBox:focus {{ border: 1px solid #9a87e5; }}
            QPushButton[primary='true'] {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #8068ee,stop:1 #6552d6); color: white; border: 1px solid #7963e3; font-weight: 600; padding: 8px 22px; }}
            QPushButton[primary='true']:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #8c76f4,stop:1 #7460e4); }}
            QPushButton[primary='true']:pressed {{ background: #5946bf; border-color: #5946bf; }}
            QPushButton[primary='true']:focus {{ border: 1px solid {'#d8cdff' if dark else '#493597'}; }}
            QPushButton:disabled, QPushButton[primary='true']:disabled {{ color: {disabled}; background: {field}; border-color: transparent; }}
            QPushButton#stopButton:enabled {{ background: {'#382b36' if dark else '#fcf0f3'}; color: {'#e5a3b3' if dark else '#b36379'}; }}
            QPushButton#stopButton:hover {{ background: {'#493440' if dark else '#f8e3e9'}; }}
            QPushButton#nav {{ text-align: left; padding: 9px 15px; background: transparent; border: 1px solid transparent; border-radius: 11px; min-height: 28px; color: {muted}; }}
            QPushButton#nav:hover {{ background: {field}; color: {text}; }}
            QPushButton#nav:checked {{ background: {tint}; color: {accent}; font-weight: 600; }}
            QPushButton#nav:focus {{ border-color: {'#9d81be' if dark else '#b19bdf'}; }}
            QPushButton#disclosure {{ text-align: left; border: 1px solid transparent; background: {field}; color: {muted}; padding: 8px 14px; }}
            QPushButton#disclosure:hover, QPushButton#disclosure:checked {{ color: {accent}; background: {tint}; }}
            QPushButton#disclosure:focus {{ border-color: #9a87e5; }}
            QWidget#segmented {{ background: {field}; border: 1px solid {border}; border-radius: 13px; }}
            QPushButton#segment {{ border: 1px solid transparent; border-radius: 9px; background: transparent; color: {muted}; padding: 5px 9px; font-size: 12px; }}
            QPushButton#segment:hover {{ color: {text}; background: {hover}; }}
            QPushButton#segment:checked {{ color: {accent}; background: {'#3a344f' if dark else '#ffffff'}; border-color: {'#544768' if dark else '#e3ddf7'}; font-weight: 600; }}
            QPushButton#segment:focus {{ border-color: #9a87e5; }}
            QPushButton#choiceCard {{ background: {field}; border: 1px solid {border}; border-radius: 12px; padding: 0; text-align: left; }}
            QPushButton#choiceCard:hover {{ background: {hover}; border-color: #b8a8ef; }}
            QPushButton#choiceCard:checked {{ background: {selected}; border-color: {'#9274be' if dark else '#af98e9'}; }}
            QPushButton#choiceCard:focus {{ border: 1px solid {'#d1bafa' if dark else '#7e61cc'}; }}
            QLabel[role='optionTitle'] {{ font-size: 14px; font-weight: 600; color: {text}; }}
            QLabel[role='optionCaption'] {{ font-size: 11px; color: {muted}; }}
            QLabel[role='optionTitle'][chosen='true'] {{ color: {accent}; }}
            QComboBox {{ background: {field}; border: 1px solid {border}; border-radius: 11px; padding: 8px 38px 8px 14px; selection-background-color: {tint}; }}
            QComboBox:hover {{ background: {hover}; border-color: {'#635370' if dark else '#c5b7e8'}; }}
            QComboBox:on {{ border-color: #9a87e5; background: {surface}; }}
            QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right; width: 34px; border: 0; background: transparent; }}
            QComboBox::down-arrow {{ image: url("{arrow}"); width: 16px; height: 16px; }}
            QComboBox#scenario {{ background: {field}; font-size: 12px; border-color: transparent; min-height: 20px; }}
            QComboBox#scenario:focus {{ border-color: #9a87e5; }}
            QComboBox QAbstractItemView {{ background: {surface}; color: {text}; border: 1px solid {border}; border-radius: 11px; padding: 6px; outline: 0; selection-background-color: {tint}; selection-color: {accent}; }}
            QComboBox QAbstractItemView::item {{ min-height: 27px; padding: 7px 12px; border-radius: 7px; border: 1px solid transparent; }}
            QComboBox QAbstractItemView::item:hover {{ background: {field}; }}
            QComboBox QAbstractItemView::item:selected {{ background: {tint}; color: {accent}; }}
            QDialog, QMenu {{ background: {surface}; }}
            QMenu {{ border: 1px solid {border}; border-radius: 10px; padding: 6px; }}
            QMenu::item {{ padding: 10px 24px; border-radius: 6px; }}
            QMenu::item:selected {{ background: {tint}; color: {accent}; }}
            QProgressBar {{ border: none; background: {tint}; border-radius: 2px; }}
            QProgressBar::chunk {{ background: #9780ed; }}
            QSlider::groove:horizontal {{ height: 6px; background: {border}; border-radius: 3px; }}
            QSlider::sub-page:horizontal {{ background: #9780ed; border-radius: 3px; }}
            QSlider::handle:horizontal {{ width: 18px; margin: -7px 0; border-radius: 10px; background: {'#c8b9ff' if dark else '#ffffff'}; border: 2px solid #9276e8; }}
            QScrollBar:vertical {{ width: 8px; background: transparent; margin: 3px 0; }}
            QScrollBar::handle:vertical {{ background: {border}; border-radius: 4px; min-height: 28px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        """)
        self.update_button_icons()
        for control in self.findChildren(SegmentedChoice):
            control.set_theme(dark)
        for sketch in self.sketches:
            sketch.dark = dark
            sketch.update()


def main():
    if "--engine" in sys.argv:
        index = sys.argv.index("--engine")
        if index + 1 >= len(sys.argv) or sys.argv[index + 1] != "bridge":
            raise SystemExit("Unknown iPadHub engine")
        from .engines.bridge import run
        return run(sys.argv[1:index] + sys.argv[index + 2:])
    parser = argparse.ArgumentParser(description="iPadHub unified desktop application")
    parser.add_argument("--preview", action="store_true", help="start the in-memory preview")
    parser.add_argument("--data-dir", type=Path, help="independent data directory")
    parser.add_argument("--no-migrate", action="store_true", help="do not import legacy data")
    parser.add_argument("--smoke-test", type=Path, help="save a device-free startup report and exit")
    args = parser.parse_args()
    if args.smoke_test and (not args.data_dir or not args.no_migrate):
        parser.error("--smoke-test requires --data-dir and --no-migrate")
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("iPadHub.Preview.0.1" if args.preview else "iPadHub.App.0.1")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("iPadHub Preview" if args.preview else "iPadHub")
    app.setOrganizationName("iPadHub")
    app.setStyle("Fusion")
    app.setQuitOnLastWindowClosed(False)
    # Neither identity overlaps either old application; tests isolate their data root.
    instance_name = ("iPadHub.Preview." if args.preview else "iPadHub.App.") + hashlib.sha256(str(args.data_dir or Path.home()).encode()).hexdigest()[:16]
    mutex = None
    kernel = None
    if sys.platform == "win32":
        # Windows named pipes allow multiple listeners. A mutex makes creation atomic.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.CreateMutexW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        mutex = kernel.CreateMutexW(None, False, "Local\\" + instance_name)
        last_error = ctypes.get_last_error()
        if not mutex:
            QMessageBox.warning(None, "无法打开 iPadHub", "无法取得应用的单实例标识，请稍后重试。")
            return 2
        if last_error == 183:  # ERROR_ALREADY_EXISTS
            kernel.CloseHandle(mutex)
            # The first instance may still be constructing its activation pipe.
            for _ in range(30):
                peer = QLocalSocket()
                peer.connectToServer(instance_name)
                if peer.waitForConnected(100):
                    peer.disconnectFromServer()
                    return 0
                time.sleep(0.1)
            QMessageBox.information(None, "iPadHub 正在启动", "应用窗口尚未响应，请稍后从托盘打开。")
            return 2
    server = QLocalServer(app)
    if not server.listen(instance_name):
        if mutex:
            kernel.CloseHandle(mutex)
        QMessageBox.warning(None, "无法打开 iPadHub", "另一个实例可能正在启动，请稍后重试。")
        return 2
    if args.preview:
        window = HubWindow()
    else:
        from .live_window import LiveHubWindow
        from .storage import HubStorage
        window = LiveHubWindow(HubStorage(args.data_dir), migrate=not args.no_migrate)
    def activate_existing():
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            connection.disconnectFromServer()
            connection.deleteLater()
        window.show_window()
    server.newConnection.connect(activate_existing)
    available = app.primaryScreen().availableGeometry()
    window.resize(min(window.width(), available.width() - 40), min(window.height(), available.height() - 40))
    window.show()
    if args.smoke_test:
        import json
        from PySide6.QtGui import QFontDatabase, QFontMetrics
        if app.platformName() == "offscreen":
            for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "segoeuib.ttf"):
                font_path = Path("C:/Windows/Fonts") / name
                if font_path.exists():
                    QFontDatabase.addApplicationFont(str(font_path))
            app.setFont(QFont("Microsoft YaHei UI", 10))
        def smoke():
            path = args.smoke_test.resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            screenshot = path.with_suffix(".png")
            has_engine = bool(getattr(window, "runtime", None) and window.runtime.engine)
            report = dict(ok=not has_engine and window.pages.count() == 5,
                          pages=window.pages.count(), title=window.windowTitle(),
                          engine_started=has_engine, migration_performed=False,
                          screenshot_saved=window.grab().save(str(screenshot)),
                          chinese_font=QFontMetrics(app.font()).inFont("中"))
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            window.quit_app()
        QTimer.singleShot(800, smoke)
    try:
        return app.exec()
    finally:
        server.close()
        if mutex:
            kernel.CloseHandle(mutex)


if __name__ == "__main__":
    raise SystemExit(main())
