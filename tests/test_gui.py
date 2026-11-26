"""Run with: xvfb-run -a /usr/bin/python3 -m unittest discover -s tests -p test_gui.py -v."""
import importlib
import itertools
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
from unittest import mock
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


class FakeShell:
    """Stands in for the dogspeak-overlay Shell extension."""

    def __init__(self):
        self.available = True
        self.shown = []
        self.keys = []
        self.clipboard = []

    def show(self, state, title, hint):
        self.shown.append((state, title, hint))
        return True

    def hide(self):
        self.shown.append(None)
        return True

    def set_keys(self, keys):
        self.keys.append(tuple(keys))
        return True

    def set_clipboard(self, text):
        self.clipboard.append(text)
        return True

    def close(self):
        self.available = False


class FakeKeys:
    def __init__(self, keys=None):
        self.keys = dict(keys or {})

    def get(self, address):
        return self.keys.get(address, '')

    def set(self, address, key):
        self.keys[address] = key

    def clear(self, address):
        self.keys.pop(address, None)


def cleanup_harness(test, controller):
    """A Cleanup whose provider is scripted by test.llm_reply and runs when test.run_llm()."""
    from dogspeak_app.cleanup import Cleanup
    test.llm_jobs, test.llm_seen = [], []
    test.llm_reply = ('Clean text.', ['Name the file.', 'Say what done means.'])

    class Client:
        def __init__(self, base_url, key, model):
            self.config = (base_url, key, model)

        def clean_up(self, text, purpose):
            test.llm_seen.append((self.config, text, purpose))
            if isinstance(test.llm_reply, Exception):
                raise test.llm_reply
            return test.llm_reply

        def test(self):
            return ['Key accepted', f'Model found: {self.config[2]}']

    test.keys = FakeKeys({'https://openrouter.ai/api/v1': 'sk-test'})
    cleanup = Cleanup(controller, test.keys, Client, spawn=test.llm_jobs.append)
    controller.store.set_setting('llm_model', 'small/model')

    def run_llm():
        while test.llm_jobs:
            test.llm_jobs.pop(0)()
    test.run_llm = run_llm
    return cleanup


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

    def test_successful_transcript_deletes_audio_and_keeps_text(self):
        c = self.controller
        self.engine.emit('ready')
        audio = self.root / 'input.wav'
        audio.write_bytes(b'audio')
        self.assertTrue(c.import_audio(audio))
        path, request_id = self.engine.jobs[-1]
        self.assertTrue(path.exists())
        self.engine.emit('result', id=request_id, text='Spoken paragraph')
        self.assertEqual(c.draft.content, 'Spoken paragraph')
        self.assertFalse(path.exists())
        self.assertEqual(self.store.list_segments(c.draft.id)[0]['text'], 'Spoken paragraph')
        self.assertEqual(self.store.list_segments(c.draft.id)[0]['audio_path'], '')

    def test_fatal_error_drops_audio_and_record_restarts_the_worker(self):
        c = self.controller
        self.engine.emit('ready')
        self.assertTrue(c.toggle_recording())
        c.stop_recording()
        path, _request_id = self.engine.jobs[-1]
        first = self.engine
        self.engine.emit('error', id=None, fatal=True, message='worker died')
        self.assertFalse(path.exists())
        self.assertEqual(c.pending, {})
        self.assertFalse(c.toggle_recording(), 'restart must not record')
        self.assertTrue(first.closed)
        self.assertIsNot(c.engine, first)
        self.assertEqual(c.state, 'loading')
        self.assertFalse(c.recording_job)
        c.engine.emit('ready')
        self.assertTrue(c.toggle_recording())

    def test_failed_transcription_deletes_audio_and_late_errors_are_ignored(self):
        c = self.controller
        self.engine.emit('ready')
        c.toggle_recording()
        c.stop_recording()
        path, request_id = self.engine.jobs[-1]
        self.engine.emit('error', id=request_id, message='bad audio')
        self.assertEqual(c.state, 'error')
        self.assertFalse(path.exists())
        c.toggle_recording()
        c.stop_recording()
        _path, request_id = self.engine.jobs[-1]
        c.cancel_recording()
        self.engine.emit('error', id=request_id, message='file vanished')
        self.assertEqual(c.state, 'ready')

    def test_close_deletes_audio_of_unfinished_jobs(self):
        c = self.controller
        self.engine.emit('ready')
        c.toggle_recording()
        recording = self.recorder.path
        c.close()
        self.assertFalse(recording.exists())

    def test_record_waits_for_a_cancelled_capture_to_finish(self):
        c = self.controller
        self.engine.emit('ready')
        with c._recorder_lock:
            self.assertFalse(c.toggle_recording())
        self.assertEqual(self.recorder.starts, [])
        self.assertTrue(c.toggle_recording())

    def test_delete_draft_switches_away_and_refuses_a_busy_draft(self):
        c = self.controller
        first = c.draft
        c.edit('First', 'unsaved words')
        second = c.store.create_draft('Second')
        c.delete_draft(second.id)
        self.assertEqual(c.draft.id, first.id)
        self.assertEqual(c.content, 'unsaved words')
        self.assertTrue(c.dirty, 'deleting another draft keeps this one\'s edits')
        c.delete_draft(first.id)
        self.assertNotEqual(c.draft.id, first.id)
        self.assertEqual(c.content, '')
        self.assertEqual(c.store.get_setting('last_draft'), c.draft.id)
        self.engine.emit('ready')
        busy = c.draft
        c.toggle_recording()
        with self.assertRaisesRegex(RuntimeError, 'waiting for a transcript'):
            c.delete_draft(busy.id)
        self.assertEqual(c.store.get_draft(busy.id).id, busy.id)

    def test_only_recordings_started_elsewhere_are_offered_to_the_clipboard(self):
        c = self.controller
        copied = []
        c.on_transcript = copied.append
        self.engine.emit('ready')
        for focused, text in ((True, 'Typed here'), (False, 'From another app')):
            c.focused = lambda focused=focused: focused
            self.assertTrue(c.toggle_recording())
            c.stop_recording()
            _path, request_id = self.engine.jobs[-1]
            self.engine.emit('result', id=request_id, text=text)
            self.assertEqual(c.outcome, 'saved')
        self.assertEqual(copied, ['From another app'])
        c.focused = lambda: False
        c.toggle_recording()
        c.stop_recording()
        self.engine.emit('result', id=self.engine.jobs[-1][1], text='  ')
        self.assertEqual(c.outcome, 'empty')
        self.assertEqual(copied, ['From another app'])
        c.toggle_recording()
        c.cancel_recording()
        self.assertEqual(c.outcome, 'cancelled')

    def test_cancel_recording_deletes_partial_audio_without_transcribing(self):
        c = self.controller
        self.engine.emit('ready')
        original = c.draft.content
        self.assertTrue(c.toggle_recording())
        path = self.recorder.path
        self.assertTrue(path.exists())
        self.assertTrue(c.cancel_recording())
        self.assertFalse(c.recording)
        self.assertFalse(self.recorder.running)
        self.assertFalse(path.exists())
        self.assertEqual(self.engine.jobs, [])
        self.assertEqual(c.draft.content, original)
        self.assertEqual(c.state, 'ready')

    def test_cancel_transcription_drops_late_result_and_deletes_audio(self):
        c = self.controller
        self.engine.emit('ready')
        self.assertTrue(c.toggle_recording())
        c.stop_recording()
        path, request_id = self.engine.jobs[-1]
        self.assertTrue(path.exists())
        self.assertEqual(c.state, 'transcribing')
        self.assertTrue(c.cancel_recording())
        self.assertEqual(c.pending, {})
        self.assertFalse(path.exists())
        self.assertEqual(c.draft.content, '')
        self.engine.emit('result', id=request_id, text='Should be ignored')
        self.assertEqual(c.draft.content, '')
        self.assertEqual(self.store.list_segments(c.draft.id), [])
        self.assertFalse(self.engine.closed)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        ControllerTests.setUp(self)
        self.cleanup = cleanup_harness(self, self.controller)
        self.replaced = []
        self.cleanup.on_replace = lambda draft_id, text: self.replaced.append((draft_id, text))

    def write(self, text):
        self.controller.edit(self.controller.title, text)

    def test_clean_up_replaces_text_keeps_versions_and_remembers_purpose(self):
        c, cleanup = self.controller, self.cleanup
        self.write('um so tidy the the parser')
        cleanup.set_purpose('Bug report for Codex')
        self.assertTrue(cleanup.start())
        self.assertEqual(cleanup.locked_drafts(), {c.draft.id})
        self.assertFalse(cleanup.start(), 'one request at a time')
        self.run_llm()
        config, sent, purpose = self.llm_seen[-1]
        self.assertEqual(config, ('https://openrouter.ai/api/v1', 'sk-test', 'small/model'))
        self.assertEqual((sent, purpose), ('um so tidy the the parser', 'Bug report for Codex'))
        self.assertIsNone(cleanup.busy)
        self.assertEqual(c.content, 'Clean text.')
        self.assertEqual(self.store.get_draft(c.draft.id).content, 'Clean text.')
        self.assertEqual(self.replaced, [(c.draft.id, 'Clean text.')])
        versions = cleanup.versions()
        self.assertEqual([v['label'] for v in versions], ['Original', 'Clean up · Bug report for Codex'])
        self.assertEqual(cleanup.current_index(), 1)
        version_id, suggestions = cleanup.suggestions()
        self.assertEqual([t for _i, t in suggestions], ['Name the file.', 'Say what done means.'])
        cleanup.dismiss(version_id, 0)
        self.assertEqual([t for _i, t in cleanup.suggestions()[1]], ['Say what done means.'])
        self.assertEqual(self.store.get_setting('purpose'), 'Bug report for Codex')
        self.assertEqual(cleanup.recent_purposes(), ['Bug report for Codex'])

    def test_recent_purposes_skip_presets_dedupe_and_cap_at_five(self):
        cleanup = self.cleanup
        for purpose in ['Essay', 'a', 'b', 'a', 'c', 'd', 'e', 'f', '  ']:
            cleanup.set_purpose(purpose, remember=True)
        self.assertEqual(cleanup.recent_purposes(), ['f', 'e', 'd', 'c', 'a'])
        self.assertEqual(self.store.get_setting('purpose'), '')
        cleanup.clear_recent()
        self.assertEqual(cleanup.recent_purposes(), [])

    def test_missing_model_or_key_asks_for_setup_and_sends_nothing(self):
        self.write('text')
        self.store.set_setting('llm_model', '')
        self.assertFalse(self.cleanup.start())
        self.assertEqual(self.cleanup.problem[0], 'setup')
        self.store.set_setting('llm_model', 'small/model')
        self.keys.keys.clear()
        self.assertTrue(self.cleanup.start())
        self.run_llm()
        self.assertEqual(self.cleanup.problem, ('setup', 'Clean up needs an API key'))
        self.assertEqual(self.llm_seen, [])
        self.assertEqual(self.controller.content, 'text')

    def test_failure_and_cancel_leave_the_draft_alone(self):
        from dogspeak_app.llm import LLMError
        c, cleanup = self.controller, self.cleanup
        self.write('keep me')
        self.llm_reply = LLMError('OpenRouter: the account is out of credit')
        cleanup.start()
        self.run_llm()
        self.assertEqual(cleanup.problem[0], 'failed')
        self.assertIn('out of credit', cleanup.problem[1])
        self.assertIn('wasn\u2019t changed', cleanup.problem[1])
        self.llm_reply = ('Should not land.', [])
        cleanup.start()
        self.assertIsNone(cleanup.problem)
        self.assertTrue(cleanup.cancel())
        self.run_llm()
        self.assertEqual(c.content, 'keep me')
        self.assertEqual(cleanup.versions(), [])
        self.assertEqual(self.replaced, [])
        self.assertFalse(self.cleanup.start() and False)

    def test_empty_draft_is_not_sent(self):
        self.write('   ')
        self.assertFalse(self.cleanup.start())
        self.assertEqual(self.llm_jobs, [])

    def test_transcript_that_lands_during_clean_up_is_kept_after_it(self):
        c, cleanup = self.controller, self.cleanup
        self.write('first ramble')
        cleanup.start()
        self.engine.emit('ready')
        self.assertTrue(c.toggle_recording())
        c.stop_recording()
        _path, request_id = self.engine.jobs[-1]
        self.engine.emit('result', id=request_id, text='Late dictation.')
        self.assertEqual(c.content, 'first ramble\n\nLate dictation.')
        self.run_llm()
        self.assertEqual(c.content, 'Clean text.\n\nLate dictation.')

    def test_result_goes_to_its_own_draft_after_switching(self):
        c, cleanup = self.controller, self.cleanup
        self.write('origin text')
        origin = c.draft.id
        cleanup.start()
        with self.assertRaisesRegex(RuntimeError, 'being cleaned up'):
            c.delete_draft(origin)
        c.new_draft()
        self.write('other draft')
        self.run_llm()
        self.assertEqual(c.content, 'other draft')
        self.assertEqual(self.store.get_draft(origin).content, 'Clean text.')
        self.assertEqual(self.replaced, [(origin, 'Clean text.')])

    def test_revert_keeps_unsaved_edits_as_a_version(self):
        c, cleanup = self.controller, self.cleanup
        self.write('original words')
        cleanup.start()
        self.run_llm()
        self.write('Clean text. Plus my edit.')
        self.assertTrue(cleanup.revert())
        self.assertEqual(c.content, 'original words')
        self.assertEqual([v['label'] for v in cleanup.versions()],
                         ['Original', 'Clean up · General purpose', 'Your edits'])
        self.assertEqual(cleanup.current_index(), 0)
        self.assertFalse(cleanup.revert(), 'nothing before the original')
        self.assertTrue(cleanup.show_version(2))
        self.assertEqual(c.content, 'Clean text. Plus my edit.')
        self.assertEqual(len(cleanup.versions()), 3, 'showing a stored version adds nothing')

    def test_second_clean_up_does_not_duplicate_an_unchanged_version(self):
        cleanup = self.cleanup
        self.write('ramble')
        cleanup.start()
        self.run_llm()
        self.llm_reply = ('Cleaner.', [])
        cleanup.start()
        self.run_llm()
        self.assertEqual([v['content'] for v in cleanup.versions()], ['ramble', 'Clean text.', 'Cleaner.'])
        self.assertEqual(cleanup.suggestions()[1], [])


