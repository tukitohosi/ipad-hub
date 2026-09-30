"""Generate the application's existing H mark for the Windows executable."""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

app = QApplication([])
pix = QPixmap(256, 256)
pix.fill(Qt.GlobalColor.transparent)
painter = QPainter(pix)
painter.setRenderHint(QPainter.RenderHint.Antialiasing)
painter.setPen(Qt.PenStyle.NoPen)
painter.setBrush(QColor("#635bd9"))
painter.drawRoundedRect(8, 8, 240, 240, 64, 64)
painter.setPen(QColor("white"))
painter.setFont(QFont("Segoe UI", 112, QFont.Weight.Bold))
painter.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "H")
painter.end()
if not pix.save(str(Path(__file__).with_name("iPadHub.ico")), "ICO"):
    raise RuntimeError("Could not write iPadHub.ico")
