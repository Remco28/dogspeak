"""Dogspeak's window on Windows, in Qt. Run from the project environment:

    .venv\\Scripts\\pythonw.exe -m dogspeak_app.qt_app [--toggle-recording] [--import-audio FILE]

The draft logic is the shared Controller (controller.py) and Clean up
(cleanup.py); this module is only the window, tray, pill and shortcuts. It
also runs on Linux for tests and development, without a global shortcut.
"""
import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QGuiApplication, QIcon, QKeySequence, QTextCursor, QTextDocument
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextDocumentLayout, QProgressBar,
    QPushButton, QSystemTrayIcon, QToolBar, QToolButton, QVBoxLayout, QWidget)

from . import hotkeys, paths
from . import qt_design as d
from .controller import Controller, background
from .qt_cleanup import CleanupView, SettingsDialog
from .qt_editor import DraftEditor, to_qt
from .qt_overlay import Pill

APP_ID = 'io.github.Remco28.Dogspeak'
IN_WINDOW_SHORTCUT = 'Ctrl+Shift+R'
AUDIO_FILTER = 'Audio files (*.wav *.flac *.ogg *.mp3);;All files (*)'


def app_icon():
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addFile(str(paths.ROOT / 'data' / 'icons' / 'hicolor' / f'{size}x{size}' / 'apps'
                         / f'{APP_ID}.png'))
    return icon


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
        self.window = window
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
        preview.setStyleSheet(d.CAPTION_STYLE)
        text.addWidget(title)
        text.addWidget(preview)
        line.addLayout(text, 1)
        delete = QToolButton()
        delete.setText('Delete')
        delete.setAutoRaise(True)
        delete.setToolTip('Delete draft')
        delete.setAccessibleName(f'Delete {draft.title or "Untitled"}')
        delete.clicked.connect(lambda _=False, dr=draft, it=item: self.window.confirm_delete(dr, it, self))
        line.addWidget(delete)
        item.setSizeHint(row.sizeHint())
        self.rows.setItemWidget(item, row)

    def _open(self, item):
        draft_id = item.data(Qt.UserRole)
        self.window.perform(lambda: self.window.controller.open_draft(draft_id))
        self.accept()
        self.window.editor.setFocus()


