"""Dogspeak's window on Windows, in Qt. Run from the project environment:

    .venv\\Scripts\\pythonw.exe -m dogspeak_app.qt_app [--toggle-recording] [--import-audio FILE]

Field Recorder + ON AIR Focused Instrument layout: slim header with the
puppy-at-the-microphone mark, eye-level Hero Instrument Strip with a 1:55
tape meter, centered draft sheet, and a bottom Instrument Dock that wraps
cleanly down to 440 px wide.
"""
import argparse
import getpass
import json
import logging
import logging.handlers
import os
import signal
import sys
import time
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QAction, QColor, QGuiApplication, QIcon, QKeySequence, QPainter, QPixmap,
    QTextCursor, QTextDocument)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextDocumentLayout,
    QPushButton, QSizePolicy, QSystemTrayIcon, QToolButton, QVBoxLayout, QWidget)

from . import hotkeys, paths
from . import qt_design as d
from .controller import Controller, background
from .qt_cleanup import CleanupView, SettingsDialog
from .qt_editor import DraftEditor, to_qt
from .qt_overlay import Pill
from .ui_values import (
    FEEDBACK_SECONDS,
    MAX_RECORDING_LABEL,
    MAX_RECORDING_SECONDS,
    NEAR_CAP_SECONDS,
    format_cap_readout,
    format_duration,
)

APP_ID = 'io.github.Remco28.Dogspeak'
APP_MUTEX = 'io.github.Remco28.Dogspeak.Running'
log = logging.getLogger('dogspeak')
IN_WINDOW_SHORTCUT = 'Ctrl+Shift+R'
AUDIO_FILTER = 'Audio files (*.wav *.flac *.ogg *.mp3);;All files (*)'


def app_icon():
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addFile(str(paths.ROOT / 'data' / 'icons' / 'hicolor' / f'{size}x{size}' / 'apps'
                         / f'{APP_ID}.png'))
    return icon


