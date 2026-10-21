"""Native, local Dogspeak scratchpad. Run with the system Python."""
from pathlib import Path
import os
import stat
import sys
import threading
import time
import uuid


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APP_ID = 'io.github.Remco28.Dogspeak'
MAX_IMPORT_BYTES = 256 * 1024 * 1024
# The global binding desktop_install.py registers; also an in-app accelerator.
SHORTCUT_LABEL = 'Super+R'


def background(fn):
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    return thread


OVERLAY_BUS = 'io.github.Remco28.Dogspeak.Overlay'
OVERLAY_PATH = '/io/github/Remco28/Dogspeak/Overlay'
OVERLAY_IFACE = 'io.github.Remco28.Dogspeak.Overlay'


class OverlayStatus:
    """What the overlay shows; drawn by the Shell, never a GTK window GNOME would keep behind."""

    def __init__(self):
        self._text = ''
        self._visible = False
        self.state = ''
        self.detail = ''
        self.hint = ''

    def get_text(self):
        return self._text

    def set_text(self, text):
        self._text = text

    def get_visible(self):
        return self._visible

    def set_visible(self, visible):
        self._visible = bool(visible)
        if not visible:
            self._text = self.state = self.detail = self.hint = ''


class ShellOverlay:
    """Client for the dogspeak-overlay GNOME Shell extension (gnome-extension/).

    available is False when the extension is not running; callers then fall
    back to a notification banner. Calls are fire-and-forget so a stalled
    Shell can never block the editor.
    """

    def __init__(self, on_key, on_change=lambda: None, bus=None):
        self.available = False
        self.on_key, self.on_change = on_key, on_change
        self._watch = self._signal = 0
        try:
            self.bus = bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.bus = None
            return
        self._watch = Gio.bus_watch_name_on_connection(
            self.bus, OVERLAY_BUS, Gio.BusNameWatcherFlags.NONE,
            lambda *_: self._set_available(True), lambda *_: self._set_available(False))
        self._signal = self.bus.signal_subscribe(
            OVERLAY_BUS, OVERLAY_IFACE, 'KeyPressed', OVERLAY_PATH, None,
            Gio.DBusSignalFlags.NONE, self._key_pressed)

    def _set_available(self, available):
        if available != self.available:
            self.available = available
            self.on_change()

    def _key_pressed(self, _bus, _sender, _path, _iface, _signal, params):
        self.on_key(params.unpack()[0])

    def _call(self, method, params=None):
        if not self.available or self.bus is None:
            return False
        self.bus.call(OVERLAY_BUS, OVERLAY_PATH, OVERLAY_IFACE, method, params, None,
                      Gio.DBusCallFlags.NO_AUTO_START, 2000, None, None, None)
        return True

    def show(self, state, title, hint):
        return self._call('Show', GLib.Variant('(sss)', (state, title, hint)))

    def hide(self):
        return self._call('Hide')

    def set_keys(self, keys):
        return self._call('SetKeys', GLib.Variant('(as)', (list(keys),)))

    def set_clipboard(self, text):
        return self._call('SetClipboard', GLib.Variant('(s)', (text,)))

    def close(self):
        if self.bus is not None:
            if self.available:
                # Release Enter/Esc now rather than when the Shell sees us exit.
                self.set_keys([])
                self.hide()
                self.bus.flush_sync(None)
            if self._signal:
                self.bus.signal_unsubscribe(self._signal)
            if self._watch:
                Gio.bus_unwatch_name(self._watch)
        self._signal = self._watch = 0
        self.available = False


