"""The pill: recording status over other apps, bottom centre of the screen.

It never takes focus and lets clicks through, like the GNOME Shell pill.
"""
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import qt_design as d


class Pill(QWidget):
    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAccessibleName("Dogspeak status")
        self.state = self.title = self.hint = ""
        self.title_font = QFont(self.font())
        self.title_font.setPointSize(d.TEXT_TITLE)
        self.title_font.setBold(True)
        self.hint_font = QFont(self.font())
        self.hint_font.setPointSize(d.TEXT_BODY + 1)

    def show_state(self, state, title, hint):
        self.state, self.title, self.hint = state, title, hint
        self._place()
        self.update()
        if not self.isVisible():
            self.show()
        self.raise_()

    def hide_state(self):
        self.state = self.title = self.hint = ""
        self.hide()

    def _size(self):
        title = QFontMetrics(self.title_font).horizontalAdvance(self.title)
        hint = QFontMetrics(self.hint_font).horizontalAdvance(self.hint) if self.hint else 0
        height = max(QFontMetrics(self.title_font).height(), d.PILL_DOT) + 2 * d.PILL_PADDING_Y
        width = (2 * d.PILL_PADDING_X + d.PILL_DOT + d.PILL_GAP + title
                 + (d.PILL_GAP + hint if hint else 0))
        return width, height

    def _place(self):
        width, height = self._size()
        screen = (QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())
        area = screen.availableGeometry()
        self.setFixedSize(width, height)
        self.move(QPoint(area.center().x() - width // 2,
                         area.bottom() - d.PILL_FROM_BOTTOM - height))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(*d.PILL_BORDER), 1))
        painter.setBrush(QColor(*d.PILL_BACKGROUND))
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
        x = d.PILL_PADDING_X
        middle = self.height() / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(d.STATE_COLOURS.get(self.state, d.STATE_COLOURS["info"])))
        painter.drawEllipse(QRectF(x, middle - d.PILL_DOT / 2, d.PILL_DOT, d.PILL_DOT))
        x += d.PILL_DOT + d.PILL_GAP
        painter.setFont(self.title_font)
        painter.setPen(QColor(*d.PILL_TEXT))
        width = QFontMetrics(self.title_font).horizontalAdvance(self.title)
        painter.drawText(QRectF(x, 0, width + 1, self.height()), Qt.AlignVCenter, self.title)
        if self.hint:
            x += width + d.PILL_GAP
            painter.setFont(self.hint_font)
            painter.setPen(QColor(*d.PILL_HINT))
            painter.drawText(QRectF(x, 0, self.width() - x, self.height()), Qt.AlignVCenter,
                             self.hint)
