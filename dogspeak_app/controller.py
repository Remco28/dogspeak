"""Draft and session state, shared by every frontend. No toolkit imports here.

The frontend supplies dispatch (run on the UI thread) and the hooks below
(on_draft, on_status, focused, insertion_point, hold_result, on_transcript).
"""
from dataclasses import replace
from pathlib import Path
import os
import stat
import sys
import threading
import time
import uuid

from .storage import transcript_insertion
from .ui_values import MAX_RECORDING_SECONDS

MAX_IMPORT_BYTES = 256 * 1024 * 1024


def background(fn):
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    return thread


def move_lines(lines, first, last, down):
    """Move lines[first..last] past their neighbour; (lines, first, new first) or None.

    A whole paragraph swaps with the next one and every blank line stays put. A
    line within a paragraph swaps with the next line, or at the paragraph's edge
    joins the next paragraph. Moving back always undoes a move.
    """
    def blank(i):
        return not lines[i].strip()

    def block_start(i):
        while i > 0 and not blank(i - 1):
            i -= 1
        return i

    def block_end(i):
        while i < len(lines) - 1 and not blank(i + 1):
            i += 1
        return i

    if any(blank(i) for i in range(first, last + 1)):
        while first <= last and blank(first):
            first += 1
        while last >= first and blank(last):
            last -= 1
        if first > last:
            return None
        first, last = block_start(first), block_end(last)
    whole = block_start(first) == first and block_end(last) == last
    unit = lines[first:last + 1]
    if down:
        after = last + 1
        if after == len(lines):
            return None
        if not blank(after):
            return lines[:first] + [lines[after]] + unit + lines[after + 1:], first, first + 1
        start = after
        while start < len(lines) and blank(start):
            start += 1
        if start == len(lines):
            return None
        gap = lines[after:start]
        if not whole:
            return lines[:first] + gap + unit + lines[start:], first, first + len(gap)
        end = block_end(start)
        other = lines[start:end + 1]
        return (lines[:first] + other + gap + unit + lines[end + 1:], first,
                first + len(other) + len(gap))
    before = first - 1
    if before < 0:
        return None
    if not blank(before):
        return lines[:before] + unit + [lines[before]] + lines[last + 1:], first, before
    end = before
    while end >= 0 and blank(end):
        end -= 1
    if end < 0:
        return None
    gap = lines[end + 1:first]
    if not whole:
        return lines[:end + 1] + unit + gap + lines[last + 1:], first, end + 1
    start = block_start(end)
    return lines[:start] + unit + gap + lines[start:end + 1] + lines[last + 1:], first, start


