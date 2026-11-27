"""The draft editor for the Qt window: numbered lines, word wrap, moving lines.

Qt counts positions in UTF-16 code units, and the rest of Dogspeak counts
Python characters. to_qt() and from_qt() convert, so an emoji never shifts a
transcript into the middle of a word.
"""
from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QTextBlockFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QWidget

from . import qt_design as d
from .controller import move_lines


def to_qt(text, offset):
    """Python character offset -> Qt position."""
    return len(text[:offset].encode("utf-16-le")) // 2


def from_qt(text, position):
    """Qt position -> Python character offset."""
    units = 0
    for index, char in enumerate(text):
        if units >= position:
            return index
        units += 2 if ord(char) > 0xFFFF else 1
    return len(text)


def _word(char):
    return char.isalnum() or char in "_'’"


def insertion_offset(text, start, end):
    """Where a transcript goes for a selection [start, end): after it, and after a word
    the cursor sits inside."""
    if 0 < end < len(text) and _word(text[end - 1]) and _word(text[end]):
        while end < len(text) and _word(text[end]):
            end += 1
    return end


class _Gutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)


class DraftEditor(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        font = self.font()
        font.setPointSize(d.TEXT_EDITOR)
        self.setFont(font)
        self.gutter_font = QFont("Consolas", d.TEXT_BODY)
        self.gutter_font.setStyleHint(QFont.StyleHint.Monospace)
        self.setAccessibleName("Draft")
        self.gutter = _Gutter(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_line)
        self._update_gutter_width()
        self._highlight_line()

    def setDocument(self, document):
        super().setDocument(document)
        self.document().setDocumentMargin(d.SPACE_S)
        self._update_gutter_width()
        self._highlight_line()

    def format_document(self, document):
        """Line spacing for a new draft's document."""
        cursor = QTextCursor(document)
        cursor.select(QTextCursor.Document)
        block = QTextBlockFormat()
        block.setLineHeight(d.EDITOR_LINE_SPACING, 1)  # QTextBlockFormat.ProportionalHeight
        cursor.mergeBlockFormat(block)

    # Line numbers: one per paragraph, so a wrapped paragraph counts once.
    def gutter_width(self):
        digits = len(str(max(1, self.blockCount())))
        return d.SPACE_M + self.fontMetrics().horizontalAdvance("9") * max(2, digits)

    def _side_inset(self):
        """Center the measure on wide windows; stay compact on narrow splits."""
        available = self.contentsRect().width()
        gutter = self.gutter_width() + d.SPACE_S
        target = d.EDITOR_MAX_WIDTH + gutter + 2 * d.SPACE_L
        if available > target:
            return (available - (d.EDITOR_MAX_WIDTH + gutter)) // 2
        return d.SPACE_M

    def _update_gutter_width(self, *_):
        inset = self._side_inset()
        left = inset + self.gutter_width() + d.SPACE_S
        self.setViewportMargins(left, d.EDITOR_MARGIN, inset + d.SPACE_S, d.EDITOR_MARGIN)
        area = self.contentsRect()
        self.gutter.setGeometry(QRect(area.left() + inset, area.top() + d.EDITOR_MARGIN,
                                      self.gutter_width(),
                                      max(0, area.height() - 2 * d.EDITOR_MARGIN)))

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_gutter_width()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._highlight_line()

    def paint_gutter(self, event):
        painter = QPainter(self.gutter)
        painter.setFont(self.gutter_font)
        pal = d.palette(d.is_dark(self))
        muted = QColor(pal["muted"])
        active = QColor(pal["ink"])
        current = self.textCursor().blockNumber()
        block = self.firstVisibleBlock()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            bottom = top + round(self.blockBoundingRect(block).height())
            if block.isVisible() and bottom >= event.rect().top():
                number = block.blockNumber()
                painter.setPen(active if number == current else muted)
                painter.drawText(0, top, self.gutter.width() - d.SPACE_S,
                                 self.fontMetrics().height(), Qt.AlignRight, str(number + 1))
            block = block.next()
            top = bottom

    def _highlight_line(self):
        selection = QTextEdit.ExtraSelection()
        pal = d.palette(d.is_dark(self))
        selection.format.setBackground(QColor(pal["line_highlight"]))
        selection.format.setProperty(QTextFormat.FullWidthSelection, True)
        selection.cursor = self.textCursor()
        selection.cursor.clearSelection()
        self.setExtraSelections([selection])
        self.gutter.update()

    # Text access in Python offsets.
    def text(self):
        return self.toPlainText()

    def insertion_point(self):
        text = self.text()
        cursor = self.textCursor()
        return insertion_offset(text, from_qt(text, cursor.selectionStart()),
                                from_qt(text, cursor.selectionEnd()))

    def move_lines(self, down):
        """Alt+Up/Down: a paragraph swaps with the next; blank lines stay put."""
        if self.isReadOnly():
            return
        text = self.text()
        lines = text.split("\n")
        cursor = self.textCursor()
        anchor, position = cursor.anchor(), cursor.position()
        doc = self.document()
        start, end = sorted((anchor, position))
        first = doc.findBlock(start).blockNumber()
        last_block = doc.findBlock(end)
        last = last_block.blockNumber()
        if end == last_block.position() and last > first:
            last -= 1  # a selection ending at a line's start doesn't include that line
        moved = move_lines(lines, first, last, down)
        if moved is None:
            return
        new, first, new_first = moved

        def place(qt_position):
            block = doc.findBlock(qt_position)
            return block.blockNumber() - first + new_first, qt_position - block.position()
        marks = [place(anchor), place(position)]
        if new != lines:
            top = next(i for i, (a, b) in enumerate(zip(lines, new)) if a != b)
            bottom = len(lines) - next(
                i for i, (a, b) in enumerate(zip(reversed(lines), reversed(new))) if a != b)
            edit = QTextCursor(doc)
            edit.beginEditBlock()
            edit.setPosition(doc.findBlockByNumber(top).position())
            stop = doc.findBlockByNumber(bottom - 1)
            edit.setPosition(stop.position() + stop.length() - 1, QTextCursor.KeepAnchor)
            edit.insertText("\n".join(new[top:bottom]))
            edit.endEditBlock()

        def position_of(line, column):
            if line >= len(new):
                return doc.characterCount() - 1
            block = doc.findBlockByNumber(max(0, line))
            return block.position() + min(column, block.length() - 1)
        cursor = self.textCursor()
        cursor.setPosition(position_of(*marks[0]))
        cursor.setPosition(position_of(*marks[1]), QTextCursor.KeepAnchor)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
