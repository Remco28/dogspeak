"""Clean up widgets for the Qt window: purpose bar, banner, suggestions panel, Settings."""
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QKeySequenceEdit, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget)

from . import hotkeys, llm
from . import qt_design as d
from .cleanup import PRESETS


def _time(created_at):
    try:
        moment = datetime.fromisoformat(created_at).astimezone()
    except ValueError:
        return ''
    return moment.strftime('%I:%M %p').lstrip('0')


def caption(text, wrap=True):
    label = QLabel(text)
    label.setWordWrap(wrap)
    font = label.font()
    font.setPointSize(d.TEXT_CAPTION + 1)
    label.setFont(font)
    label.setStyleSheet(d.CAPTION_STYLE)
    return label


class CleanupView:
    """Owns the Clean up widgets of one window."""

    def __init__(self, window, cleanup):
        self.window, self.cleanup = window, cleanup
        self.updating = False
        cleanup.on_change = self.refresh
        cleanup.on_replace = window.replace_text

        # Purpose bar
        self.bar = QWidget()
        bar = QHBoxLayout(self.bar)
        bar.setContentsMargins(d.SPACE_M, d.SPACE_S, d.SPACE_M, d.SPACE_S)
        bar.setSpacing(d.SPACE_S)
        label = QLabel('&Purpose')
        bold = label.font()
        bold.setBold(True)
        label.setFont(bold)
        bar.addWidget(label)
        self.purpose = QLineEdit(cleanup.purpose)
        self.purpose.setPlaceholderText('General purpose (leave blank)')
        self.purpose.setClearButtonEnabled(True)
        self.purpose.setMinimumWidth(220)
        self.purpose.setMaximumWidth(420)
        self.purpose.setAccessibleName('Purpose')
        self.purpose.textChanged.connect(self._purpose_changed)
        label.setBuddy(self.purpose)
        bar.addWidget(self.purpose, 1)
        self.purpose_menu = QToolButton()
        self.purpose_menu.setObjectName('menu')
        self.purpose_menu.setArrowType(Qt.DownArrow)
        self.purpose_menu.setToolTip('Choose a purpose')
        self.purpose_menu.setAccessibleName('Choose a purpose')
        self.purpose_menu.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(self.purpose_menu)
        menu.aboutToShow.connect(lambda: self._fill_purposes(menu))
        self.purpose_menu.setMenu(menu)
        bar.addWidget(self.purpose_menu)
        self.clean_button = QPushButton('Clean up')
        self.clean_button.setDefault(False)
        self.clean_button.setToolTip('Clean up this draft (Ctrl+Shift+L)')
        self.clean_button.clicked.connect(lambda: window.perform(cleanup.start))
        bar.addWidget(self.clean_button)
        bar.addStretch(1)
        self.revert_button = QPushButton('↶ Revert')
        self.revert_button.setToolTip('Go back one version')
        self.revert_button.clicked.connect(lambda: window.perform(cleanup.revert))
        bar.addWidget(self.revert_button)
        self.version_menu = QToolButton()
        self.version_menu.setObjectName('versions')
        self.version_menu.setText('No versions yet')
        self.version_menu.setToolTip('Versions of this draft (last 20)')
        self.version_menu.setPopupMode(QToolButton.InstantPopup)
        versions = QMenu(self.version_menu)
        versions.aboutToShow.connect(lambda: self._fill_versions(versions))
        self.version_menu.setMenu(versions)
        bar.addWidget(self.version_menu)

        # Banner: setup needed, or the last clean up failed.
        self.banner = QFrame()
        self.banner.setObjectName('banner')
        banner = QHBoxLayout(self.banner)
        banner.setContentsMargins(d.SPACE_L, d.SPACE_S, d.SPACE_L, d.SPACE_S)
        self.banner_text = QLabel()
        self.banner_text.setWordWrap(True)
        self.banner_text.setTextFormat(Qt.PlainText)
        banner.addWidget(self.banner_text, 1)
        self.banner_button = QPushButton()
        self.banner_button.clicked.connect(self._banner_clicked)
        banner.addWidget(self.banner_button)
        self.banner.hide()

        # Suggestions panel
        self.panel_button = QToolButton()
        self.panel_button.setToolTip('Show or hide suggestions')
        self.panel_button.setAccessibleName('Suggestions')
        show = cleanup.store.get_setting('suggestions_panel', '1') != '0'
        self.panel_button.clicked.connect(lambda: self.show_panel(self.panel.isHidden()))
        self.panel = QFrame()
        self.panel.setFrameShape(QFrame.NoFrame)
        self.panel.setFixedWidth(d.PANEL_WIDTH)
        outer = QHBoxLayout(self.panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        line = QFrame()
        line.setFrameShape(QFrame.VLine)
        line.setFrameShadow(QFrame.Plain)
        line.setStyleSheet('color: palette(mid);')
        outer.addWidget(line)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        side = QWidget()
        scroll.setWidget(side)
        column = QVBoxLayout(side)
        column.setContentsMargins(d.SPACE_L, d.SPACE_L, d.SPACE_L, d.SPACE_L)
        column.setSpacing(d.SPACE_S)
        head = QHBoxLayout()
        title = QLabel('Suggestions')
        font = title.font()
        font.setPointSize(d.TEXT_TITLE)
        font.setBold(True)
        title.setFont(font)
        head.addWidget(title, 1)
        hide = QPushButton('Hide')
        hide.setFlat(True)
        hide.clicked.connect(lambda: self.show_panel(False))
        head.addWidget(hide)
        column.addLayout(head)
        column.addWidget(caption('From the last clean up. Dismiss what you’ve dealt with.'))
        self.suggestion_list = QVBoxLayout()
        self.suggestion_list.setSpacing(d.SPACE_S)
        column.addLayout(self.suggestion_list)
        self.suggestion_empty = caption('')
        column.addWidget(self.suggestion_empty)
        column.addStretch(1)
        self.panel.setVisible(show)

    # Purpose
    def _purpose_changed(self, text):
        if not self.updating:
            self.cleanup.set_purpose(text)

    def pick_purpose(self, text):
        self.updating = True
        self.purpose.setText(text)
        self.updating = False
        self.cleanup.set_purpose(text, remember=True)

    def _fill_purposes(self, menu):
        menu.clear()
        menu.addAction('General purpose (blank)', lambda: self.pick_purpose(''))
        menu.addSection('Presets')
        for preset in PRESETS:
            menu.addAction(preset, lambda p=preset: self.pick_purpose(p))
        recent = self.cleanup.recent_purposes()
        if recent:
            menu.addSection('Recent')
            for purpose in recent:
                menu.addAction(purpose, lambda p=purpose: self.pick_purpose(p))

    # Versions
    def _fill_versions(self, menu):
        menu.clear()
        versions = self.cleanup.versions()
        current = self.cleanup.current_index()
        menu.addSection('Versions of this draft (last 20 kept)')
        if not versions:
            menu.addAction('Clean up saves a version first').setEnabled(False)
        for index in reversed(range(len(versions))):
            version = versions[index]
            text = (f'Version {index + 1}' + ('  (showing)' if index == current else '')
                    + f'  ·  {version["label"]} · {_time(version["created_at"])}')
            menu.addAction(text, lambda i=index: self.window.perform(
                lambda: self.cleanup.show_version(i)))

    def _banner_clicked(self):
        kind = self.cleanup.problem[0] if self.cleanup.problem else None
        self.cleanup.problem = None
        if kind == 'setup':
            self.window.show_settings()
        else:
            self.window.perform(self.cleanup.start)
        self.refresh()

    def show_panel(self, shown):
        self.panel.setVisible(shown)
        self.cleanup.store.set_setting('suggestions_panel', '1' if shown else '0')

    def refresh(self):
        c, cleanup, window = self.window.controller, self.cleanup, self.window
        if c.closed:
            return
        busy_here = bool(cleanup.busy and cleanup.busy['draft_id'] == c.draft.id)
        if self.purpose.text() != cleanup.purpose:
            self.updating = True
            self.purpose.setText(cleanup.purpose)
            self.updating = False
        self.clean_button.setText('Cleaning up…' if cleanup.busy else 'Clean up')
        self.clean_button.setEnabled(cleanup.can_start())
        versions = cleanup.versions()
        current = cleanup.current_index()
        self.version_menu.setText(
            f'Version {current + 1} of {len(versions)}' if versions else 'No versions yet')
        self.revert_button.setEnabled(bool(current) and not busy_here)
        self.version_menu.setEnabled(bool(versions) and not busy_here)
        window.set_locked(busy_here)
        version_id, suggestions = cleanup.suggestions()
        while self.suggestion_list.count():
            item = self.suggestion_list.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for index, text in suggestions:
            card = QFrame()
            card.setObjectName('card')
            row = QHBoxLayout(card)
            row.setContentsMargins(d.SPACE_M, d.SPACE_S, d.SPACE_XS, d.SPACE_S)
            body = QLabel(text)
            body.setWordWrap(True)
            body.setTextFormat(Qt.PlainText)
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            row.addWidget(body, 1)
            dismiss = QToolButton()
            dismiss.setText('✕')
            dismiss.setAutoRaise(True)
            dismiss.setToolTip('Dismiss suggestion')
            dismiss.setAccessibleName('Dismiss suggestion')
            dismiss.clicked.connect(lambda _=False, v=version_id, i=index: cleanup.dismiss(v, i))
            row.addWidget(dismiss, 0, Qt.AlignTop)
            self.suggestion_list.addWidget(card)
        self.panel_button.setText(f'Suggestions  {len(suggestions)}')
        self.suggestion_empty.setVisible(not suggestions)
        self.suggestion_empty.setText(
            'Press Clean up to get suggestions.' if version_id is None
            else 'All dismissed. A new clean up brings fresh ones.')
        problem = cleanup.problem
        self.banner.setVisible(bool(problem))
        if problem:
            kind, message = problem
            self.banner_text.setText(message + ('. Add one in Settings.' if kind == 'setup' else ''))
            self.banner_button.setText('Open Settings' if kind == 'setup' else 'Try again')
            self.banner.setProperty('error', kind != 'setup')
            self.banner.style().unpolish(self.banner)
            self.banner.style().polish(self.banner)
        window.render_status()


class SettingsDialog(QDialog):
    """Clean up provider, API key, purposes and the recording shortcut. Saves on close."""

    def __init__(self, window, cleanup):
        super().__init__(window)
        # Named main: an attribute called window would hide QWidget.window().
        self.main, self.cleanup = window, cleanup
        self.setWindowTitle('Settings')
        self.setMinimumWidth(520)
        config = cleanup.config()
        self.loaded_key = None
        self.address_shown = config['base_url']
        layout = QVBoxLayout(self)
        layout.setSpacing(d.SPACE_L)

        # Wrapped notes go in a plain column: a form row doesn't give them their full height.
        model_group = QGroupBox('Clean up model')
        column = QVBoxLayout(model_group)
        column.setSpacing(d.SPACE_S)
        column.addWidget(caption('Any service that speaks the OpenAI-style API, or Claude. Only '
                                 'the Clean up button sends text; dictation never leaves this '
                                 'computer.'))
        form = QFormLayout()
        form.setSpacing(d.SPACE_S)
        column.addLayout(form)
        self.names = [name for name, _url, _key in llm.PROVIDERS]
        self.provider = QComboBox()
        self.provider.addItems(self.names)
        self.provider.setCurrentIndex(self.names.index(config['provider'])
                                      if config['provider'] in self.names else len(self.names) - 1)
        form.addRow('Provider', self.provider)
        self.address = QLineEdit(config['base_url'])
        form.addRow('Address', self.address)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText('Loading…')
        form.addRow('API key', self.key)
        self.model = QLineEdit(config['model'])
        form.addRow('Model', self.model)
        column.addWidget(caption('The key is kept in Windows Credential Manager, not in '
                                 'Dogspeak’s files. OpenRouter model names look like '
                                 'provider/model (openrouter.ai/models); Claude model names look '
                                 'like claude-opus-5-5 or claude-haiku-5-5.'))
        test_row = QHBoxLayout()
        self.test_result = caption('Free: checks the key and model without using tokens.')
        test_row.addWidget(self.test_result, 1)
        self.test_button = QPushButton('Test')
        self.test_button.clicked.connect(self.test)
        test_row.addWidget(self.test_button, 0, Qt.AlignTop)
        column.addLayout(test_row)
        layout.addWidget(model_group)

        shortcut_group = QGroupBox('Recording shortcut')
        shortcut = QFormLayout(shortcut_group)
        current = cleanup.store.get_setting('hotkey', hotkeys.DEFAULT) or hotkeys.DEFAULT
        self.shortcut = QKeySequenceEdit(QKeySequence(current))
        self.shortcut.setMaximumSequenceLength(1)
        self.shortcut.setClearButtonEnabled(True)
        reset = QPushButton('Default')
        reset.clicked.connect(lambda: self.shortcut.setKeySequence(QKeySequence(hotkeys.DEFAULT)))
        row = QHBoxLayout()
        row.addWidget(self.shortcut, 1)
        row.addWidget(reset)
        shortcut.addRow('Record / stop', row)
        self.shortcut_note = caption('Works from any app. Enter stops a recording and Esc '
                                     'cancels it, while one is running.')
        shortcut.addRow(self.shortcut_note)
        layout.addWidget(shortcut_group)

        purposes = QGroupBox('Purpose list')
        purpose_row = QHBoxLayout(purposes)
        purpose_row.addWidget(caption('The menu shows the presets plus your last 5 typed '
                                      'purposes.'), 1)
        self.clear = QPushButton('Clear recent')
        self.clear.setEnabled(bool(cleanup.recent_purposes()))
        self.clear.clicked.connect(lambda: (cleanup.clear_recent(), self.clear.setEnabled(False)))
        purpose_row.addWidget(self.clear, 0, Qt.AlignVCenter)
        layout.addWidget(purposes)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.address.textChanged.connect(self._address_changed)
        self._load_key(config['base_url'])

    def showEvent(self, event):
        super().showEvent(event)
        # Qt sizes a dialog without its wrapped notes; grow to what they need.
        needed = self.layout().heightForWidth(self.width())
        if needed > self.height():
            self.resize(self.width(), needed)

    def _load_key(self, url):
        def work():
            try:
                key = self.cleanup.credentials.get(url)
            except Exception:
                key = ''
            self.cleanup.dispatch(self._show_key, url, key)
        self.cleanup.spawn(work)

    def _show_key(self, url, key):
        if url == self.address.text().strip():
            self.loaded_key = key
            self.key.setText(key)
            self.key.setPlaceholderText('Paste the provider’s API key')
        return False

    def _provider_changed(self, index):
        _name, url, _needs = llm.PROVIDERS[index]
        if url:
            self.address.setText(url)
        suggested = llm.DEFAULT_MODELS.get(url)
        current = self.model.text().strip()
        if suggested and not current.startswith(suggested.split('-')[0] + '-'):
            self.model.setText(suggested)

    def _address_changed(self, text):
        url = text.strip()
        if url != self.address_shown:
            self.address_shown = url
            self.loaded_key = None
            self.key.setText('')
            self.key.setPlaceholderText('Loading…')
            self._load_key(url)

    def save(self):
        url = self.address.text().strip()
        self.cleanup.save_config(self.names[self.provider.currentIndex()], url, self.model.text())
        key = self.key.text().strip()
        if self.loaded_key is not None and key != self.loaded_key:
            self.cleanup.credentials.set(url, key)
            self.loaded_key = key

    def save_shortcut(self):
        text = self.shortcut.keySequence().toString(QKeySequence.PortableText)
        saved = self.cleanup.store.get_setting('hotkey', hotkeys.DEFAULT) or hotkeys.DEFAULT
        if not text or hotkeys.label(text) == saved:
            return None
        return self.main.apply_shortcut(hotkeys.label(text))

    def test(self):
        try:
            self.save()
        except Exception as exc:
            self._done(f'Could not save the key: {exc}', False)
            return
        self.test_button.setEnabled(False)
        self.test_button.setText('Testing…')
        url, key, name = self.address.text().strip(), self.key.text().strip(), self.model.text()

        def work():
            try:
                lines = self.cleanup.client_factory(url, key, name).test()
                result = ('Connected · ' + ' · '.join(lines), True)
            except llm.LLMError as exc:
                result = (str(exc), False)
            except Exception as exc:
                result = (f'Test failed: {exc}', False)
            self.cleanup.dispatch(self._done, *result)
        self.cleanup.spawn(work)

    def _done(self, message, ok):
        self.test_button.setEnabled(True)
        self.test_button.setText('Test')
        self.test_result.setText(message)
        self.test_result.setStyleSheet(d.CAPTION_STYLE if ok else d.DANGER_STYLE)
        return False

    def done(self, result):
        problem = self.save_shortcut()
        try:
            self.save()
        except Exception as exc:
            self.cleanup.problem = ('failed', f'Could not save the API key: {exc}')
            self.cleanup.on_change()
        if problem:
            self.shortcut_note.setText(problem)
            self.shortcut_note.setStyleSheet(d.DANGER_STYLE)
            self.shortcut.setFocus()
            return  # stay open so the person can pick another shortcut
        super().done(result)