class Controller:
    """Main-thread draft/session state; only capture finalization runs off-thread."""

    def __init__(self, store, engine_factory, recorder, data_dir,
                 dispatch=GLib.idle_add, spawn=background, clock=time.monotonic):
        self.store, self.recorder = store, recorder
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
        self.on_draft = lambda draft, append=False: None
        self.on_status = lambda: None
        # Whether the editor has focus; recordings started elsewhere are copied when done.
        self.focused = lambda: True
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
            try:
                self.flush()
                draft = self.store.append_transcript(
                    job['draft_id'], event['id'], str(job['path']), event.get('text', ''))
                # Stored: the job is finished even if showing it fails below.
                self.pending.pop(event['id'])
                self._forget_audio(event['id'], job.get('path'))
                if draft.id == self.draft.id:
                    self.draft = draft
                    self.title, self.content = draft.title, draft.content
                    self.on_draft(draft, append=True)
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
        return f'Ready · {SHORTCUT_LABEL}'

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
            fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise OSError('Audio import requires a regular file')
                if info.st_size > MAX_IMPORT_BYTES:
                    raise OSError(
                        f'Audio file is larger than {MAX_IMPORT_BYTES // (1024 * 1024)} MiB')
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
            self.recording_job = {
                'id': request_id, 'draft_id': self.draft.id, 'path': path,
                'submitted': False, 'started_at': self.clock(),
                'background': not self.focused()}
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
                self.clock() - self.recording_job['started_at'] >= 115):
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

    def _closed(self, errors):
        self._cleanup_done = True
        self.shutdown_error = '; '.join(errors)
        if self.shutdown_error:
            print(f'Dogspeak shutdown: {self.shutdown_error}', file=sys.stderr)
        if self.closed and self._close_done:
            done, self._close_done = self._close_done, None
            done()
        return False