class Controller:
    """Main-thread draft/session state; only capture finalization runs off-thread."""

    def __init__(self, store, engine_factory, recorder, data_dir, dispatch,
                 spawn=background, clock=time.monotonic, shortcut_label='Super+R'):
        # dispatch(fn, *args) runs fn on the UI thread soon; any toolkit can supply it.
        self.store, self.recorder = store, recorder
        self.shortcut_label = shortcut_label
        self.data_dir = Path(data_dir)
        self.dispatch, self.spawn, self.clock = dispatch, spawn, clock
        self.closed = False
        self.stopping = False
        self._cleanup_started = self._cleanup_done = False
        self._close_done = None
        self._recorder_lock = threading.Lock()
        self.state, self.message, self.ready = 'loading', 'Loading model…', False
        self.pending = {}
        self.recording_job = None
        self.source = store.get_setting('microphone') or None
        self.started_at = self.clock()
        self.dirty = False
        # Last finished job: 'saved', 'empty' or 'cancelled'; cleared when a new one starts.
        self.outcome = None
        self.on_draft = lambda draft, append=False, at=None: None
        self.on_status = lambda: None
        # Whether the editor has focus; recordings started elsewhere are copied when done.
        self.focused = lambda: True
        # Where a transcript for the shown draft goes; None means the end of the draft.
        self.insertion_point = lambda: None
        # True if a result must wait for the user to stop typing; it is delivered again later.
        self.hold_result = lambda event: False
        self.held = {}
        self.on_transcript = lambda text: None
        # Drafts another feature (Clean up) is still writing to.
        self.locked_drafts = lambda: set()
        last = store.get_setting('last_draft')
        try:
            self.draft = store.get_draft(last) if last else None
        except KeyError:
            self.draft = None
        if self.draft is None:
            drafts = store.list_drafts()
            self.draft = drafts[0] if drafts else store.create_draft()
        self.title, self.content = self.draft.title, self.draft.content
        self.store.set_setting('last_draft', self.draft.id)
        self.engine_factory = engine_factory
        self.engine_failed = False
        self.engine = engine_factory(self.receive)
        self.engine.start()

    def restart_engine(self):
        """Explicit retry after the worker died; never queues a recording."""
        if self.closed or self.stopping or not self.engine_failed:
            return False
        old = self.engine
        self.engine_failed = False
        self.spawn(old.close)
        self.engine = self.engine_factory(self.receive)
        self.status('loading', 'Restarting model…')
        self.engine.start()
        return True

    def status(self, state, message):
        self.state, self.message = state, message
        self.started_at = self.clock()
        self.on_status()

    def receive(self, event):
        # EngineClient calls this on its reader thread. Never touch GTK/SQLite there.
        self.dispatch(self._event, event)

    def _event(self, event):
        if self.closed or self.stopping:
            return False
        kind = event.get('type')
        if kind == 'loading':
            self.status('loading', 'Loading model…')
        elif kind == 'ready':
            self.ready = True
            self.status('ready', self._ready_message())
            for request_id, job in list(self.pending.items()):
                if not job['submitted'] and not job.get('copying'):
                    self._submit(request_id)
        elif kind == 'result':
            job = self.pending.get(event.get('id'))
            if job is None:
                return False
            # Clean up keeps text that landed after what it sent, so stay at the end.
            at = None
            if (job.get('at_cursor') and job['draft_id'] == self.draft.id
                    and job['draft_id'] not in self.locked_drafts()):
                at = self.insertion_point()
            if at is not None and self.hold_result(event):
                self.held[event['id']] = event
                self.status('transcribing', 'Waiting for you to pause typing…')
                return False
            self.held.pop(event['id'], None)
            try:
                self.flush()
                draft = self.store.append_transcript(
                    job['draft_id'], event['id'], str(job['path']), event.get('text', ''), at)
                # Stored: the job is finished even if showing it fails below.
                self.pending.pop(event['id'])
                self._forget_audio(event['id'], job.get('path'))
                if draft.id == self.draft.id:
                    self.draft = draft
                    self.title, self.content = draft.title, draft.content
                    self.on_draft(draft, append=True, at=at)
                text = event.get('text', '').strip()
                self.outcome = 'saved' if text else 'empty'
                if text and job.get('background'):
                    self.on_transcript(text)
                self.status('ready', self._ready_message() if text else 'No speech detected')
            except Exception as exc:
                self.status('error', f'Could not save transcription: {exc}')
        elif kind == 'error':
            fatal = bool(event.get('fatal'))
            request_id = event.get('id')
            if not fatal and request_id is not None and request_id not in self.pending:
                return False  # a job the user already dropped with Esc
            job = self.pending.pop(request_id, None)
            if job:
                self._forget_audio(request_id, job.get('path'))
            if fatal:
                self.ready = False
                self.engine_failed = True
                for dropped in self._drop_jobs():
                    self._forget_audio(dropped.get('id'), dropped.get('path'))
                self.spawn(self._cancel_capture)
            self.status('error', event.get('message', 'Transcription failed'))
        return False

    def _drop_jobs(self):
        jobs = list(self.pending.values())
        if self.recording_job:
            jobs.append(self.recording_job)
        self.pending.clear()
        self.recording_job = None
        return jobs

    def _ready_message(self):
        return f'Ready · {self.shortcut_label}'

    def _forget_audio(self, request_id, path=None):
        stored = None
        try:
            stored = self.store.forget_audio(request_id)
        except Exception:
            stored = None
        for candidate in (stored, path):
            if not candidate:
                continue
            try:
                Path(candidate).unlink(missing_ok=True)
            except OSError:
                pass

    def _submit(self, request_id):
        job = self.pending[request_id]
        job['submitted'] = True
        self.status('transcribing', 'Transcribing…')
        try:
            self.engine.transcribe(job['path'], request_id)
        except Exception as exc:
            self._event({'type': 'error', 'id': request_id, 'message': str(exc)})

    def import_audio(self, path):
        if self.closed or self.stopping or self.pending or self.recording_job:
            return False
        try:
            source = Path(path).expanduser().resolve(strict=True)
            draft_id = self.draft.id
            request_id = uuid.uuid4().hex
            recordings = self.data_dir / 'recordings'
            recordings.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination = recordings / (request_id + source.suffix)
            # Non-blocking open so a FIFO can't hang the UI; Windows has neither.
            nonblock = getattr(os, 'O_NONBLOCK', 0)
            fd = os.open(source, os.O_RDONLY | nonblock | getattr(os, 'O_BINARY', 0))
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise OSError('Audio import requires a regular file')
                if info.st_size > MAX_IMPORT_BYTES:
                    raise OSError(
                        f'Audio file is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MiB')
                if nonblock:
                    os.set_blocking(fd, True)
            except Exception:
                os.close(fd)
                raise
            self.pending[request_id] = {
                'draft_id': draft_id, 'path': destination, 'submitted': False,
                'copying': True}
            self.status('loading' if not self.ready else 'transcribing',
                        'Importing audio…')
            self.spawn(lambda: self._copy_import(fd, request_id, destination, info.st_size))
            return True
        except Exception as exc:
            self.status('error', f'Could not import audio: {exc}')
            return False

    def _copy_import(self, fd, request_id, destination, expected_size):
        error = None
        copied = 0
        try:
            with os.fdopen(fd, 'rb') as src:
                if self.closed or self.stopping or request_id not in self.pending:
                    raise OSError('Import cancelled')
                with destination.open('xb') as dst:
                    destination.chmod(0o600)
                    while True:
                        if self.closed or self.stopping or request_id not in self.pending:
                            raise OSError('Import cancelled')
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > MAX_IMPORT_BYTES:
                            raise OSError(
                                f'Audio file is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MiB')
                        dst.write(chunk)
                    dst.flush()
                    os.fsync(dst.fileno())
            if copied != expected_size and expected_size:
                raise OSError('Audio file size changed during import')
        except Exception as exc:
            error = str(exc)
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
        self.dispatch(self._imported, request_id, destination, error)

    def _imported(self, request_id, destination, error):
        job = self.pending.get(request_id)
        if job is None or self.closed or self.stopping:
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        job['copying'] = False
        if error:
            self.pending.pop(request_id, None)
            try:
                destination.unlink(missing_ok=True)
            except OSError:
                pass
            self.status('error', f'Could not import audio: {error}')
            return False
        job['path'] = destination
        if self.ready:
            self._submit(request_id)
        else:
            self.status('loading', 'Loading model… Audio queued')
        return False

    @property
    def recording(self):
        return self.recording_job is not None and not self.recording_job.get('stopping', False)

    def toggle_recording(self):
        if self.closed or self.stopping:
            return False
        if self.recording:
            self.stop_recording()
            return True
        if self.engine_failed:
            self.restart_engine()
            return False
        if not self.ready or self.pending or self.recording_job:
            return False
        # A cancelled capture may still be finalizing on its thread.
        if not self._recorder_lock.acquire(blocking=False):
            return False
        request_id = uuid.uuid4().hex
        path = self.data_dir / 'recordings' / (request_id + '.wav')
        try:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.recorder.start(path, target=self.source)
            focused = self.focused()
            self.recording_job = {
                'id': request_id, 'draft_id': self.draft.id, 'path': path,
                'submitted': False, 'started_at': self.clock(),
                'at_cursor': focused, 'background': not focused}
            self.outcome = None
            self.status('recording', 'Recording')
            return True
        except Exception as exc:
            self.recorder.cancel()
            self.status('error', f'Could not record: {exc}')
            return False
        finally:
            self._recorder_lock.release()

    def stop_recording(self):
        if not self.recording:
            return
        job = self.recording_job
        job['stopping'] = True
        self.status('transcribing', 'Finishing recording…')

        def finish():
            try:
                with self._recorder_lock:
                    path = self.recorder.stop()
                self.dispatch(self._recorded, job, path, None)
            except Exception as exc:
                try:
                    self._cancel_capture()
                except Exception as cancel_error:
                    exc = RuntimeError(f'{exc}; capture cleanup: {cancel_error}')
                self.dispatch(self._recorded, job, None, str(exc))
        self.spawn(finish)

    def _recorded(self, job, path, error):
        if self.closed or self.stopping or self.recording_job is not job:
            return False
        self.recording_job = None
        if error:
            self.status('error', f'Could not finish recording: {error}')
        else:
            job['path'] = Path(path)
            self.pending[job['id']] = job
            self._submit(job['id'])
        return False

    def cancel_recording(self):
        if self.closed or self.stopping:
            return False
        cancelled = False
        if self.recording_job:
            job = self.recording_job
            self.recording_job = None
            self.spawn(self._cancel_capture)
            if job:
                try:
                    Path(job['path']).unlink(missing_ok=True)
                except OSError:
                    pass
            cancelled = True
        if self.pending:
            jobs = list(self.pending.values())
            self.pending.clear()
            for job in jobs:
                self._forget_audio(job.get('id'), job.get('path'))
            cancelled = True
        if cancelled:
            self.outcome = 'cancelled'
            self.status('ready', self._ready_message() if self.ready else 'Loading model…')
        return cancelled

    def tick(self):
        if self.recording and (
                not self.recorder.running or
                self.clock() - self.recording_job['started_at'] >= MAX_RECORDING_SECONDS):
            self.stop_recording()
        self.on_status()
        return not self.closed

    def edit(self, title, content):
        self.title, self.content = title, content
        self.dirty = True

    def flush(self):
        if self.dirty and not self.closed:
            self.draft = self.store.save_draft(self.draft.id, self.content, self.title)
            self.dirty = False

    def _select(self, draft):
        self.draft = draft
        self.title, self.content = draft.title, draft.content
        self.store.set_setting('last_draft', draft.id)
        self.on_draft(draft)

    def new_draft(self):
        self.flush()
        self._select(self.store.create_draft())

    def open_draft(self, draft_id):
        self.flush()
        self._select(self.store.get_draft(draft_id))

    def delete_draft(self, draft_id):
        """Delete a draft unless a recording or transcript is headed for it."""
        busy = [job for job in list(self.pending.values()) + [self.recording_job]
                if job and job.get('draft_id') == draft_id]
        if busy:
            raise RuntimeError('This draft is waiting for a transcript; finish or cancel it first')
        if draft_id in self.locked_drafts():
            raise RuntimeError('This draft is being cleaned up; wait or press Esc first')
        if draft_id == self.draft.id:
            self.dirty = False  # its unsaved edits go with it
        for path in self.store.delete_draft(draft_id):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        if draft_id == self.draft.id:
            drafts = self.store.list_drafts()
            self._select(drafts[0] if drafts else self.store.create_draft())
        return True

    def _cancel_capture(self):
        with self._recorder_lock:
            self.recorder.cancel()

    def discard(self, done=None):
        return self.close(done=done, discard=True)

    def close(self, done=None, discard=False):
        if self.closed:
            return True
        errors = []
        save_error = None
        if not discard:  # a transcript waiting for a pause in typing must not be lost
            self.hold_result = lambda event: False
            for event in list(self.held.values()):
                self._event(event)
                if event['id'] in self.pending:
                    self._keep_as_edit(event)
        if discard:
            self.dirty = False
        else:
            try:
                self.flush()
            except Exception as exc:
                save_error = f'Could not save draft: {exc}'
        # A refused close releases owned processes, not the unsaved editor/store.
        self.stopping = True
        self.ready = False
        unfinished = [job.get('path') for job in self._drop_jobs()]
        if save_error:
            self.status(
                'error',
                save_error + '. Draft kept open; retry saving or copy the text before discarding.')
        else:
            self.closed = True
            self._close_done = done
            try:
                self.store.close()
            except Exception as exc:
                errors.append(str(exc))

        def cleanup():
            # stop() can be finalizing on another thread; serialize recorder access.
            for finish in (self._cancel_capture, self.engine.close):
                try:
                    finish()
                except Exception as exc:
                    errors.append(str(exc))
            for path in unfinished:
                try:
                    if path:
                        Path(path).unlink(missing_ok=True)
                except OSError as exc:
                    errors.append(str(exc))
            self.dispatch(self._closed, errors)
        if not self._cleanup_started:
            self._cleanup_started = True
            self.spawn(cleanup)
        elif self._cleanup_done and self.closed:
            self._closed(errors)
        return self.closed

    def _keep_as_edit(self, event):
        """A transcript that wouldn't save becomes unsaved text, which close() protects."""
        text = event.get('text', '').strip()
        origin = self.pending[event['id']]['draft_id']
        if text and origin != self.draft.id:
            try:
                content = self.store.get_draft(origin).content
                self.store.save_draft(
                    origin, content + transcript_insertion(content, len(content), text))
                return
            except Exception:
                pass  # better in the wrong draft, in view, than gone
        if text:
            self.content += transcript_insertion(self.content, len(self.content), text)
            self.dirty = True
            self.on_draft(replace(self.draft, title=self.title, content=self.content), append=True)

    def _closed(self, errors):
        self._cleanup_done = True
        self.shutdown_error = '; '.join(errors)
        if self.shutdown_error:
            print(f'Dogspeak shutdown: {self.shutdown_error}', file=sys.stderr)
        if self.closed and self._close_done:
            done, self._close_done = self._close_done, None
            done()
        return False