def logo_pixmap(size):
    path = paths.ROOT / 'data' / 'icons' / 'hicolor' / '128x128' / 'apps' / f'{APP_ID}.png'
    pix = QPixmap(str(path))
    if pix.isNull():
        return pix
    return pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _repolish(widget):
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class TapeMeter(QWidget):
    """6 px ruled hardware progress rail for 1:55 recording cap & busy states."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(d.METER_HEIGHT)
        self.mode = 'hidden'
        self.fraction = 0.0
        self.phase = 0.0
        self.hide()

    def set_state(self, mode, fraction=None, phase=0.0):
        self.mode = mode
        if fraction is not None:
            self.fraction = max(0.0, min(1.0, float(fraction)))
        self.phase = phase % 1.0
        self.setVisible(mode != 'hidden')
        self.update()

    def paintEvent(self, _event):
        if self.mode == 'hidden':
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pal = d.palette(d.is_dark(self))
        w, h = float(self.width()), float(self.height())
        if w <= 0 or h <= 0:
            return
        track = QColor(pal['ink'])
        track.setAlpha(30)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, w, h), 2.5, 2.5)

        colour_key = {
            'recording': 'accent',
            'nearcap': 'warn',
            'loading': 'warn',
            'transcribing': 'local',
            'llm': 'ext',
        }.get(self.mode, 'accent')
        fill = QColor(pal[colour_key])
        painter.setBrush(fill)
        if self.mode in ('recording', 'nearcap'):
            fill_w = max(4.0, w * self.fraction) if self.fraction > 0 else 0.0
            if fill_w > 0:
                painter.drawRoundedRect(QRectF(0, 0, fill_w, h), 2.5, 2.5)
        else:
            # Indeterminate stepped bar, no animations — updates on the 200 ms tick.
            seg_w = max(44.0, w * 0.34)
            x = (w + seg_w) * self.phase - seg_w
            painter.setClipRect(QRectF(0, 0, w, h))
            painter.drawRoundedRect(QRectF(x, 0, seg_w, h), 2.5, 2.5)
            painter.setClipping(False)

        # Broadcast scale tick dividers (5 equal segments along the 1:55 track).
        tick = QColor(pal['canvas'])
        tick.setAlpha(185)
        painter.setBrush(tick)
        for i in range(1, 5):
            tx = w * (i / 5.0)
            painter.drawRect(QRectF(tx - 1.0, 0, 2.0, h))


class Dispatcher(QObject):
    """dispatch(fn, *args): run fn on the UI thread, from any thread."""

    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.QueuedConnection)

    def _run(self, item):
        fn, args = item
        fn(*args)

    def __call__(self, fn, *args):
        self.call.emit((fn, args))


class DraftsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.main = window  # an attribute called window would hide QWidget.window()
        self.setWindowTitle('Open a draft')
        self.resize(460, 440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(d.SPACE_L, d.SPACE_L, d.SPACE_L, d.SPACE_L)
        self.rows = QListWidget()
        self.rows.setAccessibleName('Drafts')
        self.rows.setSpacing(2)
        self.rows.itemActivated.connect(self._open)
        self.rows.itemClicked.connect(self._open)
        layout.addWidget(self.rows)
        for draft in window.controller.store.list_drafts():
            self._add(draft)

    def _add(self, draft):
        item = QListWidgetItem(self.rows)
        item.setData(Qt.UserRole, draft.id)
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(d.SPACE_S, d.SPACE_S, d.SPACE_XS, d.SPACE_S)
        text = QVBoxLayout()
        text.setSpacing(2)
        title = QLabel(draft.title or 'Untitled')
        title.setTextFormat(Qt.PlainText)
        bold = title.font()
        bold.setBold(True)
        title.setFont(bold)
        preview = QLabel(draft.content.replace('\n', ' ')[:100] or 'Empty draft')
        preview.setTextFormat(Qt.PlainText)
        preview.setProperty('muted', True)
        text.addWidget(title)
        text.addWidget(preview)
        line.addLayout(text, 1)
        delete = QToolButton()
        delete.setObjectName('flatBtn')
        delete.setText('Delete')
        delete.setToolTip('Delete draft')
        delete.setAccessibleName(f'Delete {draft.title or "Untitled"}')
        delete.setCursor(Qt.PointingHandCursor)
        delete.clicked.connect(lambda _=False, dr=draft, it=item: self.main.confirm_delete(dr, it, self))
        line.addWidget(delete)
        item.setSizeHint(row.sizeHint())
        self.rows.setItemWidget(item, row)

    def _open(self, item):
        draft_id = item.data(Qt.UserRole)
        self.main.perform(lambda: self.main.controller.open_draft(draft_id))
        self.accept()
        self.main.editor.setFocus()


class ScratchpadWindow(QMainWindow):
    def __init__(self, controller, sources_provider, cleanup, hotkeys_factory=None, tray=True):
        super().__init__()
        self.controller, self.cleanup = controller, cleanup
        self.setWindowTitle('Dogspeak')
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(d.MIN_WINDOW_WIDTH)
        self.resize(d.DEFAULT_WINDOW_WIDTH, d.DEFAULT_WINDOW_HEIGHT)
        self.rendering = self.closing = False
        self._narrow = None
        self._dark_theme = d.is_dark(QApplication.instance())
        self._updating_theme = False
        self.last_edit, self.typing_pause = float('-inf'), 1.0
        self.documents = {}
        self.displayed_draft = None
        self.drafts_dialog = self.settings_dialog = None
        self._tray_hint_shown = False
        self.notice_timer = QTimer(self, singleShot=True, interval=FEEDBACK_SECONDS * 1000)
        self.save_timer = QTimer(self, singleShot=True, interval=500)
        self.save_timer.timeout.connect(self._autosave)

        self.editor = DraftEditor()
        self.cleanup_view = CleanupView(self, cleanup)
        self._build_actions()

        central = QWidget()
        central.setObjectName('central')
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        stage = QWidget()
        stage.setObjectName('editorStage')
        stage_col = QVBoxLayout(stage)
        stage_col.setContentsMargins(0, 0, 0, 0)
        stage_col.setSpacing(0)
        stage_col.addWidget(self._build_hero_strip())
        stage_col.addWidget(self.cleanup_view.banner)
        stage_col.addWidget(self.editor, 1)
        stage_col.addWidget(self._build_notice())
        stage_col.addWidget(self._build_dock())

        body.addWidget(stage, 1)
        body.addWidget(self.cleanup_view.panel)
        column.addLayout(body, 1)
        self.setCentralWidget(central)

        self.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self._editor_menu)

        c = controller
        c.on_draft = self.render_draft
        c.on_status = self._status_changed
        self._logged_status = None
        c.insertion_point = self._insertion_point
        c.hold_result = self._hold_result
        c.focused = lambda: self.isVisible() and self.isActiveWindow()
        c.on_transcript = self._copy_transcript
        QApplication.instance().installEventFilter(self)

        self.pill = Pill()
        self._pill_shown = self._pill_keys = None
        self._pill_state = ''
        self._copied = self._announce_ready = False
        self.pill_timer = QTimer(self, singleShot=True)
        self.pill_timer.timeout.connect(self._hide_overlay)
        self.tray = self._build_tray() if tray and QSystemTrayIcon.isSystemTrayAvailable() else None
        self.hotkeys = (hotkeys_factory or (lambda _w: hotkeys.NoHotkeys()))(self)
        self.shortcut_problem = self._register_shortcut(
            c.store.get_setting('hotkey', hotkeys.DEFAULT) or hotkeys.DEFAULT)
        if self.shortcut_problem:
            log.warning('shortcut: %s', self.shortcut_problem)

        self._apply_responsive_layout(self.width())
        self.render_draft(c.draft)
        self.cleanup_view.refresh()
        self.tick_timer = QTimer(self, interval=200)
        self.tick_timer.timeout.connect(c.tick)
        self.tick_timer.start()

        def discover():  # listing devices may block; never on the UI thread
            try:
                sources = sources_provider()
            except Exception:
                sources = []
            c.dispatch(self._set_sources, sources)
        c.spawn(discover)

    # Building
    def _build_actions(self):
        self.actions_by_name = {}
        for name, text, slot, keys in [
            ('new-draft', 'New draft', lambda: self.controller.new_draft(), 'Ctrl+N'),
            ('open-draft', 'Open a draft…', self.show_drafts, 'Ctrl+O'),
            ('copy-all', 'Copy all', self.copy_all, 'Ctrl+Shift+C'),
            ('copy-clear', 'Copy && Clear', self.copy_and_clear, 'Ctrl+Shift+X'),
            ('clear', 'Clear', self.clear_text, None),
            ('toggle-recording', 'Record', self.toggle_recording, IN_WINDOW_SHORTCUT),
            ('clean-up', 'Clean up', lambda: self.cleanup.start(), 'Ctrl+Shift+L'),
            ('settings', 'Settings…', self.show_settings, 'Ctrl+,'),
            ('move-line-up', 'Move line up', lambda: self.editor.move_lines(False), 'Alt+Up'),
            ('move-line-down', 'Move line down', lambda: self.editor.move_lines(True), 'Alt+Down'),
            ('import-audio', 'Import audio file…', self.import_audio_dialog, None),
            ('quit', 'Quit', self.quit_session, 'Ctrl+Q'),
        ]:
            action = QAction(text, self)
            if keys:
                action.setShortcut(QKeySequence(keys))
            action.triggered.connect(lambda _=False, slot=slot: self.perform(slot))
            self.addAction(action)
            self.actions_by_name[name] = action

    def _tool(self, text, tooltip, action=None, slot=None, flat=False):
        button = QToolButton()
        if flat:
            button.setObjectName('flatBtn')
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAccessibleName(text.replace('&&', 'and'))
        button.setCursor(Qt.PointingHandCursor)
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        button.clicked.connect(slot or (lambda: self.actions_by_name[action].trigger()))
        return button

    def _build_header(self):
        header = QFrame()
        header.setObjectName('headerBar')
        row = QHBoxLayout(header)
        row.setContentsMargins(d.SPACE_M, d.SPACE_S, d.SPACE_M, d.SPACE_S)
        row.setSpacing(d.SPACE_S)

        logo = QLabel()
        pix = logo_pixmap(d.LOGO_HEADER)
        if not pix.isNull():
            logo.setPixmap(pix)
        logo.setFixedSize(d.LOGO_HEADER, d.LOGO_HEADER)
        logo.setToolTip('Dogspeak')
        row.addWidget(logo)

        self.wordmark = QLabel('Dogspeak')
        self.wordmark.setObjectName('wordmark')
        row.addWidget(self.wordmark)

        self.title_entry = QLineEdit()
        self.title_entry.setObjectName('titleEntry')
        self.title_entry.setPlaceholderText('Draft title')
        self.title_entry.setToolTip('Draft title')
        self.title_entry.setAccessibleName('Draft title')
        self.title_entry.setMinimumWidth(110)
        self.title_entry.setMaximumWidth(280)
        self.title_entry.textEdited.connect(lambda _t: self._changed(title=True))
        row.addWidget(self.title_entry, 1)

        self.new_button = self._tool('New', 'New draft (Ctrl+N)', 'new-draft', flat=True)
        self.open_button = self._tool('Drafts', 'Open a draft (Ctrl+O)', 'open-draft', flat=True)
        row.addWidget(self.new_button)
        row.addWidget(self.open_button)
        row.addStretch(1)

        self.sources = [None]
        self.updating_sources = False
        self.microphone = QComboBox()
        self.microphone.addItem('System default')
        self.microphone.setToolTip('Microphone')
        self.microphone.setAccessibleName('Microphone')
        self.microphone.setMinimumWidth(120)
        self.microphone.setMaximumWidth(190)
        self.microphone.currentIndexChanged.connect(self._microphone_changed)
        row.addWidget(self.microphone)

        row.addWidget(self.cleanup_view.panel_button)

        menu = QMenu(self)
        for name in ('new-draft', 'open-draft'):
            menu.addAction(self.actions_by_name[name])
        menu.addSeparator()
        for name in ('copy-all', 'copy-clear', 'clear'):
            menu.addAction(self.actions_by_name[name])
        menu.addSeparator()
        for name in ('move-line-up', 'move-line-down'):
            menu.addAction(self.actions_by_name[name])
        menu.addSeparator()
        menu.addAction(self.actions_by_name['import-audio'])
        menu.addAction(self.actions_by_name['settings'])
        menu.addSeparator()
        menu.addAction(self.actions_by_name['quit'])
        self.menu_button = QToolButton()
        self.menu_button.setText('☰')
        self.menu_button.setObjectName('menu')
        self.menu_button.setToolTip('Main menu')
        self.menu_button.setAccessibleName('Main menu')
        self.menu_button.setCursor(Qt.PointingHandCursor)
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        self.menu_button.setMenu(menu)
        row.addWidget(self.menu_button)
        return header

    def _build_hero_strip(self):
        """Eye-level ON AIR / Field Recorder status strip directly above the draft."""
        wrap = QWidget()
        wrap.setObjectName('heroWrap')
        outer = QHBoxLayout(wrap)
        outer.setContentsMargins(d.SPACE_M, d.SPACE_M, d.SPACE_M, 0)
        outer.setSpacing(0)

        self.hero_strip = QFrame()
        self.hero_strip.setObjectName('heroStrip')
        self.hero_strip.setMaximumWidth(d.EDITOR_MAX_WIDTH + 60)
        self.hero_strip.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        vbox = QVBoxLayout(self.hero_strip)
        vbox.setContentsMargins(d.SPACE_M, d.SPACE_S, d.SPACE_M, d.SPACE_S)
        vbox.setSpacing(d.SPACE_S)

        top = QHBoxLayout()
        top.setSpacing(d.SPACE_S)

        self.hero_mascot = QLabel()
        pix = logo_pixmap(d.LOGO_HERO)
        if not pix.isNull():
            self.hero_mascot.setPixmap(pix)
        self.hero_mascot.setFixedSize(d.LOGO_HERO, d.LOGO_HERO)
        self.hero_mascot.hide()
        top.addWidget(self.hero_mascot)

        self.state_lamp = QLabel('WARMING UP')
        self.state_lamp.setObjectName('stateLamp')
        top.addWidget(self.state_lamp)

        self.status_label = QLabel()
        self.status_label.setObjectName('heroStatus')
        self.status_label.setTextFormat(Qt.PlainText)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_label.setWordWrap(True)
        # Accessible name stays the text itself so screen readers and UI tests read it.
        self.status_label.setAccessibleDescription('Status')
        top.addWidget(self.status_label, 1)

        self.hero_hint = QLabel()
        self.hero_hint.setObjectName('heroHint')
        self.hero_hint.setTextFormat(Qt.PlainText)
        self.hero_hint.hide()
        top.addWidget(self.hero_hint)

        self.timer_label = QLabel()
        self.timer_label.setObjectName('heroTimer')
        self.timer_label.setTextFormat(Qt.PlainText)
        top.addWidget(self.timer_label)

        vbox.addLayout(top)
        self.tape_meter = TapeMeter()
        self.busy = self.tape_meter
        vbox.addWidget(self.tape_meter)

        outer.addStretch(1)
        outer.addWidget(self.hero_strip, 100)
        outer.addStretch(1)
        return wrap

    def _build_dock(self):
        """Bottom Field Recorder Instrument Dock: Speak · Refine · Ship."""
        wrap = QWidget()
        wrap.setObjectName('dockWrap')
        outer = QHBoxLayout(wrap)
        outer.setContentsMargins(d.SPACE_M, d.SPACE_S, d.SPACE_M, d.SPACE_M)
        outer.setSpacing(0)

        self.dock = QFrame()
        self.dock.setObjectName('dock')
        self.dock.setMaximumWidth(d.EDITOR_MAX_WIDTH + 60)
        self.dock.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.dock_vbox = QVBoxLayout(self.dock)
        self.dock_vbox.setContentsMargins(d.SPACE_S, d.SPACE_S, d.SPACE_S, d.SPACE_S)
        self.dock_vbox.setSpacing(d.SPACE_S)

        self.dock_row1_widget = QWidget()
        self.dock_row1 = QHBoxLayout(self.dock_row1_widget)
        self.dock_row1.setContentsMargins(0, 0, 0, 0)
        self.dock_row1.setSpacing(d.SPACE_S)

        self.dock_row2_widget = QWidget()
        self.dock_row2 = QHBoxLayout(self.dock_row2_widget)
        self.dock_row2.setContentsMargins(0, 0, 0, 0)
        self.dock_row2.setSpacing(d.SPACE_S)

        self.record_button = QPushButton('●  Record')
        self.record_button.setObjectName('recordBtn')
        self.record_button.setAccessibleName('Record')
        self.record_button.setCursor(Qt.PointingHandCursor)
        self.record_button.setMinimumHeight(d.CONTROL_HEIGHT)
        self.record_button.clicked.connect(lambda: self.perform(self.toggle_recording))

        self.dock_div1 = QFrame()
        self.dock_div1.setObjectName('dockDivider')
        self.dock_div2 = QFrame()
        self.dock_div2.setObjectName('dockDivider')

        self.copy_clear_button = QPushButton('Copy && Clear')
        self.copy_clear_button.setObjectName('copyClearBtn')
        self.copy_clear_button.setToolTip('Copy all text and clear this draft (Ctrl+Shift+X)')
        self.copy_clear_button.setAccessibleName('Copy and clear')
        self.copy_clear_button.setCursor(Qt.PointingHandCursor)
        self.copy_clear_button.clicked.connect(lambda: self.actions_by_name['copy-clear'].trigger())

        self.copy_all_button = self._tool('Copy', 'Copy the whole draft (Ctrl+Shift+C)', 'copy-all')

        self.dock_vbox.addWidget(self.dock_row1_widget)
        self.dock_vbox.addWidget(self.dock_row2_widget)

        outer.addStretch(1)
        outer.addWidget(self.dock, 100)
        outer.addStretch(1)
        return wrap

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)

    def _apply_responsive_layout(self, width=None):
        """Switch cleanly between single-row wide dock and two-row narrow dock."""
        total_w = self.width() if width is None else width
        panel_w = d.PANEL_WIDTH if not self.cleanup_view.panel.isHidden() else 0
        stage_w = total_w - panel_w
        narrow_header = total_w < d.NARROW_BREAKPOINT
        narrow_dock = stage_w < d.NARROW_BREAKPOINT
        state = (narrow_header, narrow_dock)
        if state == self._narrow:
            return
        self._narrow = state
        self.wordmark.setVisible(not narrow_header)
        self.new_button.setVisible(not narrow_header)
        self.open_button.setVisible(not narrow_header)
        self.microphone.setMaximumWidth(135 if narrow_header else 190)

        self._clear_layout(self.dock_row1)
        self._clear_layout(self.dock_row2)
        if narrow_dock:
            self.dock_div1.hide()
            self.dock_div2.hide()
            self.dock_row1.addWidget(self.record_button, 1)
            self.dock_row1.addWidget(self.copy_clear_button, 1)
            self.dock_row1.addWidget(self.copy_all_button)
            self.dock_row2.addWidget(self.cleanup_view.bar, 1)
            self.cleanup_view.bar.show()
            self.dock_row2_widget.show()
        else:
            self.dock_row2_widget.hide()
            self.dock_div1.show()
            self.dock_div2.show()
            self.dock_row1.addWidget(self.record_button)
            self.dock_row1.addWidget(self.dock_div1)
            self.dock_row1.addWidget(self.cleanup_view.bar, 1)
            self.dock_row1.addWidget(self.dock_div2)
            self.dock_row1.addWidget(self.copy_clear_button)
            self.dock_row1.addWidget(self.copy_all_button)
            self.cleanup_view.bar.show()
        self.dock.updateGeometry()
        self.dock.adjustSize()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_responsive_layout(self.width())

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and not self._updating_theme:
            app = QApplication.instance()
            if app is not None:
                dark = d.is_dark(app)
                if dark != self._dark_theme:
                    self._dark_theme = dark
                    self._updating_theme = True
                    try:
                        app.setStyleSheet(d.stylesheet(dark))
                    finally:
                        self._updating_theme = False

    def _editor_menu(self, point):
        menu = self.editor.createStandardContextMenu()
        menu.addSeparator()
        menu.addAction(self.actions_by_name['move-line-up'])
        menu.addAction(self.actions_by_name['move-line-down'])
        menu.exec(self.editor.mapToGlobal(point))

    def _build_notice(self):
        """Brief feedback such as "Copied and cleared", with its button (Qt has no toasts)."""
        self.notice = QFrame()
        self.notice.setObjectName('notice')
        row = QHBoxLayout(self.notice)
        row.setContentsMargins(d.SPACE_L, d.SPACE_XS, d.SPACE_L, d.SPACE_XS)
        self.notice_text = QLabel()
        self.notice_text.setTextFormat(Qt.PlainText)
        row.addWidget(self.notice_text, 1)
        self.notice_button = QPushButton('Undo')
        self.notice_button.setCursor(Qt.PointingHandCursor)
        self.notice_button.clicked.connect(self._notice_clicked)
        row.addWidget(self.notice_button)
        self.notice.hide()
        self._notice_action = None
        self.notice_timer.timeout.connect(self.dismiss_notice)
        return self.notice

    def show_notice(self, text, button=None, action=None):
        self.notice_text.setText(text)
        self.notice_button.setVisible(button is not None)
        if button:
            self.notice_button.setText(button)
        self._notice_action = action
        self.notice.show()
        self.notice_timer.start()

    def dismiss_notice(self):
        self.notice_timer.stop()
        self._notice_action = None
        self.notice.hide()

    def _notice_clicked(self):
        action = self._notice_action
        self.dismiss_notice()
        if action is not None:
            self.perform(action)

    def _build_tray(self):
        tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        menu.addAction('Show Dogspeak', self.show_window)
        self.tray_record = menu.addAction('Record', lambda: self.perform(self.toggle_recording))
        menu.addAction('New draft', lambda: self.perform(self._new_from_tray))
        menu.addAction('Copy all', lambda: self.perform(self.copy_all))
        menu.addSeparator()
        menu.addAction('Quit', lambda: self.perform(self.quit_session))
        tray.setContextMenu(menu)
        self._tray_menu = menu
        tray.activated.connect(
            lambda reason: self.show_window() if reason in (
                QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        tray.setToolTip('Dogspeak')
        tray.show()
        return tray

    def _new_from_tray(self):
        self.controller.new_draft()
        self.show_window()

    # Shortcut
    def _register_shortcut(self, text):
        problem = self.hotkeys.set_shortcut(text, lambda: self.perform(self.toggle_recording))
        self.controller.shortcut_label = (hotkeys.label(text) if problem is None
                                          else IN_WINDOW_SHORTCUT)
        return problem

    def apply_shortcut(self, text):
        """Settings changed the shortcut. Returns None, or why it can't be used."""
        old = self.controller.store.get_setting('hotkey', hotkeys.DEFAULT) or hotkeys.DEFAULT
        problem = self._register_shortcut(text)
        if problem:
            self._register_shortcut(old)
            return problem
        self.controller.store.set_setting('hotkey', text)
        self.shortcut_problem = None
        if self.controller.ready and self.controller.state == 'ready':
            self.controller.status('ready', self.controller._ready_message())
        return None

    # Drafts and editing
    def _new_document(self):
        document = QTextDocument(self)
        document.setDocumentLayout(QPlainTextDocumentLayout(document))
        document.setDefaultFont(self.editor.font())
        document.contentsChanged.connect(self._changed)
        return document

    def _insertion_point(self):
        # With the window closed the cursor can't be seen, so it is no choice of the user's.
        if not self.isVisible():
            return None
        return self.editor.insertion_point()

    def _hold_result(self, event):
        wait = self.typing_pause - (self.controller.clock() - self.last_edit)
        if wait <= 0:
            return False
        QTimer.singleShot(int(wait * 1000) + 1, lambda: self.controller._event(event))
        return True

    def _set_sources(self, sources):
        if self.closing:
            return False
        self.sources = [None] + [source['name'] for source in sources]
        labels = ['System default'] + [source['description'] for source in sources]
        c = self.controller
        self.updating_sources = True
        self.microphone.clear()
        self.microphone.addItems(labels)
        self.microphone.setCurrentIndex(self.sources.index(c.source) if c.source in self.sources else 0)
        self.updating_sources = False
        self._microphone_changed(self.microphone.currentIndex())
        return False

    def _microphone_changed(self, index):
        if self.updating_sources or not 0 <= index < len(self.sources):
            return
        self.controller.source = self.sources[index]
        self.perform(lambda: self.controller.store.set_setting(
            'microphone', self.controller.source or ''))

    def perform(self, fn):
        try:
            return fn()
        except Exception as exc:
            self.controller.status('error', str(exc))

    def _changed(self, title=False):
        if self.rendering or self.closing:
            return
        self.dismiss_notice()  # Undo only applies to the text it cleared
        if not title:
            self.last_edit = self.controller.clock()
        self.controller.edit(self.title_entry.text(), self.editor.text())
        self.save_timer.start()
        self._update_clear_actions()

    def _autosave(self):
        self.perform(self.controller.flush)
        self.cleanup_view.refresh()  # the shown version follows undo and typing

    def render_draft(self, draft, append=False, at=None):
        switching = self.displayed_draft != draft.id
        if switching:
            self.dismiss_notice()
        self.displayed_draft = draft.id
        self.rendering = True
        try:
            document = self.documents.get(draft.id)
            if document is not None:
                if self.editor.document() is not document:
                    self.editor.setDocument(document)
                append = True
            else:
                document = self._new_document()
                self.documents[draft.id] = document
                self.editor.setDocument(document)
                append = False
            if append:
                old = document.toPlainText()
                start = len(old) if at is None else at
                added = draft.content[start:start + len(draft.content) - len(old)]
                if draft.content != old[:start] + added + old[start:]:
                    raise RuntimeError('Draft changed outside the editor; reopen it to reconcile')
                if added:
                    cursor = QTextCursor(document)
                    cursor.setPosition(to_qt(old, start))
                    cursor.beginEditBlock()
                    cursor.insertText(added)
                    cursor.endEditBlock()
                    if at is not None:
                        # After the words, not their separator: they must stay in view.
                        shown = self.editor.textCursor()
                        shown.setPosition(to_qt(draft.content, start + len(added.rstrip())))
                        self.editor.setTextCursor(shown)
            else:
                document.setUndoRedoEnabled(False)
                document.setPlainText(draft.content)
                self.editor.format_document(document)
                document.setUndoRedoEnabled(True)
            if self.title_entry.text() != draft.title:
                self.title_entry.setText(draft.title)
                self.title_entry.setCursorPosition(0)
        finally:
            self.rendering = False
        if switching:
            self.editor.moveCursor(QTextCursor.End)
            self.editor.setFocus()
        self.editor.ensureCursorVisible()
        if switching:
            self.cleanup_view.refresh()
        self._update_clear_actions()

    def replace_text(self, draft_id, text):
        """Swap a draft's whole text as one undoable step (Ctrl+Z brings it back)."""
        document = self.documents.get(draft_id)
        if document is None:
            return
        self.dismiss_notice()
        self.rendering = True
        try:
            cursor = QTextCursor(document)
            cursor.beginEditBlock()
            cursor.select(QTextCursor.Document)
            cursor.insertText(text)
            cursor.endEditBlock()
        finally:
            self.rendering = False

    def set_locked(self, locked):
        """While Clean up rewrites this draft, it can't be edited."""
        self.editor.setReadOnly(locked)
        if self.editor.property('locked') != locked:
            self.editor.setProperty('locked', locked)
            _repolish(self.editor)

    def _status_changed(self):
        # States and their wording only; transcripts never reach the status line.
        c = self.controller
        shown = (c.state, c.message)
        if shown != self._logged_status:
            self._logged_status = shown
            log.info('status %s: %s', c.state, c.message)
        self.render_status()

    def _set_hero_tone(self, tone, lamp_text, lamp_style):
        if self.hero_strip.property('tone') != tone:
            self.hero_strip.setProperty('tone', tone)
            _repolish(self.hero_strip)
        if self.state_lamp.text() != lamp_text:
            self.state_lamp.setText(lamp_text)
        if self.state_lamp.property('lamp') != lamp_style:
            self.state_lamp.setProperty('lamp', lamp_style)
            _repolish(self.state_lamp)

    def render_status(self):
        c = self.controller
        recording = c.recording
        cleanup = self.cleanup
        message = c.message
        if c.state == 'ready' and self.shortcut_problem:
            message = self.shortcut_problem

        started_at = c.recording_job['started_at'] if recording else c.started_at
        elapsed = max(0.0, c.clock() - started_at)
        elapsed_int = int(elapsed)
        phase = (elapsed % 1.8) / 1.8

        show_mascot = False
        hint = ''
        meter_mode = 'hidden'
        meter_fraction = 0.0

        if recording:
            near = elapsed_int >= NEAR_CAP_SECONDS
            left = max(0, MAX_RECORDING_SECONDS - elapsed_int)
            tone = 'nearcap' if near else 'recording'
            lamp = f'● ON AIR · {left}s LEFT' if near else '● ON AIR'
            timer_text = format_cap_readout(elapsed_int)
            hint = 'Enter stops · Esc cancels'
            meter_mode = tone
            meter_fraction = min(1.0, elapsed / MAX_RECORDING_SECONDS)
            self._set_hero_tone(tone, lamp, tone)
        elif c.state == 'transcribing':
            self._set_hero_tone('transcribing', 'LOCAL CPU', 'transcribing')
            timer_text = format_duration(elapsed_int)
            hint = 'Esc drops'
            meter_mode = 'transcribing'
        elif cleanup.busy:
            # Clean up doesn't need the speech model, so it outranks "Loading model…".
            from .llm import provider_name
            cfg = cleanup.busy['config']
            name = provider_name(cfg['base_url'])
            short = name.split(' (', 1)[0].upper()
            is_local_llm = not cleanup.needs_key(cfg['base_url'])
            message = f'Sending this draft to {name}… Esc cancels.'
            lamp = f'LOCAL LLM · {short}' if is_local_llm else f'↗ EXTERNAL · {short}'
            llm_elapsed = max(0.0, time.monotonic() - cleanup.busy.get('started_at', time.monotonic()))
            phase = (llm_elapsed % 1.8) / 1.8
            self._set_hero_tone('llm', lamp, 'llm')
            timer_text = format_duration(int(llm_elapsed))
            hint = cfg.get('model') or ''
            meter_mode = 'llm'
            show_mascot = True
        elif c.state == 'loading':
            self._set_hero_tone('loading', 'WARMING UP', 'loading')
            timer_text = format_duration(elapsed_int)
            hint = 'You can type while Bark loads'
            meter_mode = 'loading'
            show_mascot = True
        elif c.state == 'error':
            self._set_hero_tone('error', 'ERROR', 'error')
            timer_text = ''
        else:
            self._set_hero_tone('ready', 'STANDBY', 'ready')
            timer_text = f'{MAX_RECORDING_LABEL} / take'

        self.hero_mascot.setVisible(show_mascot and not self.hero_mascot.pixmap().isNull())
        self.status_label.setText(message)
        is_error = c.state == 'error' and not (recording or cleanup.busy)
        if self.status_label.property('error') != is_error:
            self.status_label.setProperty('error', is_error)
            _repolish(self.status_label)
        self.timer_label.setText(timer_text)
        narrow_dock = bool(self._narrow and self._narrow[1])
        self.hero_hint.setText(hint)
        self.hero_hint.setVisible(bool(hint) and not narrow_dock)
        self.tape_meter.set_state(meter_mode, meter_fraction, phase)

        self.record_button.setText('■  Stop' if recording else '●  Record')
        self.record_button.setAccessibleName('Stop' if recording else 'Record')
        self.record_button.setToolTip(f'{"Stop" if recording else "Record"} ({c.shortcut_label})')
        if self.record_button.property('recording') != recording:
            self.record_button.setProperty('recording', recording)
            _repolish(self.record_button)
        self.record_button.setEnabled(not c.closed and (recording or c.engine_failed or (
            c.ready and not c.pending and not c.recording_job)))
        self.actions_by_name['toggle-recording'].setText('Stop' if recording else 'Record')
        self.microphone.setEnabled(not recording and not c.recording_job)
        self.cleanup_view.clean_button.setEnabled(cleanup.can_start())
        self._update_clear_actions()
        if self.tray is not None:
            self.tray_record.setText(f'{"Stop" if recording else "Record"} ({c.shortcut_label})')
            self.tray.setToolTip(f'Dogspeak · {message}'[:120])
        self._sync_overlay()

    def show_drafts(self):
        self.controller.flush()
        self.show_window()
        self.drafts_dialog = DraftsDialog(self)
        self.drafts_dialog.open()
        return self.drafts_dialog

    def show_settings(self):
        self.show_window()
        self.settings_dialog = SettingsDialog(self, self.cleanup)
        self.settings_dialog.open()
        return self.settings_dialog

    def confirm_delete(self, draft, item=None, dialog=None):
        title = draft.title or 'Untitled'
        box = QMessageBox(QMessageBox.Warning, f'Delete “{title}”?',
                          'The draft and its saved transcripts are removed. This cannot be undone.',
                          QMessageBox.Cancel, dialog or self)
        delete = box.addButton('Delete', QMessageBox.DestructiveRole)
        box.setDefaultButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is delete and self.delete_draft(draft.id) and item is not None:
            item.listWidget().takeItem(item.listWidget().row(item))
        return box

    def delete_draft(self, draft_id):
        displayed = self.displayed_draft
        if self.perform(lambda: self.controller.delete_draft(draft_id)):
            self.documents.pop(draft_id, None)
            if displayed == draft_id:
                self.displayed_draft = None
                self.render_draft(self.controller.draft)
            return True
        self.render_status()
        return False

    def copy_all(self):
        QGuiApplication.clipboard().setText(self.editor.text())

    def _read_clipboard(self):
        return QGuiApplication.clipboard().text()

    def _can_clear(self):
        c = self.controller
        return not (self.closing or c.closed or c.recording_job or c.pending
                    or self.cleanup.busy) and bool(self.editor.text())

    def _update_clear_actions(self):
        enabled = self._can_clear()
        for name in ('copy-clear', 'clear'):
            self.actions_by_name[name].setEnabled(enabled)
        self.copy_clear_button.setEnabled(enabled)

    def clear_text(self, copied=False):
        """Empty the draft's text as one undo step; the title, transcripts and versions stay."""
        if not self._can_clear():
            return False
        document = self.editor.document()
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        cursor.select(QTextCursor.Document)
        cursor.removeSelectedText()
        cursor.endEditBlock()
        self.editor.setFocus()
        self.cleanup_view.refresh()

        def undo():
            if self.editor.document() is document and document.isUndoAvailable():
                document.undo()
                self.editor.setFocus()
        self.show_notice('Copied and cleared' if copied else 'Cleared', 'Undo', undo)
        self._update_clear_actions()
        return True

    def copy_and_clear(self):
        """Copy, read the clipboard back, and clear only if it really holds the text."""
        if not self._can_clear():
            return False
        text = self.editor.text()
        try:
            self.copy_all()
            copied = self._read_clipboard()
        except Exception:
            copied = None
        # Windows stores line breaks as CRLF on the clipboard.
        if copied is None or copied.replace('\r\n', '\n') != text:
            self.show_notice('Clipboard could not be verified. Text kept.')
            return False
        return self.clear_text(copied=True)

    def import_audio_dialog(self):
        path, _filter = QFileDialog.getOpenFileName(self, 'Import audio file', str(Path.home()),
                                                    AUDIO_FILTER)
        if path:
            self.controller.import_audio(path)

    def show_window(self):
        """Bring the window forward as it was. showNormal() would also un-maximize it."""
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    # Keys while recording, when the window has focus.
    def handle_session_key(self, key):
        if QApplication.activeModalWidget() is not None:
            return False  # Esc closes the dialog; it must not drop a transcript
        c = self.controller
        if key == Qt.Key_Escape and self.cleanup.busy and not c.recording_job:
            return self.cleanup.cancel()
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if c.recording:
                c.stop_recording()
                return True
            return False
        if key == Qt.Key_Escape:
            return bool(c.cancel_recording())
        return False

    def eventFilter(self, watched, event):
        if (event.type() == event.Type.KeyPress and isinstance(watched, QWidget)
                and watched.window() is self and not event.modifiers()
                & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier)):
            if event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
                handled = self.perform(lambda: self.handle_session_key(event.key()))
                if handled:
                    self.render_status()
                    return True
        return False

    # Recording, and the pill that shows it over other apps.
    def toggle_recording(self):
        c = self.controller
        if c.toggle_recording():
            return True
        # A refused global shortcut must say why; the editor may be hidden.
        if not self.isActiveWindow():
            if c.state == 'error':
                self._overlay_flash('error', c.message, '', 4000)
            elif c.state == 'loading' and c.message.startswith('Restarting'):
                self._announce_ready = True
                self._overlay_flash('info', 'Restarting the model…', 'Try again in a moment', 2500)
            elif not c.ready:
                self._announce_ready = True
                self._overlay_flash('info', 'Dogspeak is loading…', 'Try again in a moment', 2500)
            elif c.pending or c.recording_job:
                self._overlay_flash('info', 'Still transcribing', 'Esc drops it', 2000)
            else:
                self._overlay_flash('info', 'Finishing the last recording', 'Try again in a moment', 1500)
        return False

    def _stop_hint(self):
        if self.hotkeys.available:
            return 'Enter stops · Esc cancels'
        return f'{self.controller.shortcut_label} stops' + (
            ' · Esc cancels' if self.isActiveWindow() else '')

    def _overlay_target(self):
        c = self.controller
        if c.recording:
            elapsed = max(0.0, c.clock() - c.recording_job['started_at'])
            elapsed_int = int(elapsed)
            warn = elapsed_int >= NEAR_CAP_SECONDS
            left = max(0, MAX_RECORDING_SECONDS - elapsed_int)
            title = f'ON AIR  {format_cap_readout(elapsed_int)}' + (f' · {left}s left' if warn else '')
            return ('recording', title, self._stop_hint(),
                    min(1.0, elapsed / MAX_RECORDING_SECONDS), warn)
        if c.state == 'transcribing' and (c.pending or c.recording_job):
            return 'transcribing', c.message, 'Esc drops this result', None, False
        return None

    def _sync_overlay(self):
        c = self.controller
        if c.closed:
            self._hide_overlay()
            return
        target = self._overlay_target()
        if target:
            self.pill_timer.stop()
            self._overlay_set(*target)
        elif self._pill_state in ('recording', 'transcribing'):
            # The job just ended; say how, briefly.
            if c.state == 'error':
                self._overlay_flash('error', c.message, '', 4000)
            elif c.outcome == 'saved':
                hint = 'Copied · paste with Ctrl+V' if self._copied else 'Added to the draft'
                self._overlay_flash('saved', 'Saved', hint, 2000)
            elif c.outcome == 'empty':
                self._overlay_flash('info', 'No speech detected', '', 2000)
            elif c.outcome == 'cancelled':
                self._overlay_flash('info', 'Cancelled', '', 1200)
            else:
                self._hide_overlay()
            self._copied = False
        elif self._announce_ready and c.ready:
            self._announce_ready = False
            self._overlay_flash('info', 'Dogspeak is ready', f'{c.shortcut_label} to record', 2500)

    def _overlay_set(self, state, title, hint, progress=None, warn=False):
        self._pill_state = state
        shown = (state, title, hint, None if progress is None else round(progress, 2), warn)
        if shown != self._pill_shown:
            self._pill_shown = shown
            self.pill.show_state(state, title, hint, progress=progress, warn=warn)
        keys = {'recording': ('stop', 'cancel'), 'transcribing': ('cancel',)}.get(state, ())
        self._set_keys(keys)

    def _set_keys(self, keys):
        if keys != self._pill_keys:
            self._pill_keys = keys
            self.hotkeys.set_session_keys(keys, self._overlay_key)

    def _overlay_flash(self, state, title, hint, milliseconds):
        self._overlay_set(state, title, hint)
        self.pill_timer.start(milliseconds)

    def _hide_overlay(self):
        self.pill_timer.stop()
        self._pill_state = ''
        self._pill_shown = None
        self.pill.hide_state()
        self._set_keys(())

    def _overlay_key(self, key):
        c = self.controller
        if key == 'stop' and c.recording:
            self.perform(c.stop_recording)
        elif key == 'cancel':
            self.perform(c.cancel_recording)
        self.render_status()

    def _copy_transcript(self, text):
        # Windows lets an app in the background set the clipboard.
        QGuiApplication.clipboard().setText(text)
        self._copied = True

    # Closing
    def closeEvent(self, event):
        if self.closing:
            event.accept()
            return
        try:
            self.controller.flush()
        except Exception as exc:
            self.controller.status(
                'error', f'Could not save draft: {exc}. Draft kept open; retry saving or copy '
                         'the text before discarding.')
            event.ignore()
            self._show_save_error()
            return
        event.ignore()
        self.hide()
        log.info('window hidden; still running')
        if self.tray is not None and not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage('Dogspeak is still running',
                                  f'{self.controller.shortcut_label} records from any app. '
                                  'Quit from the tray icon to free the memory.',
                                  QSystemTrayIcon.Information, 4000)

    def _pause(self):
        self.closing = True
        self.save_timer.stop()
        self.tick_timer.stop()
        self.setEnabled(False)
        self.status_label.setText('Closing…')

    def _resume(self):
        self.closing = False
        self.setEnabled(True)
        self.tick_timer.start()
        if self.controller.dirty:
            self.save_timer.start()
        self.render_status()
        self.editor.setFocus()

    def _show_save_error(self):
        box = QMessageBox(QMessageBox.Warning, 'Could not save draft', self.controller.message,
                          QMessageBox.NoButton, self)
        keep = box.addButton('Keep editing', QMessageBox.RejectRole)
        copy = box.addButton('Copy text', QMessageBox.ActionRole)
        discard = box.addButton('Discard', QMessageBox.DestructiveRole)
        box.setDefaultButton(keep)
        box.exec()
        if box.clickedButton() is copy:
            self.copy_all()
        elif box.clickedButton() is discard:
            self._pause()
            self.controller.discard(done=self._closed)
        return box

    def quit_session(self):
        if self.closing:
            return
        log.info('quitting')
        self._pause()
        if not self.controller.close(done=self._closed):
            self._resume()
            self._show_save_error()

    def _closed(self):
        self.shutdown()
        QApplication.instance().quit()

    def shutdown(self):
        """Release the shortcut, the pill and the tray; the app is going away."""
        QApplication.instance().removeEventFilter(self)
        self.tick_timer.stop()
        self.pill_timer.stop()
        self.hotkeys.close()
        self.pill.hide_state()
        if self.tray is not None:
            self.tray.hide()


