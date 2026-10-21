"""Run with: xvfb-run -a /usr/bin/python3 -m unittest discover -s tests -p test_gui.py -v."""
import importlib
import os

# Xvfb must win over the developer's inherited WAYLAND_DISPLAY.
os.environ['GDK_BACKEND'] = 'x11'
os.environ['GSK_RENDERER'] = 'cairo'
import contextlib
import io
import tempfile
import threading
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path


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
        self.stopped_thread = None

    def start(self, path, target=None):
        self.path = Path(path)
        self.path.write_bytes(b'test audio')
        self.starts.append((self.path, target))
        self.running = True

    def stop(self):
        self.stopped_thread = threading.get_ident()
        self.running = False
        return self.path

    def cancel(self):
        self.running = False


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.gui = importlib.import_module('dogspeak_app.gui')
        from dogspeak_app.storage import Store
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'test.sqlite3')
        self.recorder = FakeRecorder()
        self.controller = self.gui.Controller(
            self.store, FakeEngine, self.recorder, self.root,
            dispatch=lambda fn, *args: fn(*args), spawn=lambda fn: fn(),
        )
        self.addCleanup(self.controller.close)
        self.engine = self.controller.engine

    def test_edit_new_and_reopen_flush_latest_content(self):
        c = self.controller
        original = c.draft.id
        c.edit('My prompt', 'Typed before model readiness')
        c.new_draft()
        self.assertNotEqual(c.draft.id, original)
        c.open_draft(original)
        self.assertEqual(c.draft.title, 'My prompt')
        self.assertEqual(c.draft.content, 'Typed before model readiness')
        self.assertEqual(self.store.get_setting('last_draft'), original)

    def test_result_appends_to_origin_preserves_live_edits_and_is_idempotent(self):
        c = self.controller
        self.engine.emit('ready')
        original = c.draft.id
        audio = self.root / 'input.wav'
        audio.write_bytes(b'audio')
        self.assertTrue(c.import_audio(audio))
        path, request_id = self.engine.jobs[-1]
        c.edit('Edited title', 'Newest typing')
        self.engine.emit('result', id=request_id, text='ASR output', path=str(path))
        self.assertEqual(c.draft.content, 'Newest typing\n\nASR output')
        self.assertEqual(c.draft.title, 'Edited title')
        self.assertEqual(self.store.list_segments(original)[0]['text'], 'ASR output')
        self.engine.emit('result', id=request_id, text='ASR output')
        self.assertEqual(c.draft.content.count('ASR output'), 1)
        self.assertTrue(c.import_audio(audio))
        path, request_id = self.engine.jobs[-1]
        c.new_draft()
        c.edit('Other', 'Do not touch')
        self.engine.emit('result', id=request_id, text='Second result')
        self.assertEqual(c.content, 'Do not touch')
        self.assertEqual(self.store.get_draft(original).content,
                         'Newest typing\n\nASR output\n\nSecond result')

    def test_recording_requires_explicit_ready_toggle_and_autostops(self):
        c = self.controller
        self.assertFalse(c.toggle_recording())
        self.engine.emit('ready')
        self.assertEqual(self.recorder.starts, [])
        c.source = 'alsa_input.test'
        self.assertTrue(c.toggle_recording())
        self.assertEqual(c.state, 'recording')
        self.assertEqual(self.recorder.starts[-1][1], 'alsa_input.test')
        origin = c.draft.id
        c.new_draft()
        c.clock = lambda: c.started_at + 116
        c.tick()
        self.assertEqual(c.state, 'transcribing')
        self.assertFalse(self.recorder.running)
        self.assertFalse(c.toggle_recording())
        self.assertFalse(c.import_audio(self.root / 'input.wav'))
        path, request_id = self.engine.jobs[-1]
        self.engine.emit('result', id=request_id, text='Origin note')
        self.assertEqual(self.store.get_draft(origin).content, 'Origin note')
        self.assertEqual(c.draft.content, '')

    def test_explicit_import_during_loading_is_queued_but_never_records(self):
        audio = self.root / 'import.wav'
        audio.write_bytes(b'original')
        self.assertTrue(self.controller.import_audio(audio))
        self.assertEqual(self.engine.jobs, [])
        self.engine.emit('ready')
        self.assertEqual(len(self.engine.jobs), 1)
        copied, _ = self.engine.jobs[0]
        self.assertNotEqual(copied, audio)
        self.assertEqual(copied.read_bytes(), b'original')
        self.assertEqual(copied.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.recorder.starts, [])

    def test_fifo_import_is_rejected_without_blocking(self):
        fifo = self.root / 'pipe.wav'
        os.mkfifo(fifo)
        started = time.monotonic()
        self.assertFalse(self.controller.import_audio(fifo))
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.engine.jobs, [])
        self.assertEqual(self.controller.pending, {})
        self.assertEqual(self.controller.state, 'error')
        self.assertIn('regular file', self.controller.message)

    def test_oversized_import_is_rejected_before_copy(self):
        audio = self.root / 'huge.wav'
        audio.write_bytes(b'audio')
        real_stat = os.fstat
        def fake_stat(fd):
            info = real_stat(fd)
            return os.stat_result((
                info.st_mode, info.st_ino, info.st_dev, info.st_nlink, info.st_uid,
                info.st_gid, self.gui.MAX_IMPORT_BYTES + 1, info.st_atime,
                info.st_mtime, info.st_ctime))
        original = os.fstat
        os.fstat = fake_stat
        try:
            self.assertFalse(self.controller.import_audio(audio))
        finally:
            os.fstat = original
        self.assertEqual(self.engine.jobs, [])
        self.assertEqual(self.controller.pending, {})
        self.assertIn('256 MiB', self.controller.message)
        self.assertEqual(list((self.root / 'recordings').glob('*')), [])

    def test_import_origin_survives_draft_switch_during_copy(self):
        c = self.controller
        self.engine.emit('ready')
        origin = c.draft.id
        audio = self.root / 'import.wav'
        audio.write_bytes(b'copied-bytes')
        copies = []
        c.spawn = copies.append
        self.assertTrue(c.import_audio(audio))
        self.assertTrue(c.pending)
        self.assertEqual(self.engine.jobs, [])
        self.assertFalse(c.import_audio(audio))
        c.new_draft()
        copies.pop()()
        path, request_id = self.engine.jobs[-1]
        self.assertEqual(path.read_bytes(), b'copied-bytes')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.engine.emit('result', id=request_id, text='Imported speech')
        self.assertEqual(self.store.get_draft(origin).content, 'Imported speech')
        self.assertEqual(c.draft.content, '')
        c.spawn = lambda fn: fn()

    def test_fatal_error_cancels_queued_import_copy(self):
        audio = self.root / 'import.wav'
        audio.write_bytes(b'copied-bytes')
        copies = []
        self.controller.spawn = copies.append
        self.assertTrue(self.controller.import_audio(audio))
        destination = next(iter(self.controller.pending.values()))['path']
        copy_fn = copies.pop()
        self.controller.spawn = lambda fn: fn()
        self.engine.emit('error', fatal=True, message='worker died')
        copy_fn()
        self.assertEqual(self.engine.jobs, [])
        self.assertFalse(destination.exists())
        self.assertEqual(self.controller.pending, {})

    def test_close_cancels_in_flight_import_copy(self):
        audio = self.root / 'import.wav'
        audio.write_bytes(b'copied-bytes')
        copies = []
        self.controller.spawn = copies.append
        self.assertTrue(self.controller.import_audio(audio))
        request_id = next(iter(self.controller.pending))
        destination = self.controller.pending[request_id]['path']
        copy_fn = copies.pop()
        self.controller.spawn = lambda fn: fn()
        self.controller.close()
        copy_fn()
        self.assertEqual(self.engine.jobs, [])
        self.assertFalse(destination.exists())

    def test_close_flushes_even_on_error_and_cleans_both_owned_resources(self):
        c = self.controller
        c.edit('Last title', 'Unsaved latest text')
        called = []
        original_close = self.store.close
        def close_store():
            called.append('store')
            original_close()
        self.store.close = close_store
        def failed_cancel():
            called.append('recorder')
            raise RuntimeError('capture already failed')
        self.recorder.cancel = failed_cancel
        original_engine_close = self.engine.close
        def close_engine():
            called.append('engine')
            original_engine_close()
        self.engine.close = close_engine
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            c.close(done=lambda: called.append('done'))
        self.assertIn('capture already failed', errors.getvalue())
        self.assertEqual(called, ['store', 'recorder', 'engine', 'done'])
        self.assertTrue(c.closed)
        from dogspeak_app.storage import Store
        with_store = Store(self.root / 'test.sqlite3')
        try:
            self.assertEqual(with_store.get_draft(c.draft.id).content, 'Unsaved latest text')
        finally:
            with_store.close()

    def test_failed_close_retains_unsaved_draft_and_store_for_retry(self):
        c = self.controller
        self.engine.emit('ready')
        c.toggle_recording()
        c.edit('Unsaved title', 'Irreplaceable text')
        save = self.store.save_draft
        def fail_save(*args):
            raise OSError('disk full')
        self.store.save_draft = fail_save
        done, cleanup = [], []
        c.spawn = cleanup.append
        self.assertFalse(c.close(done=lambda: done.append(True)))
        self.assertFalse(c.closed)
        self.assertTrue(c.dirty)
        self.assertEqual(c.content, 'Irreplaceable text')
        self.assertEqual(self.store.get_draft(c.draft.id).content, '')
        self.assertEqual(done, [])
        self.assertEqual(len(cleanup), 1)
        cleanup.pop()()
        self.assertFalse(self.recorder.running)
        self.assertTrue(self.engine.closed)
        self.assertIn('disk full', c.message)
        self.assertFalse(c.toggle_recording())
        self.engine.emit('ready')
        self.assertFalse(c.ready)
        self.store.save_draft = save
        self.assertTrue(c.close(done=lambda: done.append(True)))
        self.assertEqual(done, [True])
        self.assertTrue(c.closed)
        from dogspeak_app.storage import Store
        reopened = Store(self.root / 'test.sqlite3')
        try:
            self.assertEqual(reopened.get_draft(c.draft.id).content, 'Irreplaceable text')
        finally:
            reopened.close()
        c.spawn = lambda fn: fn()

    def test_failed_capture_is_cancelled_and_reports_error(self):
        self.engine.emit('ready')
        self.controller.toggle_recording()
        def failed_stop():
            raise RuntimeError('device disappeared')
        self.recorder.stop = failed_stop
        self.controller.stop_recording()
        self.assertFalse(self.recorder.running)
        self.assertEqual(self.controller.state, 'error')
        self.assertIn('device disappeared', self.controller.message)
        self.assertEqual(self.engine.jobs, [])

    def test_watchdog_uses_capture_start_despite_later_errors(self):
        c = self.controller
        now = [100.0]
        c.clock = lambda: now[0]
        self.engine.emit('ready')
        c.toggle_recording()
        now[0] = 214.0
        c.status('error', 'Could not save draft: disk full')
        c.tick()
        self.assertTrue(self.recorder.running)
        now[0] = 215.0
        c.tick()
        self.assertFalse(self.recorder.running)
        self.assertEqual(len(self.engine.jobs), 1)
        c.tick()
        self.assertEqual(len(self.engine.jobs), 1)

    def test_tick_detects_capture_process_death(self):
        self.engine.emit('ready')
        self.controller.toggle_recording()
        self.recorder.running = False
        def failed_stop():
            raise RuntimeError('pw-record exited')
        self.recorder.stop = failed_stop
        self.controller.tick()
        self.assertEqual(self.controller.state, 'error')
        self.assertIn('pw-record exited', self.controller.message)

    def test_fatal_error_cancels_recording_without_queuing_inference(self):
        self.engine.emit('ready')
        self.controller.toggle_recording()
        self.engine.emit('error', fatal=True, message='worker died')
        self.assertFalse(self.recorder.running)
        self.assertFalse(self.controller.ready)
        self.assertEqual(self.controller.state, 'error')
        self.assertEqual(self.engine.jobs, [])


