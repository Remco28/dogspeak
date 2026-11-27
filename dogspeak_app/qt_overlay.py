"""The pill: recording status over other apps, bottom centre of the screen.

It never takes focus and lets clicks through, like the GNOME Shell pill.
Shows an ON AIR recording progress bar against the 1:55 single-take cap.
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
        self.setWindowTitle("Dogspeak status")
        self.setAccessibleName("Dogspeak status")
        self.state = self.title = self.hint = ""
        self.progress = None
        self.warn = False
        self.title_font = QFont(self.font())
        self.title_font.setPointSize(d.TEXT_TITLE)
        self.title_font.setBold(True)
        self.hint_font = QFont(self.font())
        self.hint_font.setPointSize(d.TEXT_BODY + 1)

    def show_state(self, state, title, hint, progress=None, warn=False):
        self.state, self.title, self.hint = state, title, hint
        self.progress = None if progress is None else max(0.0, min(1.0, float(progress)))
        self.warn = bool(warn)
        self.setAccessibleDescription(f"{title} · {hint}" if hint else title)
        self._place()
        self.update()
        if not self.isVisible():
            self.show()
        self.raise_()

    def hide_state(self):
        self.state = self.title = self.hint = ""
        self.progress = None
        self.warn = False
        self.hide()

    def _size(self):
        title = QFontMetrics(self.title_font).horizontalAdvance(self.title)
        hint = QFontMetrics(self.hint_font).horizontalAdvance(self.hint) if self.hint else 0
        bar_extra = (d.PILL_BAR_HEIGHT + 6) if self.progress is not None else 0
        height = (max(QFontMetrics(self.title_font).height(), d.PILL_DOT)
                  + 2 * d.PILL_PADDING_Y + bar_extra)
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
        radius = min(rect.height() / 2, 18.0)
        painter.setPen(QPen(QColor(*d.PILL_BORDER), 1))
        painter.setBrush(QColor(*d.PILL_BACKGROUND))
        painter.drawRoundedRect(rect, radius, radius)

        bar_extra = (d.PILL_BAR_HEIGHT + 6) if self.progress is not None else 0
        content_height = self.height() - bar_extra
        x = d.PILL_PADDING_X
        middle = content_height / 2
        dot_key = "nearcap" if (self.state == "recording" and self.warn) else self.state
        dot_colour = QColor(d.STATE_COLOURS.get(dot_key, d.STATE_COLOURS["info"]))
        painter.setPen(Qt.NoPen)
        painter.setBrush(dot_colour)
        painter.drawEllipse(QRectF(x, middle - d.PILL_DOT / 2, d.PILL_DOT, d.PILL_DOT))
        x += d.PILL_DOT + d.PILL_GAP
        painter.setFont(self.title_font)
        painter.setPen(QColor(*d.PILL_TEXT))
        width = QFontMetrics(self.title_font).horizontalAdvance(self.title)
        painter.drawText(QRectF(x, 0, width + 1, content_height), Qt.AlignVCenter, self.title)
        if self.hint:
            x += width + d.PILL_GAP
            painter.setFont(self.hint_font)
            painter.setPen(QColor(*d.PILL_HINT))
            painter.drawText(QRectF(x, 0, self.width() - x, content_height), Qt.AlignVCenter,
                             self.hint)

        if self.progress is not None:
            bar_x = d.PILL_PADDING_X
            bar_w = self.width() - 2 * d.PILL_PADDING_X
            bar_y = self.height() - d.PILL_PADDING_Y - d.PILL_BAR_HEIGHT + 2
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255, 36))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, bar_w, d.PILL_BAR_HEIGHT), 1.5, 1.5)
            fill_w = max(4.0, bar_w * self.progress) if self.progress > 0 else 0.0
            if fill_w > 0:
                painter.setBrush(dot_colour)
                painter.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, d.PILL_BAR_HEIGHT), 1.5, 1.5)