class SingleInstance:
    """One Dogspeak per user. A second launch hands its command to the first and exits."""

    def __init__(self, name=None):
        self.name = name or f'Dogspeak-{getpass.getuser()}'
        self.server = None
        self.on_command = lambda command: None

    def send(self, command, timeout=1000, attempts=3):
        """Hand a command to the running Dogspeak. False only if none is running.

        The first instance answers "ok" once it has the whole line; without that
        answer a Windows named pipe can lose the line, so this tries again.
        """
        payload = (json.dumps(command) + '\n').encode('utf-8')
        running = False
        for _attempt in range(attempts):
            socket = QLocalSocket()
            socket.connectToServer(self.name)
            if not socket.waitForConnected(timeout):
                return running
            running = True
            if paths.WINDOWS:
                # Let the running instance bring its window to the front.
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
            socket.write(payload)
            socket.flush()
            socket.waitForBytesWritten(timeout)
            reply = b''
            while b'\n' not in reply and socket.waitForReadyRead(timeout):
                reply += bytes(socket.readAll())
            socket.disconnectFromServer()
            if reply.startswith(b'ok'):
                return True
        return running

    def listen(self):
        QLocalServer.removeServer(self.name)  # left behind by a crash
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self._connection)
        return self.server.listen(self.name)

    def _connection(self):
        while (socket := self.server.nextPendingConnection()) is not None:
            self._serve(socket)

    def _serve(self, socket):
        buffer = bytearray()
        done = []

        def read():
            buffer.extend(bytes(socket.readAll()))
            if done or b'\n' not in buffer:
                return
            done.append(True)
            line = bytes(buffer).split(b'\n', 1)[0]
            try:
                command = json.loads(line.decode('utf-8'))
            except ValueError:
                command = None
            socket.write(b'ok\n' if isinstance(command, dict) else b'bad\n')
            socket.flush()
            if isinstance(command, dict):
                # Answer first; the command may open dialogs or take a moment.
                QTimer.singleShot(0, lambda: self.on_command(command))
        socket.readyRead.connect(read)
        socket.disconnected.connect(socket.deleteLater)
        read()  # the line may already be here

    def close(self):
        if self.server is not None:
            self.server.close()