class GtkTests(unittest.TestCase):
    def setUp(self):
        ControllerTests.setUp(self)
        self.app = self.gui.DogspeakApplication(
            controller_factory=lambda: self.controller,
            sources_provider=lambda: [{'name': 'mic.test', 'description': 'Test microphone'}],
            flags=self.gui.Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.assertEqual(self.app.get_application_id(), 'io.github.Remco28.Dogspeak')
        self.app.set_application_id('io.github.Remco28.Dogspeak.Test' + uuid.uuid4().hex)
        self.assertTrue(self.app.register(None))
        self.app.activate()
        self.window = self.app.window
        self.addCleanup(self.window.destroy)
        self.pump()

    def pump(self, until=lambda: True, timeout=2):
        context = self.gui.GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while True:
            while context.pending():
                context.iteration(False)
            if until():
                return
            if time.monotonic() > deadline:
                self.fail('Main loop did not reach expected state')
            time.sleep(0.005)

    def text(self):
        buf = self.window.buffer
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)

    def test_actual_textview_append_retains_typing_undo_and_autosaves(self):
        self.assertIsInstance(self.window.editor, self.gui.Gtk.TextView)
        self.assertTrue(self.window.buffer.get_enable_undo())
        self.window.title_entry.set_text('Title from GTK')
        buf = self.window.buffer
        buf.begin_user_action()
        buf.insert_at_cursor('Typed in GTK')
        buf.end_user_action()
        self.pump(lambda: not self.controller.dirty)
        self.assertEqual(self.store.get_draft(self.controller.draft.id).content, 'Typed in GTK')
        self.engine.emit('ready')
        audio = self.root / 'input.wav'
        audio.write_bytes(b'audio')
        self.app.activate_action('import-audio', self.gui.GLib.Variant('s', str(audio)))
        _, request_id = self.engine.jobs[-1]
        self.engine.emit('result', id=request_id, text='Spoken paragraph')
        self.assertEqual(self.text(), 'Typed in GTK\n\nSpoken paragraph')
        buf.undo()
        self.assertEqual(self.text(), 'Typed in GTK')
        buf.undo()
        self.assertEqual(self.text(), '')
        self.assertEqual(self.window.title_entry.get_text(), 'Title from GTK')

    def test_reopen_keeps_undo_history_after_background_origin_result(self):
        original = self.controller.draft.id
        buf = self.window.buffer
        buf.begin_user_action()
        buf.insert_at_cursor('Original typing')
        buf.end_user_action()
        self.engine.emit('ready')
        audio = self.root / 'input.wav'
        audio.write_bytes(b'audio')
        self.controller.import_audio(audio)
        _, request_id = self.engine.jobs[-1]
        self.controller.new_draft()
        self.engine.emit('result', id=request_id, text='Background result')
        self.assertEqual(self.text(), '')
        self.controller.open_draft(original)
        self.assertIs(self.window.buffer, buf)
        self.assertEqual(self.text(), 'Original typing\n\nBackground result')
        buf.undo()
        self.assertEqual(self.text(), 'Original typing')
        buf.undo()
        self.assertEqual(self.text(), '')

    def test_autosave_error_keeps_stop_enabled_and_finalizes_once(self):
        c = self.controller
        self.engine.emit('ready')
        c.toggle_recording()
        self.window.buffer.insert_at_cursor('Keep these edits')
        save = self.store.save_draft
        def fail_save(*args):
            raise OSError('disk full')
        self.store.save_draft = fail_save
        self.window._autosave()
        self.store.save_draft = save
        self.assertEqual(c.state, 'error')
        self.assertTrue(self.recorder.running)
        self.assertEqual(self.window.record_label.get_text(), 'Stop')
        self.assertTrue(self.window.record_button.get_sensitive())
        queued = []
        c.spawn = queued.append
        self.app.activate_action('toggle-recording', None)
        c.stop_recording()
        c.tick()
        self.assertEqual(len(queued), 1)
        queued.pop()()
        self.assertFalse(self.recorder.running)
        self.assertEqual(len(self.engine.jobs), 1)
        c.spawn = lambda fn: fn()

    def test_source_refresh_preserves_stable_selection(self):
        self.controller.source = 'mic.test'
        self.window._set_sources([{'name': 'mic.test', 'description': 'Test microphone'}])
        self.assertEqual(self.window.microphone.get_selected(), 1)
        self.assertEqual(self.controller.source, 'mic.test')
        self.assertEqual(self.store.get_setting('microphone'), 'mic.test')

    def test_background_events_and_slow_stop_do_not_block_gtk(self):
        c = self.controller
        c.dispatch = self.gui.GLib.idle_add
        c.spawn = self.gui.background
        reader = threading.Thread(target=lambda: self.engine.emit('ready'))
        reader.start()
        reader.join()
        self.assertFalse(c.ready)
        self.pump(lambda: c.ready)
        c.toggle_recording()
        entered, release = threading.Event(), threading.Event()
        original_stop = self.recorder.stop
        def slow_stop():
            entered.set()
            release.wait(2)
            return original_stop()
        self.recorder.stop = slow_stop
        c.stop_recording()
        self.assertTrue(entered.wait(1))
        heartbeat = []
        self.gui.GLib.idle_add(lambda: heartbeat.append(True))
        self.pump(lambda: bool(heartbeat))
        self.assertEqual(self.engine.jobs, [])
        release.set()
        self.pump(lambda: bool(self.engine.jobs))
        self.assertNotEqual(self.recorder.stopped_thread, threading.get_ident())
        c.spawn = lambda fn: fn()

    def test_slow_import_copy_does_not_block_gtk(self):
        c = self.controller
        c.dispatch = self.gui.GLib.idle_add
        c.spawn = self.gui.background
        reader = threading.Thread(target=lambda: self.engine.emit('ready'))
        reader.start()
        reader.join()
        self.pump(lambda: c.ready)
        audio = self.root / 'import.wav'
        audio.write_bytes(b'copied-bytes')
        entered, release = threading.Event(), threading.Event()
        original_copy = c._copy_import
        def slow_copy(*args):
            entered.set()
            release.wait(2)
            original_copy(*args)
        c._copy_import = slow_copy
        started = time.monotonic()
        self.assertTrue(c.import_audio(audio))
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(entered.wait(1))
        heartbeat = []
        self.gui.GLib.idle_add(lambda: heartbeat.append(True))
        self.pump(lambda: bool(heartbeat))
        self.assertEqual(self.engine.jobs, [])
        other = c.draft.id
        c.new_draft()
        self.assertNotEqual(c.draft.id, other)
        release.set()
        self.pump(lambda: bool(self.engine.jobs))
        path, request_id = self.engine.jobs[-1]
        self.assertEqual(path.read_bytes(), b'copied-bytes')
        self.engine.emit('result', id=request_id, text='Copied off-thread')
        self.pump(lambda: self.store.get_draft(other).content == 'Copied off-thread')
        self.assertEqual(self.text(), '')
        c.spawn = lambda fn: fn()

    def test_window_close_flushes_before_async_owned_process_cleanup(self):
        self.window.buffer.insert_at_cursor('Last unsaved keystrokes')
        self.controller.spawn = self.gui.background
        self.controller.dispatch = self.gui.GLib.idle_add
        entered, release = threading.Event(), threading.Event()
        original_close = self.engine.close
        def slow_close():
            entered.set()
            release.wait(2)
            original_close()
        self.engine.close = slow_close
        self.window.close()
        self.assertTrue(entered.wait(1))
        self.assertTrue(self.controller.closed)
        from dogspeak_app.storage import Store
        reopened = Store(self.root / 'test.sqlite3')
        try:
            self.assertEqual(reopened.get_draft(self.controller.draft.id).content,
                             'Last unsaved keystrokes')
        finally:
            reopened.close()
        heartbeat = []
        self.gui.GLib.idle_add(lambda: heartbeat.append(True))
        self.pump(lambda: bool(heartbeat))
        self.assertFalse(self.engine.closed)
        release.set()
        self.pump(lambda: self.engine.closed and not self.window.get_visible())

    def test_failed_window_close_keeps_editor_until_retry(self):
        self.window.buffer.insert_at_cursor('Keep these keystrokes')
        save = self.store.save_draft
        def fail_save(*args, **kwargs):
            raise OSError('disk full')
        self.store.save_draft = fail_save
        self.window.close()
        self.assertTrue(self.window.get_visible())
        self.assertFalse(self.controller.closed)
        self.assertFalse(self.window.closing)
        self.assertTrue(self.window.get_sensitive())
        self.assertTrue(self.window.editor.get_sensitive())
        self.assertTrue(self.window.title_entry.get_sensitive())
        self.assertTrue(self.window.tick_source)
        self.assertEqual(self.text(), 'Keep these keystrokes')
        self.assertIsInstance(self.window.save_error_dialog, self.gui.Adw.AlertDialog)
        self.app.activate_action('copy-all', None)
        copied = []
        self.window.get_clipboard().read_text_async(
            None, lambda clip, result: copied.append(clip.read_text_finish(result)))
        self.pump(lambda: bool(copied))
        self.assertEqual(copied[0], 'Keep these keystrokes')
        dialog = self.window.save_error_dialog
        self.window._on_unsaved_response(dialog, 'keep')
        dialog.force_close()
        self.pump(lambda: self.window.save_error_dialog is None)
        self.window.buffer.insert_at_cursor(' still editable')
        self.store.save_draft = save
        self.window.close()
        self.pump(lambda: self.controller.closed and not self.window.get_visible())
        from dogspeak_app.storage import Store
        reopened = Store(self.root / 'test.sqlite3')
        try:
            self.assertEqual(reopened.get_draft(self.controller.draft.id).content,
                             'Keep these keystrokes still editable')
        finally:
            reopened.close()

    def test_failed_window_close_discards_only_when_explicit(self):
        draft_id = self.controller.draft.id
        self.window.buffer.insert_at_cursor('Throw away')
        def fail_save(*args, **kwargs):
            raise OSError('disk full')
        self.store.save_draft = fail_save
        self.window.close()
        self.assertTrue(self.window.get_visible())
        self.assertEqual(self.store.get_draft(draft_id).content, '')
        dialog = self.window.save_error_dialog
        self.assertIsInstance(dialog, self.gui.Adw.AlertDialog)
        self.window._on_unsaved_response(dialog, 'discard')
        self.pump(lambda: self.controller.closed and not self.window.get_visible())
        from dogspeak_app.storage import Store
        reopened = Store(self.root / 'test.sqlite3')
        try:
            self.assertEqual(reopened.get_draft(draft_id).content, '')
        finally:
            reopened.close()

    def test_native_clipboard_and_redo(self):
        buf = self.window.buffer
        buf.begin_user_action()
        buf.insert_at_cursor('Copy this — exactly')
        buf.end_user_action()
        buf.undo()
        self.assertEqual(self.text(), '')
        buf.redo()
        self.app.activate_action('copy-all', None)
        copied = []
        clipboard = self.window.get_clipboard()
        clipboard.read_text_async(None, lambda clip, result: copied.append(clip.read_text_finish(result)))
        self.pump(lambda: bool(copied))
        self.assertEqual(copied[0], 'Copy this — exactly')

    def test_remote_command_line_forwards_import_and_toggle(self):
        audio = self.root / 'relative.wav'
        audio.write_bytes(b'test audio')
        self.engine.emit('ready')
        code = (
            f'import sys; sys.path.insert(0, {str(self.gui.ROOT)!r}); '
            'from dogspeak_app.gui import DogspeakApplication; '
            f'app=DogspeakApplication(); app.set_application_id({self.app.get_application_id()!r}); '
            'raise SystemExit(app.run(sys.argv))'
        )
        def remote(*args):
            process = subprocess.Popen([sys.executable, '-c', code, *args], cwd=self.root,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: process.kill() if process.poll() is None else None)
            self.pump(lambda: process.poll() is not None, timeout=5)
            out, err = process.communicate(timeout=1)
            self.assertEqual(process.returncode, 0, out + err)
        remote('--import-audio', 'relative.wav')
        self.assertEqual(len(self.engine.jobs), 1)
        copied, request_id = self.engine.jobs[-1]
        self.assertEqual(copied.read_bytes(), b'test audio')
        self.engine.emit('result', id=request_id, text='Remote import')
        remote('--toggle-recording')
        self.assertEqual(self.controller.state, 'recording')
        self.assertEqual(len(self.recorder.starts), 1)

    def test_new_draft_keeps_keyboard_focus_in_the_editor(self):
        self.window.title_entry.grab_focus()
        self.app.activate_action('new-draft', None)
        self.assertTrue(self.window.editor.has_focus())

    def test_actions_shortcuts_microphone_and_draft_dialog(self):
        self.assertIsNone(self.app.lookup_action('toggle-recording').get_parameter_type())
        self.assertEqual(self.app.lookup_action('import-audio').get_parameter_type().dup_string(), 's')
        self.assertEqual(
            self.gui.Gtk.accelerator_parse(self.app.get_accels_for_action('app.toggle-recording')[0]),
            self.gui.Gtk.accelerator_parse('<Control><Shift>r'))
        self.assertFalse(self.window.record_button.get_sensitive())
        self.engine.emit('ready')
        self.assertTrue(self.window.record_button.get_sensitive())
        self.window.microphone.set_selected(1)
        self.assertEqual(self.controller.source, 'mic.test')
        self.app.activate_action('toggle-recording', None)
        self.assertEqual(self.recorder.starts[-1][1], 'mic.test')
        self.assertEqual(self.controller.state, 'recording')
        self.app.activate_action('new-draft', None)
        self.window.show_drafts()
        self.assertIsInstance(self.window.drafts_dialog, self.gui.Adw.Dialog)
        self.window.drafts_dialog.close()


if __name__ == '__main__':
    unittest.main()