class GtkTests(unittest.TestCase):
    def setUp(self):
        ControllerTests.setUp(self)
        self.app = self.gui.DogspeakApplication(
            controller_factory=lambda: self.controller,
            cleanup_factory=lambda controller: cleanup_harness(self, controller),
            sources_provider=lambda: [{'name': 'mic.test', 'description': 'Test microphone'}],
            flags=self.gui.Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.assertEqual(self.app.get_application_id(), 'io.github.Remco28.Dogspeak')
        self.app.set_application_id('io.github.Remco28.Dogspeak.Test' + uuid.uuid4().hex)
        self.assertTrue(self.app.register(None))
        self.app.activate()
        self.window = self.app.window
        self.window.typing_pause = 0
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

    def start_dictation(self, focused=True):
        c = self.controller
        self.engine.emit('ready')
        c.focused = lambda: focused
        self.assertTrue(c.toggle_recording())
        c.stop_recording()
        return self.engine.jobs[-1][1]

    def dictate(self, text, focused=True):
        self.engine.emit('result', id=self.start_dictation(focused), text=text)

    def set_text(self, text):
        buf = self.window.buffer
        buf.begin_user_action()
        buf.insert_at_cursor(text)
        buf.end_user_action()

    def caret(self):
        buf = self.window.buffer
        return buf.get_iter_at_mark(buf.get_insert()).get_offset()

    def test_dictation_lands_at_the_cursor_which_then_follows_it(self):
        buf = self.window.buffer
        self.set_text('First\n\nSecond')
        self.dictate('End')
        self.assertEqual(self.text(), 'First\n\nSecond\n\nEnd')
        self.assertEqual(self.caret(), len(self.text()))
        buf.place_cursor(buf.get_iter_at_offset(5))
        self.dictate('Middle')
        self.assertEqual(self.text(), 'First\n\nMiddle\n\nSecond\n\nEnd')
        self.assertEqual(self.caret(), len('First\n\nMiddle'))
        self.assertEqual(self.store.get_draft(self.controller.draft.id).content, self.text())
        buf.undo()
        self.assertEqual(self.text(), 'First\n\nSecond\n\nEnd')

    def test_cursor_stays_on_the_spoken_words_not_after_their_separator(self):
        buf = self.window.buffer
        self.set_text('First\n\nSecond')
        buf.place_cursor(buf.get_iter_at_offset(7))
        self.dictate('New')
        self.assertEqual(self.text(), 'First\n\nNew\n\nSecond')
        self.assertEqual(self.caret(), len('First\n\nNew'))
        self.dictate('Next')
        self.assertEqual(self.text(), 'First\n\nNew\n\nNext\n\nSecond')
        self.assertEqual(self.caret(), len('First\n\nNew\n\nNext'))

    def test_inline_dictation_in_a_row_keeps_single_spaces(self):
        buf = self.window.buffer
        self.set_text('one two')
        buf.place_cursor(buf.get_iter_at_offset(3))
        self.dictate('new')
        self.dictate('more')
        self.assertEqual(self.text(), 'one new more two')

    def test_view_follows_dictation_down_a_long_draft(self):
        self.set_text('\n'.join(f'Line {i}' for i in range(200)))
        scroller = self.window.editor.get_parent().get_vadjustment()
        self.window.buffer.place_cursor(self.window.buffer.get_start_iter())
        self.pump(lambda: scroller.get_upper() > scroller.get_page_size() > 0)
        scroller.set_value(0)
        self.window.buffer.place_cursor(self.window.buffer.get_end_iter())
        self.dictate('Bottom')
        self.pump(lambda: scroller.get_value() > 0)

    def test_dictation_after_a_selection_keeps_the_selected_text(self):
        buf = self.window.buffer
        self.set_text('Keep this words')
        buf.select_range(buf.get_iter_at_offset(0), buf.get_iter_at_offset(9))
        self.dictate('spoken')
        self.assertEqual(self.text(), 'Keep this spoken words')
        self.assertFalse(buf.get_has_selection())

    def test_dictation_goes_to_the_end_while_the_window_is_closed(self):
        buf = self.window.buffer
        self.set_text('First\n\nSecond')
        buf.place_cursor(buf.get_iter_at_offset(5))
        self.window.set_visible(False)
        self.dictate('Away')
        self.assertEqual(self.text(), 'First\n\nSecond\n\nAway')
        self.assertEqual(self.caret(), 5)

    def test_dictation_during_clean_up_stays_behind_what_was_sent(self):
        buf = self.window.buffer
        self.set_text('First\n\nSecond')
        buf.place_cursor(buf.get_iter_at_offset(5))
        self.pump()
        self.assertTrue(self.window.cleanup.start())
        self.dictate('Late')
        self.assertEqual(self.text(), 'First\n\nSecond\n\nLate')
        self.run_llm()
        self.assertEqual(self.text(), 'Clean text.\n\nLate')

    def test_dictation_started_from_another_app_goes_to_the_end(self):
        buf = self.window.buffer
        self.set_text('First\n\nSecond')
        buf.place_cursor(buf.get_iter_at_offset(5))
        self.dictate('Elsewhere', focused=False)
        self.assertEqual(self.text(), 'First\n\nSecond\n\nElsewhere')
        self.assertEqual(self.caret(), 5)

    def test_imported_audio_goes_to_the_end(self):
        buf = self.window.buffer
        self.set_text('First\n\nSecond')
        buf.place_cursor(buf.get_iter_at_offset(5))
        self.engine.emit('ready')
        audio = self.root / 'input.wav'
        audio.write_bytes(b'audio')
        self.assertTrue(self.controller.import_audio(audio))
        self.engine.emit('result', id=self.engine.jobs[-1][1], text='Imported')
        self.assertEqual(self.text(), 'First\n\nSecond\n\nImported')

    def test_dictation_inside_a_word_goes_after_the_word(self):
        buf = self.window.buffer
        self.set_text('hello world')
        buf.place_cursor(buf.get_iter_at_offset(2))
        self.dictate('there')
        self.assertEqual(self.text(), 'hello there world')
        buf.place_cursor(buf.get_iter_at_offset(len('hello there ')))
        self.dictate('big')
        self.assertEqual(self.text(), 'hello there big world')

    def test_transcript_waits_for_a_pause_in_typing(self):
        self.window.typing_pause = 0.2
        self.set_text('First line\n\nI am typ')
        request_id = self.start_dictation()
        self.engine.emit('result', id=request_id, text='Spoken words.')
        self.assertEqual(self.text(), 'First line\n\nI am typ')
        self.assertIn('pause', self.window.status_label.get_text())
        self.assertFalse(self.window.record_button.get_sensitive())
        self.set_text('ing a word')
        self.pump(lambda: 'Spoken' in self.text())
        self.assertEqual(self.text(), 'First line\n\nI am typing a word\n\nSpoken words.')
        self.assertEqual(self.controller.pending, {})
        self.assertEqual(self.controller.held, {})

    def test_waiting_transcript_can_be_dropped_with_esc(self):
        self.window.typing_pause = 0.05
        self.set_text('words')
        self.engine.emit('result', id=self.start_dictation(), text='Dropped')
        self.assertTrue(self.controller.cancel_recording())
        time.sleep(0.06)
        self.pump()
        self.assertEqual(self.text(), 'words')

    def test_waiting_transcript_follows_its_draft_when_another_opens(self):
        self.window.typing_pause = 0.05
        origin = self.controller.draft.id
        self.set_text('words')
        self.engine.emit('result', id=self.start_dictation(), text='Spoken')
        self.controller.new_draft()
        self.pump(lambda: not self.controller.pending)
        self.assertEqual(self.text(), '')
        self.assertEqual(self.store.get_draft(origin).content, 'words\n\nSpoken')

    def test_quitting_keeps_a_waiting_transcript(self):
        self.window.typing_pause = 60
        origin = self.controller.draft.id
        database = self.root / 'kept.txt'
        self.set_text('words')
        self.engine.emit('result', id=self.start_dictation(), text='Spoken')
        save = self.store.close
        self.store.close = lambda: (database.write_text(self.store.get_draft(origin).content), save())
        self.controller.close()
        self.assertEqual(database.read_text(), 'words\n\nSpoken')

    def test_waiting_transcript_that_will_not_save_becomes_an_edit_on_quit(self):
        self.window.typing_pause = 60
        origin = self.controller.draft.id
        kept = self.root / 'kept.txt'
        self.set_text('words')
        self.engine.emit('result', id=self.start_dictation(), text='Spoken')
        def fail(*args):
            raise OSError('disk full')
        self.store.append_transcript = fail
        save = self.store.close
        self.store.close = lambda: (kept.write_text(self.store.get_draft(origin).content), save())
        self.assertTrue(self.controller.close())
        self.assertEqual(kept.read_text(), 'words\n\nSpoken')

    def test_unsavable_waiting_transcript_stays_with_its_own_draft(self):
        c = self.controller
        self.window.typing_pause = 60
        origin = c.draft.id
        kept = self.root / 'kept.txt'
        self.set_text('words')
        self.engine.emit('result', id=self.start_dictation(), text='Spoken')
        c.dispatch = lambda fn, *args: None  # the timer has not fired
        c.new_draft()
        other = c.draft.id
        def fail(*args):
            raise OSError('disk full')
        c.draft = self.store.get_draft(other)
        self.store.append_transcript = fail
        save = self.store.close
        self.store.close = lambda: (kept.write_text(repr((
            self.store.get_draft(origin).content, self.store.get_draft(other).content))), save())
        self.assertTrue(c.close())
        self.assertEqual(kept.read_text(), repr(('words\n\nSpoken', '')))

    def test_waiting_transcript_survives_refused_quit_and_retry(self):
        self.window.typing_pause = 60
        origin = self.controller.draft.id
        kept = self.root / 'kept.txt'
        self.set_text('words')
        self.engine.emit('result', id=self.start_dictation(), text='Spoken')
        def fail(*args):
            raise OSError('disk full')
        append, save_draft = self.store.append_transcript, self.store.save_draft
        self.store.append_transcript = self.store.save_draft = fail
        self.assertFalse(self.controller.close())
        self.assertEqual(self.text(), 'words\n\nSpoken')  # Copy text includes it
        self.store.append_transcript, self.store.save_draft = append, save_draft
        save = self.store.close
        self.store.close = lambda: (kept.write_text(self.store.get_draft(origin).content), save())
        self.assertTrue(self.controller.close())
        self.assertEqual(kept.read_text(), 'words\n\nSpoken')

    def test_hidden_append_leaves_a_selection_alone(self):
        buf = self.window.buffer
        self.set_text('Whole draft')
        buf.select_range(buf.get_start_iter(), buf.get_end_iter())
        self.window.set_visible(False)
        self.dictate('Away')
        start, end = buf.get_selection_bounds()
        self.assertEqual((start.get_offset(), end.get_offset()), (0, len('Whole draft')))

    def test_returning_to_a_draft_puts_the_cursor_at_its_end(self):
        buf = self.window.buffer
        self.set_text('First\n\nSecond')
        buf.place_cursor(buf.get_iter_at_offset(5))
        origin = self.controller.draft.id
        self.controller.new_draft()
        self.controller.open_draft(origin)
        self.assertEqual(self.caret(), len('First\n\nSecond'))
        self.dictate('More')
        self.assertEqual(self.text(), 'First\n\nSecond\n\nMore')

    def test_opened_draft_starts_with_the_cursor_at_its_end(self):
        c = self.controller
        c.edit('Old', 'Some earlier text')
        c.new_draft()
        old = next(d.id for d in self.store.list_drafts() if d.title == 'Old')
        c.open_draft(old)
        self.assertEqual(self.text(), 'Some earlier text')
        self.assertEqual(self.caret(), len('Some earlier text'))
        self.dictate('More')
        self.assertEqual(self.text(), 'Some earlier text\n\nMore')

    def place(self, line, column=0):
        buf = self.window.buffer
        buf.place_cursor(buf.get_iter_at_line_offset(line, column)[1])

    def test_lines_are_numbered_and_move_as_one_undoable_autosaved_step(self):
        self.assertTrue(self.window.editor.get_show_line_numbers())
        buf = self.window.buffer
        self.set_text('one\ntwo\nthree')
        self.place(1, 2)
        self.app.activate_action('move-line-up', None)
        self.assertEqual(self.text(), 'two\none\nthree')
        self.assertEqual(self.caret(), 2)
        self.pump(lambda: not self.controller.dirty)
        self.assertEqual(self.store.get_draft(self.controller.draft.id).content, self.text())
        buf.undo()
        self.assertEqual(self.text(), 'one\ntwo\nthree')
        self.place(0)
        self.app.activate_action('move-line-down', None)
        self.assertEqual(self.text(), 'two\none\nthree')
        self.window.editor.set_editable(False)  # as during Clean up
        self.app.activate_action('move-line-up', None)
        self.assertEqual(self.text(), 'two\none\nthree')

    def test_gtksource_key_binding_gets_our_move_too(self):
        self.set_text('aaa\n\nbbb')
        self.place(0)
        self.window.editor.emit('move-lines', True)
        self.assertEqual(self.text(), 'bbb\n\naaa')

    def test_paragraphs_swap_and_blank_lines_stay_where_they_were(self):
        self.set_text('aaa\n\nbbb\n\nccc')
        self.place(0, 1)
        self.window.move_line(True)
        self.assertEqual(self.text(), 'bbb\n\naaa\n\nccc')
        self.window.move_line(True)
        self.assertEqual(self.text(), 'bbb\n\nccc\n\naaa')
        self.assertEqual(self.window.buffer.get_iter_at_mark(
            self.window.buffer.get_insert()).get_line(), 4)
        self.window.move_line(True)  # already last
        self.assertEqual(self.text(), 'bbb\n\nccc\n\naaa')
        self.window.move_line(False)
        self.window.move_line(False)
        self.assertEqual(self.text(), 'aaa\n\nbbb\n\nccc')
        self.window.buffer.undo()
        self.assertEqual(self.text(), 'bbb\n\naaa\n\nccc')

    def test_selected_lines_move_and_stay_selected(self):
        buf = self.window.buffer
        self.set_text('a\nb\nc\nd')
        buf.select_range(buf.get_iter_at_line(0)[1], buf.get_iter_at_line(2)[1])
        self.window.move_line(True)
        self.assertEqual(self.text(), 'c\na\nb\nd')
        start, end = buf.get_selection_bounds()
        self.assertEqual(buf.get_text(start, end, True), 'a\nb\n')

    def test_selection_moved_to_the_end_of_the_draft_moves_back(self):
        buf = self.window.buffer
        self.set_text('a\nb\n\nc')
        buf.select_range(buf.get_iter_at_line(0)[1], buf.get_iter_at_line(2)[1])
        self.window.move_line(True)
        self.assertEqual(self.text(), 'c\n\na\nb')
        start, end = buf.get_selection_bounds()
        self.assertEqual(buf.get_text(start, end, True), 'a\nb')
        self.window.move_line(False)
        self.assertEqual(self.text(), 'a\nb\n\nc')

    def test_moving_past_identical_lines_still_moves_the_cursor(self):
        self.set_text('a\na\nb')
        self.place(0)
        self.window.move_line(True)
        self.window.move_line(True)
        self.assertEqual(self.text(), 'a\nb\na')

    def test_moving_any_line_back_restores_the_text(self):
        move = self.gui.move_lines
        for size in range(1, 7):
            for shape in itertools.product(('', 'x'), repeat=size):
                lines = [f'{word}{i}' if word else '' for i, word in enumerate(shape)]
                for line, word in enumerate(shape):
                    moved = word and move(lines, line, line, True)
                    if moved:
                        new, _first, new_first = moved
                        back = move(new, new_first, new_first, False)
                        self.assertEqual(back and back[0], lines, (lines, line))

    def test_move_lines_rules(self):
        move = self.gui.move_lines
        for lines, first, last, down, expected in (
                (['a', 'b', '', 'c'], 1, 1, True, ['a', '', 'b', 'c']),
                (['a', '', 'b', 'c'], 2, 2, False, ['a', 'b', '', 'c']),
                (['a', '', 'b1', 'b2', '', 'c'], 0, 0, True, ['b1', 'b2', '', 'a', '', 'c']),
                (['a', '', '', 'b'], 0, 0, True, ['b', '', '', 'a']),
                (['a', '', 'b'], 0, 2, True, None),
                (['', 'a', '', 'b'], 1, 1, False, None),
                (['a', '', ''], 0, 0, True, None),
                (['a', '', 'b'], 1, 1, True, None),
                (['a1', 'a2', '', 'b', '', 'c'], 1, 3, True, ['c', '', 'a1', 'a2', '', 'b'])):
            with self.subTest(lines=lines, first=first):
                result = move(lines, first, last, down)
                self.assertEqual(result and result[0], expected)

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

    def test_window_close_hides_without_unloading_the_model(self):
        self.window.buffer.insert_at_cursor('Last unsaved keystrokes')
        self.window.close()
        self.assertFalse(self.window.get_visible())
        self.assertFalse(self.controller.closed)
        self.assertFalse(self.engine.closed)
        from dogspeak_app.storage import Store
        reopened = Store(self.root / 'test.sqlite3')
        try:
            self.assertEqual(reopened.get_draft(self.controller.draft.id).content,
                             'Last unsaved keystrokes')
        finally:
            reopened.close()

    def test_quit_flushes_before_async_owned_process_cleanup(self):
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
        self.window.quit_session()
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
        self.window.quit_session()
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

    def test_copy_clear_verifies_native_clipboard_and_is_one_saved_undo_step(self):
        win, buf = self.window, self.window.buffer
        text = '  Copy this — exactly\n\nSecond paragraph.\n'
        win.title_entry.set_text('Keep this title')
        buf.begin_user_action()
        buf.insert_at_cursor(text)
        buf.end_user_action()
        buf.select_range(buf.get_start_iter(), buf.get_iter_at_offset(4))
        self.assertTrue(self.app.lookup_action('copy-clear').get_enabled())
        self.assertEqual(self.app.get_accels_for_action('app.copy-clear'), ['<Shift><Control>x'])
        self.app.activate_action('copy-clear', None)
        self.pump(lambda: win.copy_clear_request is None)
        self.assertEqual(self.text(), '')
        self.assertEqual(win.clear_toast.get_title(), 'Copied and cleared')
        copied = []
        win.get_clipboard().read_text_async(None, lambda clip, result: copied.append(clip.read_text_finish(result)))
        self.pump(lambda: bool(copied))
        self.assertEqual(copied, [text])
        self.pump(lambda: not self.controller.dirty)
        draft = self.store.get_draft(self.controller.draft.id)
        self.assertEqual((draft.title, draft.content), ('Keep this title', ''))
        self.assertFalse(self.app.lookup_action('copy-clear').get_enabled())
        win.clear_toast.emit('button-clicked')
        self.assertEqual(self.text(), text)
        self.pump(lambda: not self.controller.dirty)
        self.assertEqual(self.store.get_draft(draft.id).content, text)
        buf.redo()
        self.assertEqual(self.text(), '')
        buf.undo()
        self.assertEqual(self.text(), text)

    def deferred_clipboard(self):
        clipboard = mock.Mock()
        pending = []
        clipboard.read_text_async.side_effect = lambda cancel, callback: pending.append(callback)
        patch = mock.patch.object(self.window, 'get_clipboard', return_value=clipboard)
        patch.start()
        self.addCleanup(patch.stop)
        return clipboard, pending

    def test_copy_clear_keeps_text_on_clipboard_mismatch_or_error(self):
        clipboard, pending = self.deferred_clipboard()
        self.window.buffer.insert_at_cursor('Keep me')
        for result in ('previous clipboard', RuntimeError('read failed')):
            self.window.copy_and_clear()
            if isinstance(result, Exception):
                clipboard.read_text_finish.side_effect = result
            else:
                clipboard.read_text_finish.return_value = result
            pending.pop()(clipboard, object())
            self.assertEqual(self.text(), 'Keep me')
            self.assertIsNone(self.window.copy_clear_request)
            self.assertTrue(self.app.lookup_action('copy-clear').get_enabled())

    def test_copy_clear_timeout_ignores_a_late_success(self):
        clipboard, pending = self.deferred_clipboard()
        self.window.buffer.insert_at_cursor('Keep me')
        clipboard.read_text_finish.return_value = 'Keep me'
        with mock.patch.object(self.gui, 'CLIPBOARD_TIMEOUT_MS', 1):
            self.window.copy_and_clear()
            self.pump(lambda: self.window.copy_clear_request is None)
        pending.pop()(clipboard, object())
        self.assertEqual(self.text(), 'Keep me')

    def test_copy_clear_does_not_clear_after_edit_or_switch_back(self):
        clipboard, pending = self.deferred_clipboard()
        win, c = self.window, self.controller
        win.buffer.insert_at_cursor('Original')
        clipboard.read_text_finish.return_value = 'Original'
        win.copy_and_clear()
        self.assertFalse(self.app.lookup_action('clear').get_enabled())
        win.buffer.insert_at_cursor(' edited')
        pending.pop()(clipboard, object())
        self.assertEqual(self.text(), 'Original edited')
        win.copy_and_clear()
        clipboard.read_text_finish.return_value = 'Original edited'
        original = c.draft.id
        c.new_draft()
        c.open_draft(original)
        pending.pop()(clipboard, object())
        self.assertEqual(self.text(), 'Original edited')

    def test_clear_actions_are_disabled_during_recording_transcription_and_cleanup(self):
        win, c = self.window, self.controller
        win.buffer.insert_at_cursor('Keep me')
        self.engine.emit('ready')
        c.toggle_recording()
        self.assertFalse(self.app.lookup_action('copy-clear').get_enabled())
        self.assertFalse(self.app.lookup_action('clear').get_enabled())
        win.clear_text()
        self.assertEqual(self.text(), 'Keep me')
        c.stop_recording()
        self.assertFalse(self.app.lookup_action('copy-clear').get_enabled())
        c.cancel_recording()
        self.assertTrue(self.app.lookup_action('copy-clear').get_enabled())
        self.assertTrue(win.cleanup.start())
        self.assertFalse(self.app.lookup_action('copy-clear').get_enabled())
        win.copy_and_clear()
        self.assertIsNone(win.copy_clear_request)
        win.cleanup.cancel()
        self.assertTrue(self.app.lookup_action('copy-clear').get_enabled())

    def test_copy_clear_aborts_if_recording_starts_during_verification(self):
        clipboard, pending = self.deferred_clipboard()
        win = self.window
        win.buffer.insert_at_cursor('Keep me')
        clipboard.read_text_finish.return_value = 'Keep me'
        win.copy_and_clear()
        self.engine.emit('ready')
        self.controller.toggle_recording()
        pending.pop()(clipboard, object())
        self.assertEqual(self.text(), 'Keep me')

    def test_clear_leaves_clipboard_alone_and_dismisses_stale_undo(self):
        win = self.window
        win.buffer.insert_at_cursor('Old text')
        clipboard, _pending = self.deferred_clipboard()
        self.app.activate_action('clear', None)
        self.assertEqual(self.text(), '')
        self.assertEqual(win.clear_toast.get_title(), 'Cleared')
        toast = win.clear_toast
        win.buffer.insert_at_cursor('New text')
        toast.emit('button-clicked')
        self.assertEqual(self.text(), 'New text')
        clipboard.set.assert_not_called()

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
            [self.gui.Gtk.accelerator_parse(accel)
             for accel in self.app.get_accels_for_action('app.toggle-recording')],
            [self.gui.Gtk.accelerator_parse('<Super>r'), self.gui.Gtk.accelerator_parse('<Control><Shift>r')])
        self.assertFalse(self.window.record_button.get_sensitive())
        self.engine.emit('ready')
        self.assertTrue(self.window.record_button.get_sensitive())
        self.assertIn('Super+R', self.window.status_label.get_text())
        self.window.microphone.set_selected(1)
        self.assertEqual(self.controller.source, 'mic.test')
        self.app.activate_action('toggle-recording', None)
        self.assertEqual(self.recorder.starts[-1][1], 'mic.test')
        self.assertEqual(self.controller.state, 'recording')
        self.app.activate_action('new-draft', None)
        self.window.show_drafts()
        self.assertIsInstance(self.window.drafts_dialog, self.gui.Adw.Dialog)
        self.window.drafts_dialog.close()

    def test_enter_stops_and_escape_cancels_without_changing_the_draft(self):
        self.engine.emit('ready')
        self.window.buffer.insert_at_cursor('Keep this')
        self.app.activate_action('toggle-recording', None)
        self.assertTrue(self.window.handle_session_key(self.gui.Gdk.KEY_Return))
        self.assertEqual(self.controller.state, 'transcribing')
        self.assertEqual(self.text(), 'Keep this')
        self.assertTrue(self.window.handle_session_key(self.gui.Gdk.KEY_Escape))
        self.assertEqual(self.controller.state, 'ready')
        self.assertEqual(self.text(), 'Keep this')
        self.assertEqual(self.controller.pending, {})
        self.assertFalse(self.window.handle_session_key(self.gui.Gdk.KEY_Return))
        self.assertEqual(self.text(), 'Keep this')

    def test_recording_overlay_and_hide_keep_the_model_until_quit(self):
        self.engine.emit('ready')
        self.app.activate_action('toggle-recording', None)
        self.assertNotIsInstance(self.window.overlay, self.gui.Gtk.Window)
        self.assertTrue(self.window.overlay.get_visible())
        self.assertIn('Recording', self.window.overlay_label.get_text())
        self.window.close()
        self.assertFalse(self.window.get_visible())
        self.assertTrue(self.window.overlay.get_visible())
        self.assertFalse(self.controller.closed)
        self.assertFalse(self.engine.closed)
        self.assertEqual(self.controller.state, 'recording')
        self.app.activate_action('quit', None)
        self.pump(lambda: self.controller.closed and self.engine.closed)
        self.assertTrue(self.engine.closed)

    def test_unfocused_toggle_records_without_presenting_the_editor(self):
        notes = []
        self.window.overlay_notify = lambda title, body, resident: notes.append((title, body, resident))
        self.engine.emit('ready')
        self.window.set_visible(False)
        self.app.activate_action('toggle-recording', None)
        self.assertEqual(self.controller.state, 'recording')
        self.assertFalse(self.window.get_visible())
        self.assertTrue(self.window.overlay.get_visible())
        self.assertNotIsInstance(self.window.overlay, self.gui.Gtk.Window)
        self.assertTrue(notes)
        self.assertIn('Recording', notes[-1][0])
        self.assertTrue(notes[-1][2])

    def test_escape_in_a_dialog_does_not_drop_the_transcript(self):
        self.engine.emit('ready')
        self.app.activate_action('toggle-recording', None)
        self.controller.stop_recording()
        self.window.show_drafts()
        self.pump(lambda: self.window.get_visible_dialog() is not None)
        self.assertFalse(self.window.handle_session_key(self.gui.Gdk.KEY_Escape))
        self.assertTrue(self.controller.pending)

    def test_delete_from_dialog_confirms_and_shows_the_next_draft(self):
        other = self.controller.store.create_draft('Other')
        self.window.buffer.insert_at_cursor('doomed text')
        self.pump()
        doomed = self.controller.draft
        alert = self.window.confirm_delete(doomed)
        alert.emit('response', 'cancel')
        self.assertEqual(self.controller.store.get_draft(doomed.id).id, doomed.id)
        alert = self.window.confirm_delete(doomed)
        alert.emit('response', 'delete')
        with self.assertRaises(KeyError):
            self.controller.store.get_draft(doomed.id)
        self.assertEqual(self.controller.draft.id, other.id)
        self.assertEqual(self.text(), '')
        self.assertNotIn(doomed.id, self.window.buffers)
        self.window.buffer.insert_at_cursor('still editable')
        self.pump()
        self.assertEqual(self.controller.content, 'still editable')

    def test_tray_copy_all_uses_the_shell_clipboard_when_unfocused(self):
        shell = self.use_shell()
        self.window.buffer.insert_at_cursor('whole draft')
        self.window.set_visible(False)
        self.app.activate_action('copy-all', None)
        self.assertEqual(shell.clipboard, ['whole draft'])

    def test_clean_up_in_the_window_is_one_undo_step_and_locks_while_busy(self):
        win = self.window
        win.buffer.insert_at_cursor('um so the ramble')
        self.pump()
        win.cleanup_view.purpose.set_text('Essay')
        self.app.activate_action('clean-up', None)
        self.assertFalse(win.editor.get_editable())
        self.assertEqual(win.cleanup_view.clean_button.get_label(), 'Cleaning up\u2026')
        self.assertIn('Sending this draft to OpenRouter', win.status_label.get_text())
        self.run_llm()
        self.assertTrue(win.editor.get_editable())
        self.assertEqual(self.text(), 'Clean text.')
        self.assertEqual(win.cleanup_view.version_menu.get_label(), 'Version 2 of 2')
        self.assertEqual(win.cleanup_view.panel_count.get_text(), '2')
        self.assertEqual(self.llm_seen[-1][2], 'Essay')
        win.buffer.undo()
        self.pump(lambda: self.controller.content == 'um so the ramble')
        self.assertEqual(self.text(), 'um so the ramble')
        self.pump(lambda: win.cleanup_view.version_menu.get_label() == 'Version 1 of 2')
        self.assertFalse(win.cleanup_view.revert_button.get_sensitive())
        win.cleanup_view._show_version(1)
        self.assertEqual(self.text(), 'Clean text.')
        self.assertEqual(len(win.cleanup.versions()), 2)

    def test_escape_cancels_a_clean_up_in_flight(self):
        win = self.window
        win.buffer.insert_at_cursor('words')
        self.pump()
        self.app.activate_action('clean-up', None)
        self.assertTrue(win.handle_session_key(self.gui.Gdk.KEY_Escape))
        self.run_llm()
        self.assertEqual(self.text(), 'words')
        self.assertTrue(win.editor.get_editable())

    def test_setup_banner_opens_settings_and_settings_save_and_test(self):
        win = self.window
        self.controller.store.set_setting('llm_model', '')
        win.buffer.insert_at_cursor('words')
        self.pump()
        self.app.activate_action('clean-up', None)
        self.assertTrue(win.cleanup_view.banner.get_revealed())
        self.assertEqual(win.cleanup_view.banner.get_button_label(), 'Open Settings')
        win.cleanup_view.banner.emit('button-clicked')
        dialog = win.settings_dialog
        self.pump(lambda: win.get_visible_dialog() is dialog)
        self.run_llm()
        self.pump()
        self.assertFalse(win.cleanup_view.banner.get_revealed())
        rows = {}
        def walk(widget):
            for kind in ('EntryRow', 'PasswordEntryRow', 'ActionRow', 'ComboRow'):
                if type(widget).__name__ == kind:
                    rows[widget.get_title()] = widget
            child = widget.get_first_child()
            while child is not None:
                walk(child)
                child = child.get_next_sibling()
        walk(dialog)
        self.assertEqual(rows['Address'].get_text(), 'https://openrouter.ai/api/v1')
        self.assertEqual(rows['API key'].get_text(), 'sk-test')
        rows['Model'].set_text('cheap/model')
        rows['API key'].set_text('sk-new')
        button = rows['Test connection'].get_first_child()
        test_buttons = []
        def find_buttons(widget):
            if type(widget).__name__ == 'Button' and widget.get_label() == 'Test':
                test_buttons.append(widget)
            child = widget.get_first_child()
            while child is not None:
                find_buttons(child)
                child = child.get_next_sibling()
        find_buttons(rows['Test connection'])
        test_buttons[0].emit('clicked')
        self.run_llm()
        self.pump(lambda: rows['Test connection'].get_subtitle().startswith('Connected'))
        self.assertIn('Model found: cheap/model', rows['Test connection'].get_subtitle())
        self.assertEqual(self.keys.get('https://openrouter.ai/api/v1'), 'sk-new')
        from dogspeak_app import llm
        names = [name for name, _url, _key in llm.PROVIDERS]
        rows['Provider'].set_selected(names.index('Claude (Anthropic)'))
        self.assertEqual(rows['Address'].get_text(), 'https://api.anthropic.com')
        self.assertEqual(rows['Model'].get_text(), 'claude-opus-5-5')
        rows['Model'].set_text('cheap/model')
        rows['Provider'].set_selected(names.index('Ollama on this computer'))
        self.assertEqual(rows['Address'].get_text(), 'http://localhost:11434/v1')
        dialog.close()
        self.pump(lambda: win.get_visible_dialog() is None)
        config = self.window.cleanup.config()
        self.assertEqual(config['base_url'], 'http://localhost:11434/v1')
        self.assertEqual(config['model'], 'cheap/model')
        self.assertEqual(config['provider'], 'Ollama on this computer')

    def test_suggestions_dismiss_and_panel_toggle_persist(self):
        win = self.window
        win.buffer.insert_at_cursor('words')
        self.pump()
        self.app.activate_action('clean-up', None)
        self.run_llm()
        view = win.cleanup_view
        self.assertTrue(view.panel.get_reveal_child())
        first = view.suggestion_list.get_first_child()
        dismiss = first.get_last_child()
        dismiss.emit('clicked')
        self.assertEqual(view.panel_count.get_text(), '1')
        view.panel_button.set_active(False)
        self.assertFalse(view.panel.get_reveal_child())
        self.assertEqual(self.controller.store.get_setting('suggestions_panel'), '0')

    def test_purpose_menu_lists_presets_and_recent(self):
        view = self.window.cleanup_view
        self.window.cleanup.set_purpose('Commit message', remember=True)
        view._fill_purposes()
        labels = []
        child = view.purpose_list.get_first_child()
        while child is not None:
            label = child.get_child().get_first_child() if type(child).__name__ == 'Button' else child
            labels.append(label.get_label())
            child = child.get_next_sibling()
        self.assertEqual(labels, ['General purpose (blank)', 'Presets', 'Prompt for an AI agent',
                                  'Essay', 'Email', 'Notes', 'Recent', 'Commit message'])
        view.pick_purpose('Email')
        self.assertEqual(view.purpose.get_text(), 'Email')
        self.assertEqual(self.controller.store.get_setting('purpose'), 'Email')
        view.pick_purpose('')
        self.assertEqual(self.window.cleanup.purpose, '')

    def use_shell(self):
        shell = FakeShell()
        self.window.shell = shell
        self.window._shell_changed()
        return shell

    def test_shell_overlay_tracks_recording_grabs_keys_and_copies_result(self):
        shell = self.use_shell()
        self.controller.focused = lambda: False
        self.engine.emit('ready')
        self.window.set_visible(False)
        self.app.activate_action('toggle-recording', None)
        self.assertFalse(self.window.get_visible())
        state, title, hint = shell.shown[-1]
        self.assertEqual(state, 'recording')
        self.assertTrue(title.startswith('Recording'))
        self.assertIn('Enter stops', hint)
        self.assertEqual(shell.keys[-1], ('stop', 'cancel'))
        calls = len(shell.shown)
        self.window.render_status()
        self.assertEqual(len(shell.shown), calls, 'unchanged status must not be resent')
        self.window._overlay_key('stop')
        self.assertEqual(shell.shown[-1][0], 'transcribing')
        self.assertEqual(shell.keys[-1], ('cancel',))
        _path, request_id = self.engine.jobs[-1]
        self.engine.emit('result', id=request_id, text='Dictated elsewhere')
        self.assertEqual(shell.clipboard, ['Dictated elsewhere'])
        self.assertEqual(shell.shown[-1], ('saved', 'Saved', 'Copied · paste with Ctrl+V'))
        self.assertEqual(shell.keys[-1], ())
        self.assertIn('Dictated elsewhere', self.text())
        self.pump(lambda: shell.shown[-1] is None, timeout=4)

    def test_shell_escape_cancels_and_releases_keys(self):
        shell = self.use_shell()
        self.engine.emit('ready')
        self.app.activate_action('toggle-recording', None)
        self.window._overlay_key('cancel')
        self.assertFalse(self.controller.recording)
        self.assertEqual(shell.shown[-1], ('info', 'Cancelled', ''))
        self.assertEqual(shell.keys[-1], ())
        self.assertEqual(self.engine.jobs, [])

    def test_shortcut_during_loading_explains_then_announces_ready(self):
        shell = self.use_shell()
        self.window.set_visible(False)
        self.app.activate_action('toggle-recording', None)
        self.assertFalse(self.controller.recording_job)
        self.assertEqual(shell.shown[-1][1], 'Dogspeak is loading…')
        self.engine.emit('ready')
        self.window.render_status()
        self.assertEqual(shell.shown[-1][1], 'Dogspeak is ready')
        self.assertFalse(self.controller.recording_job, 'ready must never start a recording')

    def test_quit_releases_shell_keys_and_ends_the_application(self):
        shell = self.use_shell()
        quits = []
        self.app.quit = lambda: quits.append(True)
        self.engine.emit('ready')
        self.app.activate_action('toggle-recording', None)
        self.app.activate_action('quit', None)
        self.pump(lambda: self.controller.closed and self.engine.closed and quits)
        self.assertFalse(shell.available)
        self.assertEqual(quits, [True])


if __name__ == '__main__':
    unittest.main()