def platform_parts():
    """(recorder, list_sources, keyring, hotkeys_factory) for this system."""
    if paths.WINDOWS:
        from .audio_portaudio import Recorder, list_sources
        from .credentials_keyring import Keyring
        return Recorder(), list_sources, Keyring(), hotkeys.windows_hotkeys
    from .audio import Recorder, list_sources
    try:
        from .credentials_keyring import Keyring
        keyring = Keyring()
    except ImportError:
        keyring = MemoryKeyring()
    return Recorder(), list_sources, keyring, None


class MemoryKeyring:
    """Development on Linux without the keyring package: keys last until Quit."""

    def __init__(self):
        self.keys = {}

    def get(self, address):
        return self.keys.get(address, '')

    def set(self, address, key):
        self.keys[address] = key

    def clear(self, address):
        self.keys.pop(address, None)


def command_from(args):
    if args.import_audio:
        return {'action': 'import-audio', 'path': str(Path(args.import_audio).resolve())}
    if args.toggle_recording:
        return {'action': 'toggle-recording'}
    return {'action': 'show'}


def run_command(window, command):
    action = command.get('action')
    log.info('command from another launch: %s', action)
    if action == 'toggle-recording':
        window.perform(window.toggle_recording)
    elif action == 'import-audio' and isinstance(command.get('path'), str):
        window.show_window()
        window.perform(lambda: window.controller.import_audio(command['path']))
    else:
        window.show_window()