class ScratchpadWindow(Adw.ApplicationWindow):
    def __init__(self, app, controller, sources_provider, cleanup=None):
        super().__init__(application=app, title='Dogspeak', default_width=1100,
                         default_height=720)
        self.controller = controller
        self.rendering, self.save_source, self.closing = False, 0, False
        self.drafts_dialog = self.save_error_dialog = self.settings_dialog = None
        if cleanup is None:
            from .cleanup import Cleanup
            from .credentials import Keyring
            cleanup = Cleanup(controller, Keyring())
        from .cleanup_ui import CleanupView
        self.cleanup = cleanup
        self.cleanup_view = CleanupView(self, cleanup)
        self.displayed_draft = None
        layout = Adw.ToolbarView()
        self.set_content(layout)
        header = Adw.HeaderBar()
        self.title_entry = Gtk.Entry(placeholder_text='Draft title', width_chars=22,
                                     max_width_chars=36, hexpand=True)
        self.title_entry.set_tooltip_text('Draft title')
        header.set_title_widget(self.title_entry)
        for action, icon, tooltip in [
            ('new-draft', 'document-new-symbolic', 'New draft (Ctrl+N)'),
            ('open-draft', 'document-open-symbolic', 'Open a draft (Ctrl+O)'),
        ]:
            header.pack_start(Gtk.Button(icon_name=icon, action_name='app.' + action,
                                         tooltip_text=tooltip))
        self.record_button = Gtk.Button(action_name='app.toggle-recording',
                                        tooltip_text=f'Record / stop ({SHORTCUT_LABEL})')
        record_box = Gtk.Box(spacing=6)
        self.record_icon = Gtk.Image(icon_name='media-record-symbolic')
        self.record_label = Gtk.Label(label='Record')
        record_box.append(self.record_icon)
        record_box.append(self.record_label)
        self.record_button.set_child(record_box)
        menu = Gio.Menu()
        menu.append('Settings', 'app.settings')
        menu.append('Quit', 'app.quit')
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu,
                                       tooltip_text='Main menu', primary=True))
        header.pack_end(self.record_button)
        header.pack_end(self.cleanup_view.panel_button)
        header.pack_end(Gtk.Button(icon_name='edit-copy-symbolic', action_name='app.copy-all',
                                   tooltip_text='Copy all (Ctrl+Shift+C)'))
        layout.add_top_bar(header)
        layout.add_top_bar(self.cleanup_view.bar)
        layout.add_top_bar(self.cleanup_view.banner)
        self.buffer = Gtk.TextBuffer()
        self.buffers = {}
        self.buffer.set_enable_undo(True)
        self.editor = Gtk.TextView(buffer=self.buffer, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                   left_margin=40, right_margin=40, top_margin=28,
                                   bottom_margin=28, pixels_above_lines=3,
                                   pixels_below_lines=3, vexpand=True)
        self.editor.add_css_class('view')
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.editor)
        content = Gtk.Box()
        scroll.set_hexpand(True)
        content.append(scroll)
        content.append(self.cleanup_view.panel)
        layout.set_content(content)
        status = Gtk.Box(spacing=10, margin_start=16, margin_end=16,
                         margin_top=10, margin_bottom=10)
        self.spinner = Gtk.Spinner()
        self.status_label = Gtk.Label(xalign=0, hexpand=True, wrap=True, selectable=True)
        self.timer_label = Gtk.Label(label='', width_chars=5)
        self.timer_label.add_css_class('dim-label')
        status.append(self.spinner)
        status.append(self.status_label)
        status.append(self.timer_label)
        self.sources = [None]
        self.updating_sources = False
        self.microphone = Gtk.DropDown(model=Gtk.StringList.new(['System default']))
        self.microphone.set_tooltip_text('Microphone')
        self.microphone.set_size_request(160, -1)
        self.microphone.connect('notify::selected', self._microphone_changed)
        status.append(self.microphone)
        layout.add_bottom_bar(status)
        self.buffer.connect('changed', self._changed)
        self.title_entry.connect('changed', self._changed)
        self.controller.on_draft = self.render_draft
        self.controller.on_status = self.render_status
        self.set_hide_on_close(True)
        self.connect('close-request', self._close_requested)
        self._install_session_keys(self)
        self._init_overlay(app)
        self.render_draft(controller.draft)
        self.cleanup_view.refresh()
        self.tick_source = GLib.timeout_add(200, controller.tick)
        # pw-dump may block; discovery cannot stall the editor or model events.
        def discover():
            try:
                sources = sources_provider()
            except Exception:
                sources = []
            GLib.idle_add(self._set_sources, sources)
        controller.spawn(discover)

    def _set_sources(self, sources):
        if self.closing:
            return False
        self.sources = [None] + [source['name'] for source in sources]
        labels = ['System default'] + [source['description'] for source in sources]
        selected = self.sources.index(self.controller.source) if self.controller.source in self.sources else 0
        self.updating_sources = True
        self.microphone.set_model(Gtk.StringList.new(labels))
        self.microphone.set_selected(selected)
        self.updating_sources = False
        self._microphone_changed()
        return False

    def _microphone_changed(self, *_):
        if self.updating_sources:
            return
        index = self.microphone.get_selected()
        if index < len(self.sources):
            self.controller.source = self.sources[index]
            self.perform(lambda: self.controller.store.set_setting(
                'microphone', self.controller.source or ''))

    def perform(self, fn):
        try:
            return fn()
        except Exception as exc:
            self.controller.status('error', str(exc))

    def _changed(self, *_):
        if self.rendering or self.closing:
            return
        self.controller.edit(self.title_entry.get_text(), self.buffer.get_text(
            self.buffer.get_start_iter(), self.buffer.get_end_iter(), True))
        if self.save_source:
            GLib.source_remove(self.save_source)
        self.save_source = GLib.timeout_add(500, self._autosave)

    def _autosave(self):
        self.save_source = 0
        self.perform(self.controller.flush)
        self.cleanup_view.refresh()  # the shown version follows undo and typing
        return False

    def render_draft(self, draft, append=False):
        switching = self.displayed_draft != draft.id
        self.displayed_draft = draft.id
        self.rendering = True
        try:
            if draft.id in self.buffers:
                self.buffer = self.buffers[draft.id]
                self.editor.set_buffer(self.buffer)
                append = True
            else:
                if self.buffers:
                    self.buffer = Gtk.TextBuffer()
                    self.buffer.set_enable_undo(True)
                    self.buffer.connect('changed', self._changed)
                    self.editor.set_buffer(self.buffer)
                self.buffers[draft.id] = self.buffer
            if append:
                old = self.buffer.get_text(self.buffer.get_start_iter(),
                                           self.buffer.get_end_iter(), True)
                if not draft.content.startswith(old):
                    raise RuntimeError('Draft changed outside the editor; reopen it to reconcile')
                suffix = draft.content[len(old):]
                if suffix:
                    cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
                    bound = self.buffer.get_iter_at_mark(self.buffer.get_selection_bound()).get_offset()
                    self.buffer.begin_user_action()
                    self.buffer.insert(self.buffer.get_end_iter(), suffix)
                    self.buffer.end_user_action()
                    self.buffer.select_range(self.buffer.get_iter_at_offset(cursor),
                                             self.buffer.get_iter_at_offset(bound))
            else:
                self.buffer.set_enable_undo(False)
                self.buffer.set_text(draft.content)
                self.buffer.set_enable_undo(True)
            self.title_entry.set_text(draft.title)
        finally:
            self.rendering = False
        if switching:
            self.editor.grab_focus()
            if getattr(self, 'cleanup_view', None) is not None:
                self.cleanup_view.refresh()

    def replace_text(self, draft_id, text):
        """Swap a draft's whole text as one undoable step (Ctrl+Z brings it back)."""
        buffer = self.buffers.get(draft_id)
        if buffer is None:
            return
        self.rendering = True
        try:
            buffer.begin_user_action()
            buffer.delete(buffer.get_start_iter(), buffer.get_end_iter())
            buffer.insert(buffer.get_start_iter(), text)
            buffer.end_user_action()
        finally:
            self.rendering = False

    def render_status(self):
        c = self.controller
        active = c.recording or c.state in ('loading', 'transcribing')
        self.status_label.set_text(c.message)
        self.status_label.set_tooltip_text(c.message)
        started_at = c.recording_job['started_at'] if c.recording else c.started_at
        elapsed = int(max(0, c.clock() - started_at))
        self.timer_label.set_text(f'{elapsed // 60:02}:{elapsed % 60:02}' if active else '')
        self.spinner.set_spinning(c.state in ('loading', 'transcribing'))
        recording = c.recording
        self.record_label.set_text('Stop' if recording else 'Record')
        self.record_icon.set_from_icon_name('media-playback-stop-symbolic' if recording else 'media-record-symbolic')
        self.record_button.set_sensitive(not c.closed and (recording or c.engine_failed or (
            c.ready and not c.pending and not c.recording_job)))
        self.microphone.set_sensitive(not recording and not c.recording_job)
        if recording:
            self.record_button.add_css_class('destructive-action')
        else:
            self.record_button.remove_css_class('destructive-action')
        cleanup = getattr(self, 'cleanup', None)
        if cleanup is not None:
            self.cleanup_view.clean_button.set_sensitive(cleanup.can_start())
            # Clean up doesn't need the speech model, so it outranks "Loading model…".
            if cleanup.busy and not (c.recording or c.state == 'transcribing'):
                from .llm import provider_name
                name = provider_name(cleanup.busy['config']['base_url'])
                self.status_label.set_text(f'Sending this draft to {name}… Esc cancels.')
                self.spinner.set_spinning(True)
        self._sync_overlay()

    def show_drafts(self):
        self.controller.flush()
        dialog = Adw.Dialog(title='Open a draft', content_width=440, content_height=420)
        self.drafts_dialog = dialog
        box = Adw.ToolbarView()
        box.add_top_bar(Adw.HeaderBar())
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, margin_start=16,
                           margin_end=16, margin_top=12, margin_bottom=12)
        rows.add_css_class('boxed-list')
        for draft in self.controller.store.list_drafts():
            row = Adw.ActionRow(title=draft.title or 'Untitled',
                                subtitle=draft.content.replace('\n', ' ')[:100] or 'Empty draft',
                                activatable=True)
            row.set_use_markup(False)
            def select(_row, draft_id=draft.id):
                self.perform(lambda: self.controller.open_draft(draft_id))
                dialog.close()
                self.editor.grab_focus()
            row.connect('activated', select)
            delete = Gtk.Button(icon_name='user-trash-symbolic', valign=Gtk.Align.CENTER,
                                tooltip_text='Delete draft')
            delete.add_css_class('flat')
            delete.connect('clicked', lambda _b, d=draft, r=row: self.confirm_delete(d, r))
            row.add_suffix(delete)
            rows.append(row)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(rows)
        box.set_content(scroll)
        dialog.set_child(box)
        dialog.present(self)

    def show_settings(self):
        from .cleanup_ui import present_settings
        self.present()
        return present_settings(self, self.cleanup)

    def confirm_delete(self, draft, row=None):
        title = draft.title or 'Untitled'
        alert = Adw.AlertDialog(heading=f'Delete “{title}”?',
                                body='The draft and its saved transcripts are removed. This cannot be undone.')
        alert.add_response('cancel', 'Cancel')
        alert.add_response('delete', 'Delete')
        alert.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        alert.set_default_response('cancel')
        alert.set_close_response('cancel')

        def respond(_alert, response):
            if response == 'delete':
                self.delete_draft(draft.id, row)
        alert.connect('response', respond)
        alert.present(self)
        return alert

    def delete_draft(self, draft_id, row=None):
        displayed = self.displayed_draft
        if self.perform(lambda: self.controller.delete_draft(draft_id)):
            self.buffers.pop(draft_id, None)
            if displayed == draft_id:
                self.displayed_draft = None
                self.render_draft(self.controller.draft)
            if row is not None and row.get_parent() is not None:
                row.get_parent().remove(row)
            return True
        self.render_status()
        return False

    def copy_all(self):
        text = self.buffer.get_text(self.buffer.get_start_iter(), self.buffer.get_end_iter(), True)
        # From the tray menu the window is unfocused, and Wayland ignores its selection.
        shell = getattr(self, 'shell', None)
        if not self.is_active() and shell is not None and shell.available:
            shell.set_clipboard(text)
        else:
            self.get_clipboard().set(text)

    def _pause_window(self):
        self.closing = True
        for source in (self.save_source, self.tick_source):
            if source:
                GLib.source_remove(source)
        self.save_source = self.tick_source = 0
        self.set_sensitive(False)
        self.status_label.set_text('Closing…')

    def _resume_window(self):
        self.closing = False
        self.set_sensitive(True)
        if not self.tick_source:
            self.tick_source = GLib.timeout_add(200, self.controller.tick)
        if self.controller.dirty and not self.save_source:
            self.save_source = GLib.timeout_add(500, self._autosave)
        self.render_status()
        self.editor.grab_focus()

    def _show_save_error(self):
        dialog = Adw.AlertDialog(
            heading='Could not save draft',
            body=self.controller.message)
        dialog.add_response('keep', 'Keep editing')
        dialog.add_response('copy', 'Copy text')
        dialog.add_response('discard', 'Discard')
        dialog.set_response_appearance('discard', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('keep')
        dialog.set_close_response('keep')
        dialog.connect('response', self._on_unsaved_response)
        self.save_error_dialog = dialog
        dialog.present(self)

    def _on_unsaved_response(self, dialog, response):
        if self.save_error_dialog is dialog:
            self.save_error_dialog = None
        if response == 'copy':
            self.copy_all()
        elif response == 'discard':
            self._pause_window()
            self.controller.discard(done=self._destroyed)

    def _destroyed(self):
        # Ask before destroy(): a destroyed window no longer has an application,
        # and the app holds itself open for the hidden window.
        app = self.get_application()
        self._destroy_overlay()
        self.destroy()
        if app is not None:
            app.quit()

    def handle_session_key(self, keyval):
        if self.get_visible_dialog() is not None:
            return False  # Esc closes the dialog; it must not drop a transcript
        if keyval == Gdk.KEY_Escape and self.cleanup.busy and not self.controller.recording_job:
            return self.cleanup.cancel()
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if self.controller.recording:
                self.controller.stop_recording()
                return True
            return False
        if keyval == Gdk.KEY_Escape:
            return bool(self.controller.cancel_recording())
        return False

    def _on_session_key(self, _controller, keyval, _keycode, _state):
        return self.handle_session_key(keyval)

    def _install_session_keys(self, widget):
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect('key-pressed', self._on_session_key)
        widget.add_controller(keys)

    def overlay_notify(self, title, body, resident):
        app = self.get_application()
        if app is None:
            return
        if not title:
            app.withdraw_notification('status')
            return
        note = Gio.Notification.new(title)
        if body:
            note.set_body(body)
        note.set_icon(Gio.ThemedIcon.new('audio-input-microphone-symbolic'))
        note.set_priority(
            Gio.NotificationPriority.URGENT if resident else Gio.NotificationPriority.NORMAL)
        app.send_notification('status', note)

    def _init_overlay(self, _app):
        self.overlay = OverlayStatus()
        self.overlay_label = self.overlay
        self._overlay_hide = 0
        self._overlay_sent = self._overlay_keys = self._overlay_noted = None
        self._copied = self._announce_ready = False
        self.shell = ShellOverlay(self._overlay_key, self._shell_changed)
        self.controller.focused = lambda: self.get_visible() and self.is_active()
        self.controller.on_transcript = self._copy_transcript
        self.connect('destroy', self._destroy_overlay)

    def toggle_recording(self):
        c = self.controller
        if c.toggle_recording():
            return True
        # A refused global shortcut must say why; the editor may be hidden.
        if not self.is_active():
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
        if self.shell.available:
            return 'Enter stops · Esc cancels'
        return f'{SHORTCUT_LABEL} stops' + (' · Esc cancels' if self.is_active() else '')

    def _overlay_target(self):
        c = self.controller
        if c.recording:
            elapsed = int(max(0, c.clock() - c.recording_job['started_at']))
            return 'recording', 'Recording', f'{elapsed // 60}:{elapsed % 60:02}', self._stop_hint()
        if c.state == 'transcribing' and (c.pending or c.recording_job):
            return 'transcribing', c.message, '', 'Esc drops this result'
        return None

    def _sync_overlay(self):
        overlay = getattr(self, 'overlay', None)
        if overlay is None:
            return
        c = self.controller
        if c.closed:
            self._hide_overlay()
            return
        target = self._overlay_target()
        if target:
            self._cancel_overlay_timer()
            self._overlay_set(*target)
        elif overlay.state in ('recording', 'transcribing'):
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
            self._overlay_flash('info', 'Dogspeak is ready', f'{SHORTCUT_LABEL} to record', 2500)

    def _overlay_set(self, state, text, detail, hint):
        overlay = self.overlay
        overlay.set_visible(True)
        overlay.set_text(text)
        overlay.state, overlay.detail, overlay.hint = state, detail, hint
        self._publish_overlay()

    def _overlay_flash(self, state, text, hint, milliseconds):
        if getattr(self, 'overlay', None) is None:
            return
        self._cancel_overlay_timer()
        self._overlay_set(state, text, '', hint)
        self._overlay_hide = GLib.timeout_add(milliseconds, self._hide_overlay)

    def _cancel_overlay_timer(self):
        if self._overlay_hide:
            GLib.source_remove(self._overlay_hide)
            self._overlay_hide = 0

    def _hide_overlay(self):
        self._overlay_hide = 0
        overlay = getattr(self, 'overlay', None)
        if overlay is not None:
            overlay.set_visible(False)
            self._publish_overlay()
        return False

    def _publish_overlay(self):
        overlay = self.overlay
        visible = overlay.get_visible()
        shown = (visible, overlay.state, overlay.get_text(), overlay.detail, overlay.hint)
        keys = {'recording': ('stop', 'cancel'), 'transcribing': ('cancel',)}.get(
            overlay.state, ()) if visible else ()
        if self.shell.available:
            if shown != self._overlay_sent:
                if visible:
                    title = f'{overlay.get_text()}  {overlay.detail}'.strip()
                    self.shell.show(overlay.state, title, overlay.hint)
                else:
                    self.shell.hide()
            if keys != self._overlay_keys:
                self.shell.set_keys(keys)
            self._overlay_sent, self._overlay_keys = shown, keys
            return
        # Banner fallback: no ticking timer, or GNOME would re-announce every second.
        noted = shown[:3] + shown[4:]
        if noted != self._overlay_noted:
            self._overlay_noted = noted
            if visible:
                self.overlay_notify(overlay.get_text(), overlay.hint,
                                    overlay.state in ('recording', 'transcribing'))
            else:
                self.overlay_notify('', '', False)

    def _shell_changed(self):
        # The extension appeared or went away: resend everything to whichever is live now.
        self._overlay_sent = self._overlay_keys = None
        if self.shell.available and self._overlay_noted and self._overlay_noted[0]:
            self.overlay_notify('', '', False)
        self._overlay_noted = None
        if getattr(self, 'overlay', None) is not None:
            self._publish_overlay()

    def _overlay_key(self, key):
        c = self.controller
        if key == 'stop' and c.recording:
            self.perform(c.stop_recording)
        elif key == 'cancel':
            self.perform(c.cancel_recording)
        self.render_status()

    def _copy_transcript(self, text):
        if self.shell.available:
            self._copied = self.shell.set_clipboard(text)
        else:
            # Best effort: Wayland may ignore a selection set by an unfocused client.
            self.get_clipboard().set(text)
            self._copied = False

    def _destroy_overlay(self, *_):
        if getattr(self, 'overlay', None) is None:
            return
        self._cancel_overlay_timer()
        self.overlay_notify('', '', False)
        self.shell.close()
        self.overlay = None

    def quit_session(self):
        if self.closing:
            return
        self._pause_window()
        if not self.controller.close(done=self._destroyed):
            self._resume_window()
            self._show_save_error()

    def _close_requested(self, *_):
        if self.closing:
            return True
        try:
            self.controller.flush()
        except Exception as exc:
            self.controller.status(
                'error',
                f'Could not save draft: {exc}. Draft kept open; retry saving or copy the text before discarding.')
            self._show_save_error()
            return True
        self.set_visible(False)
        return True


class DogspeakApplication(Adw.Application):
    def __init__(self, controller_factory=None, sources_provider=None, cleanup_factory=None,
                 **kwargs):
        kwargs.setdefault('flags', Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        super().__init__(application_id=APP_ID, **kwargs)
        self.window = None
        self.controller_factory = controller_factory or self._make_controller
        self.sources_provider = sources_provider
        self.cleanup_factory = cleanup_factory
        self.add_main_option('toggle-recording', 0, GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE, 'Toggle recording in the running scratchpad', None)
        self.add_main_option('import-audio', 0, GLib.OptionFlags.NONE,
                             GLib.OptionArg.FILENAME, 'Transcribe an audio file into this draft', 'FILE')

    def _make_controller(self):
        from .audio import Recorder
        from .engine import EngineClient
        from .storage import Store
        data = Path(os.environ.get('DOGSPEAK_DATA_DIR') or
                    str(Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'dogspeak'))
        data.mkdir(parents=True, mode=0o700, exist_ok=True)
        data.chmod(0o700)
        return Controller(Store(data / 'dogspeak.sqlite3'), EngineClient, Recorder(), data)

    def do_startup(self):
        Adw.Application.do_startup(self)
        if GLib.get_application_name() != 'Dogspeak':
            GLib.set_application_name('Dogspeak')
        # Run from the checkout, the icon is found here; installed, in the user theme.
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.IconTheme.get_for_display(display).add_search_path(str(ROOT / 'data' / 'icons'))
        Gtk.Window.set_default_icon_name(APP_ID)
        self.hold()
        for name, parameter, callback in [
            ('toggle-recording', None, lambda: self.window.toggle_recording()),
            ('new-draft', None, lambda: self.window.controller.new_draft()),
            ('open-draft', None, lambda: self.window.show_drafts()),
            ('copy-all', None, lambda: self.window.copy_all()),
            ('quit', None, lambda: self.window.quit_session()),
            ('clean-up', None, lambda: self.window.cleanup.start()),
            ('settings', None, lambda: self.window.show_settings()),
            ('show-window', None, lambda: self.window.present()),
        ]:
            action = Gio.SimpleAction.new(name, parameter)
            action.connect('activate', lambda _a, _p, cb=callback: self._action(cb))
            self.add_action(action)
        action = Gio.SimpleAction.new('import-audio', GLib.VariantType.new('s'))
        action.connect('activate', lambda _a, path: self._action(
            lambda: self.window.controller.import_audio(path.get_string())))
        self.add_action(action)
        for name, accelerators in {
            'toggle-recording': ['<Super>r', '<Control><Shift>r'], 'new-draft': ['<Control>n'],
            'open-draft': ['<Control>o'], 'copy-all': ['<Control><Shift>c'],
            'quit': ['<Control>q'], 'clean-up': ['<Control><Shift>l'],
            'settings': ['<Control>comma'],
        }.items():
            self.set_accels_for_action('app.' + name, accelerators)

    def _action(self, callback):
        if self.window is None:
            self._ensure_window()
        self.window.perform(callback)

    def _ensure_window(self):
        if self.window is None:
            sources = self.sources_provider
            if sources is None:
                from .audio import list_sources
                sources = list_sources
            controller = self.controller_factory()
            cleanup = self.cleanup_factory(controller) if self.cleanup_factory else None
            self.window = ScratchpadWindow(self, controller, sources, cleanup)
        return self.window

    def do_activate(self):
        self._ensure_window().present()

    def do_command_line(self, command_line):
        self._ensure_window()
        options = command_line.get_options_dict()
        if options.contains('toggle-recording'):
            self.activate_action('toggle-recording', None)
        else:
            self.do_activate()
        if options.contains('import-audio'):
            raw = options.lookup_value('import-audio', None).unpack()
            # G_OPTION_ARG_FILENAME is an ay variant including its C terminator.
            path = os.fsdecode(bytes(raw) if not isinstance(raw, (str, bytes)) else raw).removesuffix('\0')
            # Relative paths belong to the invoking process, not the primary instance.
            path = command_line.create_file_for_arg(path).get_path()
            self.activate_action('import-audio', GLib.Variant('s', path))
        return 0

    def do_shutdown(self):
        window = self.window
        if window is not None:
            controller = window.controller
            try:
                controller.flush()
            except Exception:
                pass
            if not controller.closed:
                controller.spawn = lambda fn: fn()
                controller.dispatch = lambda fn, *args: fn(*args)
                controller.close()
            else:
                try:
                    controller.engine.close()
                except Exception:
                    pass
        Adw.Application.do_shutdown(self)


def main(argv=None):
    return DogspeakApplication().run(sys.argv if argv is None else argv)


if __name__ == '__main__':
    raise SystemExit(main())
