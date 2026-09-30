"""Run Qt interaction/layout checks and save screenshots without device access."""
import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtWidgets import QApplication, QPushButton

from ipadhub.app import HubWindow
from ipadhub.model import Mode, State


app = QApplication([])
app.setStyle("Fusion")
# Qt's Windows offscreen plugin does not populate the system font database.
# Load existing OS fonts for meaningful screenshots; do not bundle them.
for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "segoeuib.ttf"):
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / name
    if font_path.exists():
        QFontDatabase.addApplicationFont(str(font_path))
app.setFont(QFont("Microsoft YaHei UI", 10))
if not QFontMetrics(app.font()).inFont("中"):
    raise RuntimeError("Chinese font missing; screenshots would not be valid visual QA.")
window = HubWindow()
window.tray.hide()
window.show()
app.processEvents()
out = ROOT / os.environ.get("IPADHUB_QA_OUTPUT", "artifacts/preview")
out.mkdir(parents=True, exist_ok=True)
scale = os.environ.get("QT_SCALE_FACTOR", "1")
results = []


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    results.append(name)


def capture(name):
    app.processEvents()
    pix = window.grab()
    path = out / f"{name}-scale-{scale}.png"
    check(f"saved {path.name}", pix.save(str(path)))


window.set_theme("浅色")
capture("home")
for i, name in enumerate(["home", "display", "bridge", "tools", "settings"]):
    window.navigate(i)
    app.processEvents()
    area = window.pages.widget(i)
    check(f"{name} no horizontal scrolling", area.horizontalScrollBar().maximum() == 0)
    capture(name)
window.navigate(1)
window.transport_choice.buttons[1].click()
check("transport segment selects USB", window.transport_choice.currentText() == "USB")
QTest.keyClick(window.transport_choice.buttons[1], Qt.Key.Key_Right)
check("transport arrow key selects Wi-Fi", window.transport_choice.currentText() == "Wi-Fi")
window.transport_choice.setCurrentText("自动")
window.quality_choice.buttons[1].click()
check("quality cards select clearly", window.quality_choice.currentText() == "清晰" and sum(b.isChecked() for b in window.quality_choice.buttons) == 1)
window.quality_choice.setCurrentText("均衡")
window.navigate(2)
window.position_choice.buttons[0].click()
check("position segment selects left", window.position_choice.currentText() == "电脑左侧")
window.position_choice.setCurrentText("电脑右侧")
window.navigate(4)
window.theme_box.buttons[2].click()
check("theme segment applies dark mode", window.dark and window.theme_choice == "深色")
capture("settings-dark")
window.theme_box.buttons[1].click()
check("theme segment applies light mode", not window.dark)
window.scenario_box.showPopup()
app.processEvents()
popup = window.scenario_box.view().window()
check("dropdown menu renders", popup.grab().save(str(out / f"options-menu-scale-{scale}.png")))
QTest.keyClick(window.scenario_box.view(), Qt.Key.Key_Down)
QTest.keyClick(window.scenario_box.view(), Qt.Key.Key_Return)
check("dropdown keyboard selection", window.scenario_box.currentText() == "缺少条件")
window.scenario_box.setCurrentText("正常连接")
window.navigate(1)
QTest.mouseClick(window.mode_controls[Mode.DISPLAY]["start"], Qt.MouseButton.LeftButton)
check("display connects", window.session.state == State.CONNECTING)
QTest.qWait(1150)
check("display active", window.session.state == State.ACTIVE)
window.navigate(2)
QTest.mouseClick(window.mode_controls[Mode.BRIDGE]["start"], Qt.MouseButton.LeftButton)
check("switch stops old mode first", window.session.state == State.STOPPING and window.session.owner == Mode.DISPLAY)
QTest.qWait(1150)
check("new mode only after release", window.session.state == State.CONNECTING and window.session.owner == Mode.BRIDGE)
QTest.qWait(1150)
check("bridge active", window.session.state == State.ACTIVE)
window.scenario_box.setCurrentText("停止失败")
window.navigate(1)
window.mode_controls[Mode.DISPLAY]["start"].click()
QTest.qWait(1150)
check("failed stop blocks start", window.session.release_blocked and not window.mode_controls[Mode.DISPLAY]["start"].isEnabled())
capture("stop-failed")
window.mode_controls[Mode.DISPLAY]["recovery"].click()
QTest.qWait(1150)
check("recovery releases owner", window.session.owner is None and not window.session.release_blocked)
window.scenario_box.setCurrentText("缺少条件")
window.mode_controls[Mode.DISPLAY]["start"].click()
check("missing prerequisites", window.session.state == State.MISSING)
capture("missing")
window.scenario_box.setCurrentText("连接失败")
window.mode_controls[Mode.DISPLAY]["start"].click()
QTest.qWait(1150)
check("connection failure displayed", window.session.state == State.FAILED)
capture("connection-failed")
window.scenario_box.setCurrentText("正常连接")
window.mode_controls[Mode.DISPLAY]["start"].click()
QTest.keyClick(window, Qt.Key.Key_Escape)
QTest.qWait(1150)
check("escape cancels and ignores stale event", window.session.owner is None and window.session.state == State.READY)
window.navigate(3)
window.tool_buttons[1].click()
check("flash blocks start", window.session.busy_tool and not window.mode_controls[Mode.BRIDGE]["start"].isEnabled())
QTest.qWait(2550)
check("flash demo finishes", not window.session.busy_tool)
window.set_theme("深色")
window.navigate(0)
capture("home-dark")
window.scenario_box.setCurrentText("长设备名")
window.resize(820, 520)
for i in range(5):
    window.navigate(i)
    app.processEvents()
    area = window.pages.widget(i)
    check(f"minimum size page {i} no horizontal overflow", area.horizontalScrollBar().maximum() == 0)
    capture(f"minimum-page-{i}")
window.navigate(2)
advanced = window.pages.currentWidget().findChild(QPushButton, "disclosure")
advanced.click()
app.processEvents()
capture("bridge-advanced-minimum-dark")
QTest.keyClick(window, Qt.Key.Key_1, Qt.KeyboardModifier.AltModifier)
check("Alt+1 navigates home", window.pages.currentIndex() == 0)
QTest.keyClick(window, Qt.Key.Key_Tab)
check("keyboard focus exists", app.focusWidget() is not None)
report = {"scale": scale, "passed": len(results), "checks": results,
          "scope": "Simulated UI only; no hardware/real migration/engine acceptance."}
(out / f"checks-scale-{scale}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(report, ensure_ascii=False))
window.quit_app()