def start_log():
    """app.log next to worker.log: states and errors, never what was said.

    pythonw.exe has no console, so without this an error would vanish.
    """
    folder = paths.state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(folder / 'app.log', maxBytes=256 * 1024,
                                                   backupCount=1, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(handler)
    log.setLevel(logging.INFO)

    def unexpected(kind, value, trace):
        log.error('unexpected error', exc_info=(kind, value, trace))
        sys.__excepthook__(kind, value, trace)
    sys.excepthook = unexpected
    return handler


def main(argv=None):
    parser = argparse.ArgumentParser(prog='dogspeak', description='Dogspeak dictation scratchpad')
    parser.add_argument('--toggle-recording', action='store_true',
                        help='Start or stop recording in the running Dogspeak')
    parser.add_argument('--import-audio', metavar='FILE', help='Transcribe an audio file into this draft')
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    os.environ.setdefault('HF_HUB_OFFLINE', '1')
    os.environ.setdefault('TRANSFORMERS_OFFLINE', '1')
    if paths.WINDOWS:
        import ctypes
        # Taskbar grouping and icon belong to Dogspeak, not to pythonw.exe.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        # The installer checks this name to ask you to quit Dogspeak before it updates
        # or removes it. Held until the process ends.
        ctypes.windll.kernel32.CreateMutexW(None, False, APP_MUTEX)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('Dogspeak')
    app.setApplicationDisplayName('Dogspeak')
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(d.stylesheet(d.is_dark(app)))
    command = command_from(args)
    instance = SingleInstance()
    if instance.send(command):
        return 0
    instance.listen()
    start_log()
    log.info('started, pid %s', os.getpid())

    from .cleanup import Cleanup
    from .engine import EngineClient
    from .storage import Store
    recorder, list_sources, keyring, hotkeys_factory = platform_parts()
    data = paths.data_dir()
    data.mkdir(parents=True, mode=0o700, exist_ok=True)
    dispatcher = Dispatcher()
    controller = Controller(Store(data / 'dogspeak.sqlite3'), EngineClient, recorder, data,
                            dispatch=dispatcher, spawn=background, clock=time.monotonic)
    cleanup = Cleanup(controller, keyring)
    window = ScratchpadWindow(controller, list_sources, cleanup, hotkeys_factory)
    instance.on_command = lambda cmd: run_command(window, cmd)

    def shutdown():
        try:
            controller.flush()
        except Exception:
            pass
        if not controller.closed:
            controller.spawn = lambda fn: fn()
            controller.dispatch = lambda fn, *a: fn(*a)
            controller.close()
        window.shutdown()
        instance.close()
    app.aboutToQuit.connect(shutdown)
    if hasattr(signal, 'SIGTERM') and not paths.WINDOWS:
        # Ctrl+C or a plain kill quits like Ctrl+Q, so no recorder is left behind.
        # Python only sees signals between Qt events, so wake it now and then.
        for number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(number, lambda *_: QTimer.singleShot(0, window.quit_session))
        waker = QTimer(app, interval=500)
        waker.timeout.connect(lambda: None)
        waker.start()
    if command['action'] == 'toggle-recording':
        run_command(window, command)
    else:
        window.show()
        if command['action'] == 'import-audio':
            run_command(window, command)
    if window.shortcut_problem and window.tray is not None:
        window.tray.showMessage('Dogspeak', window.shortcut_problem, QSystemTrayIcon.Warning, 6000)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