class ScratchpadWindow(QMainWindow):
    def __init__(self, controller, sources_provider, cleanup, hotkeys_factory=None, tray=True):
        super().__init__()
        self.controller, self.cleanup = controller, cleanup
        self.setWindowTitle('Dogspeak')
        self.setWindowIcon(app_icon())
        self.resize(1100, 720)
        self.rendering = self.closing = False
        self.last_edit, self.typing_pause = float('-inf'), 1.0
        self.documents = {}
        self.displayed_draft = None
        self.drafts_dialog = self.settings_dialog = None
        self._tray_hint_shown = False
        self.save_timer = QTimer(self, singleShot=True, interval=500)
        self.save_timer.timeout.connect(self._autosave)

        self.editor = DraftEditor()
        self.cleanup_view = CleanupView(self, cleanup)
        self._build_actions()
        self._build_toolbar()
        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.cleanup_view.bar)
        column.addWidget(self.cleanup_view.banner)
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self.editor, 1)
        body.addWidget(self.cleanup_view.panel)
        column.addLayout(body, 1)
        column.addWidget(self._build_status())
        self.setCentralWidget(central)

        c = controller
        c.on_draft = self.render_draft
        c.on_status = self.render_status
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

    def _tool(self, text, tooltip, action=None, slot=None):
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAccessibleName(text)
        button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        button.clicked.connect(slot or (lambda: self.actions_by_name[action].trigger()))
        return button

    def _build_toolbar(self):
        bar = QToolBar('Main')
        bar.setMovable(False)
        bar.setFloatable(False)
        bar.setContextMenuPolicy(Qt.PreventContextMenu)
        bar.layout().setSpacing(d.SPACE_XS)
        bar.addWidget(self._tool('New', 'New draft (Ctrl+N)', 'new-draft'))
        bar.addWidget(self._tool('Open', 'Open a draft (Ctrl+O)', 'open-draft'))
        self.title_entry = QLineEdit()
        self.title_entry.setPlaceholderText('Draft title')
        self.title_entry.setToolTip('Draft title')
        self.title_entry.setAccessibleName('Draft title')
        self.title_entry.setMinimumWidth(220)
        self.title_entry.setMaximumWidth(520)
        self.title_entry.textEdited.connect(lambda _t: self._changed(title=True))
        spacer_left, spacer_right = QWidget(), QWidget()
        for spacer in (spacer_left, spacer_right):
            spacer.setSizePolicy(spacer.sizePolicy().Policy.Expanding,
                                 spacer.sizePolicy().Policy.Preferred)
        bar.addWidget(spacer_left)
        bar.addWidget(self.title_entry)
        bar.addWidget(spacer_right)
        bar.addWidget(self._tool('Copy all', 'Copy the whole draft (Ctrl+Shift+C)', 'copy-all'))
        bar.addWidget(self.cleanup_view.panel_button)
        self.record_button = QPushButton('●  Record')
        self.record_button.setAccessibleName('Record')
        self.record_button.setMinimumHeight(d.CONTROL_HEIGHT)
        self.record_button.clicked.connect(lambda: self.perform(self.toggle_recording))
        bar.addWidget(self.record_button)
        menu = QMenu(self)
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
        self.menu_button.setPopupMode(QToolButton.InstantPopup)
        self.menu_button.setMenu(menu)
        bar.addWidget(self.menu_button)
        self.addToolBar(bar)
        self.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self._editor_menu)

    def _editor_menu(self, point):
        menu = self.editor.createStandardContextMenu()
        menu.addSeparator()
        menu.addAction(self.actions_by_name['move-line-up'])
        menu.addAction(self.actions_by_name['move-line-down'])
        menu.exec(self.editor.mapToGlobal(point))

    def _build_status(self):
        status = QWidget()
        row = QHBoxLayout(status)
        row.setContentsMargins(d.SPACE_L, d.SPACE_S, d.SPACE_L, d.SPACE_S)
        row.setSpacing(d.SPACE_M)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.setTextVisible(False)
        self.busy.setFixedWidth(64)
        self.busy.setMaximumHeight(d.ICON // 2)
        row.addWidget(self.busy)
        self.status_label = QLabel()
        self.status_label.setTextFormat(Qt.PlainText)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.status_label.setWordWrap(True)
        # The accessible name stays the text itself, so screen readers and UI tests read it.
        self.status_label.setAccessibleDescription('Status')
        row.addWidget(self.status_label, 1)
        self.timer_label = QLabel()
        self.timer_label.setStyleSheet(d.CAPTION_STYLE)
        self.timer_label.setMinimumWidth(self.timer_label.fontMetrics().horizontalAdvance('00:00'))
        row.addWidget(self.timer_label)
        self.sources = [None]
        self.updating_sources = False
        self.microphone = QComboBox()
        self.microphone.addItem('System default')
        self.microphone.setToolTip('Microphone')
        self.microphone.setAccessibleName('Microphone')
        self.microphone.setMinimumWidth(200)
        self.microphone.currentIndexChanged.connect(self._microphone_changed)
        row.addWidget(self.microphone)
        return status

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
        if not title:
            self.last_edit = self.controller.clock()
        self.controller.edit(self.title_entry.text(), self.editor.text())
        self.save_timer.start()

    def _autosave(self):
        self.perform(self.controller.flush)
        self.cleanup_view.refresh()  # the shown version follows undo and typing

    def render_draft(self, draft, append=False, at=None):
        switching = self.displayed_draft != draft.id
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
        finally:
            self.rendering = False
        if switching:
            self.editor.moveCursor(QTextCursor.End)
            self.editor.setFocus()
        self.editor.ensureCursorVisible()
        if switching:
            self.cleanup_view.refresh()

    def replace_text(self, draft_id, text):
        """Swap a draft's whole text as one undoable step (Ctrl+Z brings it back)."""
        document = self.documents.get(draft_id)
        if document is None:
            return
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
        self.editor.setStyleSheet(f'QPlainTextEdit {{ {d.CAPTION_STYLE} }}' if locked else '')

    def render_status(self):
        c = self.controller
        recording = c.recording
        active = recording or c.state in ('loading', 'transcribing')
        message = c.message
        busy = c.state in ('loading', 'transcribing')
        if c.state == 'ready' and self.shortcut_problem:
            message = self.shortcut_problem
        cleanup = self.cleanup
        if cleanup.busy and not (recording or c.state == 'transcribing'):
            # Clean up doesn't need the speech model, so it outranks "Loading model…".
            from .llm import provider_name
            message = f'Sending this draft to {provider_name(cleanup.busy["config"]["base_url"])}… Esc cancels.'
            busy = True
        self.status_label.setText(message)
        self.status_label.setStyleSheet(d.DANGER_STYLE if c.state == 'error' else '')
        started_at = c.recording_job['started_at'] if recording else c.started_at
        elapsed = int(max(0, c.clock() - started_at))
        self.timer_label.setText(f'{elapsed // 60:02}:{elapsed % 60:02}' if active else '')
        self.busy.setVisible(busy)
        self.record_button.setText('■  Stop' if recording else '●  Record')
        self.record_button.setAccessibleName('Stop' if recording else 'Record')
        self.record_button.setToolTip(f'{"Stop" if recording else "Record"} ({c.shortcut_label})')
        self.record_button.setStyleSheet(d.DANGER_STYLE if recording else '')
        self.record_button.setEnabled(not c.closed and (recording or c.engine_failed or (
            c.ready and not c.pending and not c.recording_job)))
        self.actions_by_name['toggle-recording'].setText('Stop' if recording else 'Record')
        self.microphone.setEnabled(not recording and not c.recording_job)
        self.cleanup_view.clean_button.setEnabled(cleanup.can_start())
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

    def import_audio_dialog(self):
        path, _filter = QFileDialog.getOpenFileName(self, 'Import audio file', str(Path.home()),
                                                    AUDIO_FILTER)
        if path:
            self.controller.import_audio(path)

    def show_window(self):
        self.showNormal()
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
            elapsed = int(max(0, c.clock() - c.recording_job['started_at']))
            return 'recording', f'Recording  {elapsed // 60}:{elapsed % 60:02}', self._stop_hint()
        if c.state == 'transcribing' and (c.pending or c.recording_job):
            return 'transcribing', c.message, 'Esc drops this result'
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

    def _overlay_set(self, state, title, hint):
        self._pill_state = state
        shown = (state, title, hint)
        if shown != self._pill_shown:
            self._pill_shown = shown
            self.pill.show_state(state, title, hint)
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

    def send(self, command, timeout=1000):
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        if not socket.waitForConnected(timeout):
            return False
        if paths.WINDOWS:
            # Let the running instance bring its window to the front.
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        socket.write((json.dumps(command) + '\n').encode('utf-8'))
        socket.waitForBytesWritten(timeout)
        socket.disconnectFromServer()
        return True

    def listen(self):
        QLocalServer.removeServer(self.name)  # left behind by a crash
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self._connection)
        return self.server.listen(self.name)

    def _connection(self):
        socket = self.server.nextPendingConnection()
        buffer = bytearray()

        def read():
            buffer.extend(bytes(socket.readAll()))
            if b'\n' in buffer:
                line = bytes(buffer).split(b'\n', 1)[0]
                try:
                    command = json.loads(line.decode('utf-8'))
                except ValueError:
                    command = None
                if isinstance(command, dict):
                    self.on_command(command)
                socket.disconnectFromServer()
        socket.readyRead.connect(read)
        socket.disconnected.connect(socket.deleteLater)

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
    if action == 'toggle-recording':
        window.perform(window.toggle_recording)
    elif action == 'import-audio' and isinstance(command.get('path'), str):
        window.show_window()
        window.perform(lambda: window.controller.import_audio(command['path']))
    else:
        window.show_window()


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
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName('Dogspeak')
    app.setApplicationDisplayName('Dogspeak')
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(d.stylesheet())
    command = command_from(args)
    instance = SingleInstance()
    if instance.send(command):
        return 0
    instance.listen()

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
