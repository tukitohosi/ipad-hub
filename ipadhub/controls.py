"""Small native Qt controls for the iPadHub preview."""
from PySide6.QtCore import QByteArray, Qt, QSize, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


PATHS = {
    "home": '<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/>',
    "display": '<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8m-4-4v4"/>',
    "keyboard": '<rect x="2" y="5" width="20" height="14" rx="3"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M6 12h.01M10 12h.01M14 12h.01M18 12h.01M7 15h10"/>',
    "tools": '<path d="M14 4a6 6 0 0 0-7 7L3 15a3 3 0 0 0 4 4l4-4a6 6 0 0 0 8-7l-4 4-3-3 4-4z"/>',
    "settings": '<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="8" cy="6" r="2" fill="{bg}"/><circle cx="16" cy="12" r="2" fill="{bg}"/><circle cx="10" cy="18" r="2" fill="{bg}"/>',
    "arrow": '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    "play": '<path d="m8 5 11 7-11 7z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    "refresh": '<path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5"/>',
    "chevron": '<path d="m7 10 5 5 5-5"/>',
    "chevron-up": '<path d="m7 14 5-5 5 5"/>',
    "usb": '<path d="M12 21V3m-3 3 3-3 3 3M12 15l-5-4V8m5 4 5-4V6"/><circle cx="7" cy="6" r="2"/><path d="M15 3h4v3h-4z"/>',
    "wifi": '<path d="M2 8a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0m-11 4a6 6 0 0 1 8 0"/><circle cx="12" cy="20" r=".8"/>',
    "auto": '<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1 1m12 12 1 1M5 19l1-1M18 6l1-1"/>',
    "moon": '<path d="M20 15a9 9 0 0 1-11-11 9 9 0 1 0 11 11z"/>',
    "left": '<path d="M20 12H4m6-6-6 6 6 6"/>',
    "backup": '<path d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5"/>',
    "flash": '<path d="m13 2-9 12h7l-1 8 10-13h-7z"/>',
    "check": '<path d="m5 12 4 4L19 6"/>',
}


def icon(name, color="#6e748b", background="transparent"):
    paths = PATHS.get(name, PATHS["auto"]).replace("{bg}", background)
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>'
    # Two resolutions keep icons crisp at both desktop and high-DPI sizes.
    result = QIcon()
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    for size in (24, 48):
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        renderer.render(painter)
        painter.end()
        result.addPixmap(pix)
    return result


class ChoiceButton(QPushButton):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            owner = self.parentWidget()
            index = owner.buttons.index(self)
            step = 1 if event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down) else -1
            target = owner.buttons[(index + step) % len(owner.buttons)]
            target.click()
            target.setFocus(Qt.FocusReason.TabFocusReason)
            event.accept()
        else:
            super().keyPressEvent(event)


class SegmentedChoice(QWidget):
    currentTextChanged = Signal(str)

    def __init__(self, items, *, captions=None, glyphs=None):
        super().__init__()
        self.items = items
        self.buttons = []
        self.dark = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("choiceCards" if captions else "segmented")
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0) if captions else layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10 if captions else 4)
        for index, text in enumerate(items):
            b = ChoiceButton("" if captions else text, self)
            b.setObjectName("choiceCard" if captions else "segment")
            b.setAccessibleName(text + (f"，{captions[index]}" if captions else ""))
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(78 if captions else 36)
            b.setMinimumWidth(0)
            if glyphs:
                b.setProperty("glyph", glyphs[index])
                b.setIconSize(QSize(17, 17))
            if captions:
                inner = QVBoxLayout(b)
                inner.setContentsMargins(14, 11, 12, 11)
                inner.setSpacing(4)
                for content, role in ((text, "optionTitle"), (captions[index], "optionCaption")):
                    t = QLabel(content)
                    t.setProperty("role", role)
                    t.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
                    inner.addWidget(t)
            self.group.addButton(b, index)
            self.buttons.append(b)
            layout.addWidget(b, 1)
            b.toggled.connect(lambda _: self.update_icons())
        self.buttons[0].setChecked(True)
        self.group.idClicked.connect(lambda i: self.currentTextChanged.emit(self.items[i]))

    def currentText(self):
        return self.items[self.group.checkedId()]

    def setCurrentText(self, text):
        if text in self.items and text != self.currentText():
            self.buttons[self.items.index(text)].setChecked(True)
            self.currentTextChanged.emit(text)

    def set_theme(self, dark):
        self.dark = dark
        self.update_icons()

    def update_icons(self):
        for b in self.buttons:
            for label in b.findChildren(QLabel):
                label.setProperty("chosen", b.isChecked())
                label.style().unpolish(label)
                label.style().polish(label)
            glyph = b.property("glyph")
            if glyph:
                color = ("#c0b4ff" if self.dark else "#6551d4") if b.isChecked() else ("#b0b6c8" if self.dark else "#788096")
                b.setIcon(icon(glyph, color))
