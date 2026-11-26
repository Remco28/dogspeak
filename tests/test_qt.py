"""The Qt window (Windows frontend), headless. Needs PySide6 from the dev group."""
import os
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QGuiApplication, QKeyEvent
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - setup.sh installs without dev dependencies
    QApplication = None


class FakeEngine:
    def __init__(self, callback):
        self.callback = callback
        self.jobs = []
        self.closed = False

    def start(self):
        self.callback({'type': 'loading'})

    def transcribe(self, path, request_id):
        self.jobs.append((Path(path), request_id))

    def close(self):
        self.closed = True

    def emit(self, kind, **values):
        self.callback({'type': kind, **values})


class FakeRecorder:
    def __init__(self):
        self.running = False
        self.starts = []

    def start(self, path, target=None):
        self.path = Path(path)
        self.path.write_bytes(b'test audio')
        self.starts.append((self.path, target))
        self.running = True

    def stop(self):
        self.running = False
        return self.path

    def cancel(self):
        self.running = False


class FakeHotkeys:
    available = True

    def __init__(self, window=None, refuse=()):
        self.refuse = set(refuse)
        self.shortcut = None
        self.callback = None
        self.session = ()
        self.session_callback = None
        self.closed = False

    def set_shortcut(self, text, callback):
        if text in self.refuse:
            return f'{text} is already used by another app; choose another in Settings'
        self.shortcut, self.callback = text, callback
        return None

    def set_session_keys(self, keys, callback):
        self.session, self.session_callback = tuple(keys), callback

    def close(self):
        self.closed = True
        self.session = ()


class FakeKeyring:
    def __init__(self):
        self.keys = {}

    def get(self, address):
        return self.keys.get(address, '')

    def set(self, address, key):
        self.keys[address] = key

    def clear(self, address):
        self.keys.pop(address, None)


class FakeClient:
    result = ('Clean text.', ['Say who it is for'])

    def __init__(self, base_url, key, model):
        self.args = (base_url, key, model)

    def clean_up(self, text, purpose):
        return self.result

    def test(self):
        return ['Key accepted', 'Model found']


