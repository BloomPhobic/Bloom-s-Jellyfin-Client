"""A small message that appears at the bottom of a window and fades on its own."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget

from .. import theme


class Toast(QLabel):
    def __init__(self, host: QWidget):
        super().__init__(host)
        self.host = host
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        host.installEventFilter(self)
        self.hide()

    def show_message(self, text: str, error: bool = False, ms: int = 2800) -> None:
        border = theme.DANGER if error else theme.ACCENT
        self.setStyleSheet(
            f"background: {theme.RAISED}; color: {theme.TEXT}; border: 1px solid {border};"
            "border-radius: 10px; padding: 10px 18px; font-size: 14px;")
        self.setText(text)
        self._place()
        self.show()
        self.raise_()
        self._timer.start(ms)

    def _place(self) -> None:
        self.setMaximumWidth(min(560, self.host.width() - 40))
        self.adjustSize()
        self.move((self.host.width() - self.width()) // 2, self.host.height() - self.height() - 28)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if obj is self.host and ev.type() == QEvent.Type.Resize and self.isVisible():
            self._place()
        return False
