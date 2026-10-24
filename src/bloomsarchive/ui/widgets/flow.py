"""A layout that places widgets left to right and wraps onto new lines.

Rows of buttons that fit at 100 % text size can be too wide at 130 % or in a
narrow window; in a flow layout they wrap instead of squashing or pushing the
page wider than the window.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    def __init__(self, parent: QWidget | None = None, spacing: int = 8, line_spacing: int | None = None):
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._h = spacing
        self._v = spacing if line_spacing is None else line_spacing
        self.setContentsMargins(0, 0, 0, 0)

    # -- QLayout plumbing
    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._place(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def sizeHint(self) -> QSize:
        """One line, as wide as everything side by side."""
        m = self.contentsMargins()
        shown = [i for i in self._items if not i.isEmpty()]
        w = sum(i.sizeHint().width() for i in shown) + self._h * max(0, len(shown) - 1)
        h = max((i.sizeHint().height() for i in shown), default=0)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self) -> QSize:
        """As narrow as the widest single item: everything else can wrap."""
        m = self.contentsMargins()
        size = QSize()
        for i in self._items:
            if not i.isEmpty():
                size = size.expandedTo(i.minimumSize())
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    # -- placement
    def _place(self, rect: QRect, apply: bool) -> int:
        m = self.contentsMargins()
        area = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line_h = area.x(), area.y(), 0
        line: list[tuple[QLayoutItem, QSize]] = []

        def flush() -> None:
            # Centre each item vertically within its line.
            for it, sz in line:
                if apply:
                    pos = it.geometry().topLeft()
                    it.setGeometry(QRect(QPoint(pos.x(), y + (line_h - sz.height()) // 2), sz))

        for item in self._items:
            if item.isEmpty():
                continue
            sz = item.sizeHint().boundedTo(QSize(area.width(), 10_000)).expandedTo(item.minimumSize())
            if line and x + sz.width() > area.right() + 1:
                flush()
                line = []
                x = area.x()
                y += line_h + self._v
                line_h = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), sz))
            line.append((item, sz))
            x += sz.width() + self._h
            line_h = max(line_h, sz.height())
        flush()
        return y + line_h - rect.y() + m.bottom()


def flow_widget(spacing: int = 8) -> tuple[QWidget, FlowLayout]:
    """A plain widget holding a FlowLayout, sized height-for-width."""
    w = QWidget()
    w.setProperty("role", "plain")
    lay = FlowLayout(w, spacing)
    sp = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    sp.setHeightForWidth(True)
    w.setSizePolicy(sp)
    return w, lay