@unittest.skipIf(QApplication is None, 'PySide6 is not installed (uv sync installs it)')
class QtWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from dogspeak_app import qt_app
        from dogspeak_app.cleanup import Cleanup
        from dogspeak_app.controller import Controller
        from dogspeak_app.storage import Store
        self.qt_app = qt_app
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'test.sqlite3')
        self.recorder = FakeRecorder()
        self.controller = Controller(self.store, FakeEngine, self.recorder, self.root,
                                     dispatch=lambda fn, *args: fn(*args), spawn=lambda fn: fn())
        self.engine = self.controller.engine
        self.keyring = FakeKeyring()
        self.cleanup = Cleanup(self.controller, self.keyring, client_factory=FakeClient)
        self.hotkeys = FakeHotkeys()
        self.window = qt_app.ScratchpadWindow(
            self.controller, lambda: [{'name': 'mic-1', 'description': 'USB microphone'}],
            self.cleanup, hotkeys_factory=lambda _w: self.hotkeys, tray=False)
        self.addCleanup(self._close)
        self.controller.focused = lambda: True

    def _close(self):
        self.window.shutdown()
        if not self.controller.closed:
            self.controller.close()
        self.window.deleteLater()
        self.window.pill.deleteLater()

    def ready(self):
        self.engine.emit('ready', load_seconds=0.1, pid=1)

    def record_and_transcribe(self, text):
        c = self.controller
        self.assertTrue(self.window.toggle_recording())
        c.stop_recording()
        _path, request_id = self.engine.jobs[-1]
        self.engine.emit('result', id=request_id, path='x.wav', text=text, seconds=0.1)

    def test_starts_loading_then_ready_with_the_shortcut_in_the_status(self):
        self.assertEqual(self.hotkeys.shortcut, 'Ctrl+Alt+R')
        self.assertIn('Loading', self.window.status_label.text())
        self.assertFalse(self.window.record_button.isEnabled())
        self.ready()
        self.assertEqual(self.window.status_label.text(), 'Ready · Ctrl+Alt+R')
        self.assertTrue(self.window.record_button.isEnabled())
        self.assertEqual(self.window.microphone.itemText(1), 'USB microphone')

    def test_status_changes_are_logged_once_each_without_transcripts(self):
        with self.assertLogs('dogspeak', 'INFO') as logged:
            self.ready()
            self.window.render_status()
            self.controller.tick()
            self.window.show()
            self.window.last_edit = float('-inf')
            self.record_and_transcribe('a private sentence')
        self.assertIn('INFO:dogspeak:status ready: Ready · Ctrl+Alt+R', logged.output)
        self.assertEqual(len([line for line in logged.output if 'status ready: Ready' in line]), 2)
        self.assertFalse(any('private sentence' in line for line in logged.output))

    def test_recording_shows_the_pill_and_holds_enter_and_esc_until_done(self):
        self.window.show()
        self.ready()
        self.window.editor.setPlainText('Hello world')
        self.window.editor.moveCursor(self.window.editor.textCursor().MoveOperation.End)
        self.window.last_edit = float('-inf')  # not typing
        self.window.toggle_recording()
        self.assertEqual(self.window.record_button.text(), '■  Stop')
        self.assertTrue(self.window.pill.isVisible())
        self.assertEqual(self.window.pill.state, 'recording')
        self.assertEqual(self.window.pill.hint, 'Enter stops · Esc cancels')
        self.assertEqual(self.hotkeys.session, ('stop', 'cancel'))
        self.hotkeys.session_callback('stop')  # Enter, pressed in another app
        self.assertEqual(self.hotkeys.session, ('cancel',))
        _path, request_id = self.engine.jobs[-1]
        self.engine.emit('result', id=request_id, path='x.wav', text='and more', seconds=0.1)
        self.assertEqual(self.window.editor.text(), 'Hello world\n\nand more')
        self.assertEqual(self.window.pill.state, 'saved')
        self.assertEqual(self.hotkeys.session, ())
        self.assertEqual(self.store.get_draft(self.controller.draft.id).content,
                         'Hello world\n\nand more')

    def test_transcript_goes_after_the_word_the_cursor_is_in_and_is_one_undo_step(self):
        self.window.show()
        self.ready()
        editor = self.window.editor
        editor.setPlainText('Fix the 🐛 parser today')
        self.window._changed()
        cursor = editor.textCursor()
        cursor.setPosition(len('Fix the 🐛 par'.encode('utf-16-le')) // 2)
        editor.setTextCursor(cursor)
        self.window.last_edit = float('-inf')
        self.record_and_transcribe('quickly')
        self.assertEqual(editor.text(), 'Fix the 🐛 parser quickly today')
        editor.undo()
        self.assertEqual(editor.text(), 'Fix the 🐛 parser today')

    def test_recording_started_from_another_app_goes_to_the_end_and_the_clipboard(self):
        self.ready()
        self.window.editor.setPlainText('First line')
        self.window._changed()
        self.controller.focused = lambda: False
        self.record_and_transcribe('From elsewhere.')
        self.assertEqual(self.window.editor.text(), 'First line\n\nFrom elsewhere.')
        self.assertEqual(QGuiApplication.clipboard().text(), 'From elsewhere.')
        self.assertEqual(self.window.pill.hint, 'Copied · paste with Ctrl+V')

    def test_result_waits_while_typing(self):
        self.window.show()
        self.ready()
        self.window.last_edit = self.controller.clock()
        self.record_and_transcribe('later')
        self.assertIn('pause typing', self.window.status_label.text())
        self.assertEqual(self.window.editor.text(), '')
        self.window.last_edit = float('-inf')
        request_id = next(iter(self.controller.held))
        self.controller._event(self.controller.held[request_id])
        self.assertEqual(self.window.editor.text(), 'later')

    def test_escape_in_the_window_cancels_a_recording(self):
        self.ready()
        self.window.toggle_recording()
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        QApplication.sendEvent(self.window.editor, event)
        self.assertIsNone(self.controller.recording_job)
        self.assertEqual(self.controller.outcome, 'cancelled')
        self.assertEqual(self.window.pill.title, 'Cancelled')

    def test_refused_shortcut_from_another_app_explains_on_the_pill(self):
        self.window.toggle_recording()  # still loading
        self.assertEqual(self.window.pill.title, 'Dogspeak is loading…')
        self.ready()
        self.assertEqual(self.window.pill.title, 'Dogspeak is ready')

    def test_move_lines_swaps_paragraphs_and_keeps_the_blank_line(self):
        editor = self.window.editor
        editor.setPlainText('one\n\ntwo\nthree')
        editor.moveCursor(editor.textCursor().MoveOperation.Start)
        editor.move_lines(True)
        self.assertEqual(editor.text(), 'two\nthree\n\none')
        self.assertEqual(editor.textCursor().blockNumber(), 3)
        editor.move_lines(False)
        self.assertEqual(editor.text(), 'one\n\ntwo\nthree')

    def test_drafts_keep_their_own_undo_and_can_be_deleted(self):
        first = self.controller.draft.id
        self.window.editor.insertPlainText('first draft')
        self.controller.new_draft()
        self.assertEqual(self.window.editor.text(), '')
        self.window.editor.insertPlainText('second')
        self.controller.open_draft(first)
        self.assertEqual(self.window.editor.text(), 'first draft')
        self.window.editor.undo()
        self.assertEqual(self.window.editor.text(), '')
        dialog = self.window.show_drafts()
        self.assertEqual(dialog.rows.count(), 2)
        dialog.reject()
        self.assertTrue(self.window.delete_draft(first))
        self.assertNotEqual(self.controller.draft.id, first)
        self.assertEqual(self.window.editor.text(), 'second')

    def test_clean_up_replaces_text_as_one_undo_step_and_shows_suggestions(self):
        self.store.set_setting('llm_model', 'some/model')
        self.keyring.set('https://openrouter.ai/api/v1', 'sk-test')
        self.window.editor.setPlainText('um so fix the thing')
        self.window._changed()
        self.assertTrue(self.cleanup.start())
        self.assertEqual(self.window.editor.text(), 'Clean text.')
        self.assertEqual(self.window.cleanup_view.panel_button.text(), 'Suggestions  1')
        self.assertEqual(self.window.cleanup_view.version_menu.text(), 'Version 2 of 2')
        self.window.editor.undo()
        self.assertEqual(self.window.editor.text(), 'um so fix the thing')

    def test_clean_up_without_a_key_shows_the_setup_banner(self):
        self.store.set_setting('llm_model', 'some/model')
        self.window.editor.setPlainText('text')
        self.window._changed()
        self.cleanup.start()
        self.assertFalse(self.window.cleanup_view.banner.isHidden())
        self.assertIn('API key', self.window.cleanup_view.banner_text.text())
        self.assertEqual(self.window.cleanup_view.banner_button.text(), 'Open Settings')

    def test_keys_in_dialogs_reach_the_filter_without_error(self):
        # Dialogs once kept the main window in self.window, hiding QWidget.window().
        for dialog in (self.window.show_settings(), self.window.show_drafts()):
            event = QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
            self.assertFalse(self.window.eventFilter(dialog, event))
            self.assertIs(dialog.window(), dialog)
            dialog.reject()

    def test_suggestions_button_shows_and_hides_the_panel(self):
        view = self.window.cleanup_view
        self.assertFalse(view.panel.isHidden())
        view.panel_button.click()
        self.assertTrue(view.panel.isHidden())
        self.assertEqual(self.store.get_setting('suggestions_panel'), '0')
        view.panel_button.click()
        self.assertFalse(view.panel.isHidden())

    def test_settings_saves_the_key_and_refuses_a_taken_shortcut(self):
        from PySide6.QtGui import QKeySequence
        self.hotkeys.refuse = {'Ctrl+Alt+P'}
        dialog = self.window.show_settings()
        dialog.key.setText('sk-new')
        dialog.model.setText('anthropic/claude-haiku')
        dialog.shortcut.setKeySequence(QKeySequence('Ctrl+Alt+P'))
        dialog.done(0)
        self.assertTrue(dialog.isVisible(), 'a refused shortcut keeps Settings open')
        self.assertIn('already used', dialog.shortcut_note.text())
        self.assertEqual(self.hotkeys.shortcut, 'Ctrl+Alt+R')
        dialog.shortcut.setKeySequence(QKeySequence('Ctrl+Alt+D'))
        dialog.done(0)
        self.assertFalse(dialog.isVisible())
        self.assertEqual(self.hotkeys.shortcut, 'Ctrl+Alt+D')
        self.assertEqual(self.store.get_setting('hotkey'), 'Ctrl+Alt+D')
        self.assertEqual(self.keyring.get('https://openrouter.ai/api/v1'), 'sk-new')
        self.assertEqual(self.store.get_setting('llm_model'), 'anthropic/claude-haiku')
        self.ready()
        self.assertEqual(self.window.status_label.text(), 'Ready · Ctrl+Alt+D')

    def test_closing_the_window_hides_it_and_quit_stops_the_engine(self):
        self.window.show()
        self.window.close()
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.controller.closed)
        self.window.editor.setPlainText('keep me')
        self.window._changed()
        quits = []
        self.window._closed = lambda: quits.append(True)
        self.window.quit_session()
        self.assertTrue(self.controller.closed)
        self.assertTrue(self.engine.closed)
        self.assertEqual(quits, [True])


@unittest.skipIf(QApplication is None, 'PySide6 is not installed (uv sync installs it)')
class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_second_launch_hands_its_command_to_the_first(self):
        import subprocess
        import sys
        from dogspeak_app.qt_app import SingleInstance
        name = f'dogspeak-test-{uuid.uuid4().hex[:8]}'
        first = SingleInstance(name)
        self.assertFalse(first.send({'action': 'show'}, timeout=200))
        self.assertTrue(first.listen())
        self.addCleanup(first.close)
        received = []
        first.on_command = received.append
        # A real second process, as when dogspeak.cmd runs while Dogspeak is open.
        second = subprocess.Popen([sys.executable, '-c', (
            'import sys; from PySide6.QtCore import QCoreApplication; '
            'from dogspeak_app.qt_app import SingleInstance; app = QCoreApplication([]); '
            f'sys.exit(0 if SingleInstance({name!r}).send('
            '{"action": "import-audio", "path": "C:\\\\clip.wav"}, timeout=5000) else 3)')],
            cwd=str(Path(__file__).resolve().parents[1]))
        for _ in range(1000):
            self.app.processEvents()
            if received and second.poll() is not None:
                break
            threading.Event().wait(0.01)
        self.assertEqual(second.wait(timeout=10), 0)
        self.assertEqual(received, [{'action': 'import-audio', 'path': 'C:\\clip.wav'}])

    def test_many_hand_offs_all_arrive(self):
        """Windows named pipes lost 3-10 of 20 lines before the first instance answered."""
        import subprocess
        import sys
        from dogspeak_app.qt_app import SingleInstance
        name = f'dogspeak-test-{uuid.uuid4().hex[:8]}'
        first = SingleInstance(name)
        self.assertTrue(first.listen())
        self.addCleanup(first.close)
        received = []
        first.on_command = received.append
        script = ('import sys; from PySide6.QtCore import QCoreApplication; '
                  'from dogspeak_app.qt_app import SingleInstance; app = QCoreApplication([]); '
                  f'sys.exit(0 if SingleInstance({name!r}).send('
                  '{"action": "show", "n": int(sys.argv[1])}, timeout=5000) else 3)')
        root = str(Path(__file__).resolve().parents[1])
        for n in range(10):
            second = subprocess.Popen([sys.executable, '-c', script, str(n)], cwd=root)
            while second.poll() is None:
                self.app.processEvents()
                threading.Event().wait(0.005)
            self.assertEqual(second.returncode, 0)
            for _ in range(50):
                self.app.processEvents()
        self.assertEqual([c['n'] for c in received], list(range(10)))

if __name__ == '__main__':
    unittest.main()
