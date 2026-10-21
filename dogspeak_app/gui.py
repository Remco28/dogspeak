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
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
APP_ID = 'io.github.Remco28.Dogspeak'
MAX_IMPORT_BYTES = 256 * 1024 * 1024


def background(fn):
    thread = threading.Thread(target=fn, daemon=True)
    thread.start()
    return thread


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
        self.on_draft = lambda draft, append=False: None
        self.on_status = lambda: None
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
        self.engine = engine_factory(self.receive)
        self.engine.start()

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
            self.status('ready', 'Ready')
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
                if draft.id == self.draft.id:
                    self.draft = draft
                    self.title, self.content = draft.title, draft.content
                    self.on_draft(draft, append=True)
                self.pending.pop(event['id'])
                self.status('ready', 'Ready' if event.get('text') else 'No speech detected')
            except Exception as exc:
                self.status('error', f'Could not save transcription: {exc}')
        elif kind == 'error':
            self.pending.pop(event.get('id'), None)
            if event.get('fatal'):
                self.ready = False
                self.pending.clear()
                self.recording_job = None
                self.spawn(self._cancel_capture)
            self.status('error', event.get('message', 'Transcription failed'))
        return False

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
        if not self.ready or self.pending or self.recording_job:
            return False
        request_id = uuid.uuid4().hex
        path = self.data_dir / 'recordings' / (request_id + '.wav')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.recorder.start(path, target=self.source)
            self.recording_job = {
                'id': request_id, 'draft_id': self.draft.id, 'path': path,
                'submitted': False, 'started_at': self.clock()}
            self.status('recording', 'Recording')
            return True
        except Exception as exc:
            self.recorder.cancel()
            self.status('error', f'Could not record: {exc}')
            return False

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
        self.pending.clear()
        self.recording_job = None
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
    def __init__(self, app, controller, sources_provider):
        super().__init__(application=app, title='Dogspeak', default_width=900,
                         default_height=680)
        self.controller = controller
        self.rendering, self.save_source, self.closing = False, 0, False
        self.drafts_dialog = self.save_error_dialog = None
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
                                        tooltip_text='Record / stop (Ctrl+Shift+R)')
        record_box = Gtk.Box(spacing=6)
        self.record_icon = Gtk.Image(icon_name='media-record-symbolic')
        self.record_label = Gtk.Label(label='Record')
        record_box.append(self.record_icon)
        record_box.append(self.record_label)
        self.record_button.set_child(record_box)
        header.pack_end(self.record_button)
        header.pack_end(Gtk.Button(icon_name='edit-copy-symbolic', action_name='app.copy-all',
                                   tooltip_text='Copy all (Ctrl+Shift+C)'))
        layout.add_top_bar(header)
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
        layout.set_content(scroll)
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
        self.render_draft(controller.draft)
        self.render_status()
        self.tick_source = GLib.timeout_add(200, controller.tick)
        self.connect('close-request', self._close_requested)
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
        self.record_button.set_sensitive(not c.closed and (recording or (
            c.ready and not c.pending and not c.recording_job)))
        self.microphone.set_sensitive(not recording and not c.recording_job)
        if recording:
            self.record_button.add_css_class('destructive-action')
        else:
            self.record_button.remove_css_class('destructive-action')

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
            rows.append(row)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(rows)
        box.set_content(scroll)
        dialog.set_child(box)
        dialog.present(self)

    def copy_all(self):
        self.get_clipboard().set(self.buffer.get_text(
            self.buffer.get_start_iter(), self.buffer.get_end_iter(), True))

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
            self.controller.discard(done=self.destroy)

    def _close_requested(self, *_):
        if self.closing:
            return True
        self._pause_window()
        if not self.controller.close(done=self.destroy):
            self._resume_window()
            self._show_save_error()
        return True


class DogspeakApplication(Adw.Application):
    def __init__(self, controller_factory=None, sources_provider=None, **kwargs):
        kwargs.setdefault('flags', Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        super().__init__(application_id=APP_ID, **kwargs)
        self.window = None
        self.controller_factory = controller_factory or self._make_controller
        self.sources_provider = sources_provider
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
        for name, parameter, callback in [
            ('toggle-recording', None, lambda: self.window.controller.toggle_recording()),
            ('new-draft', None, lambda: self.window.controller.new_draft()),
            ('open-draft', None, lambda: self.window.show_drafts()),
            ('copy-all', None, lambda: self.window.copy_all()),
        ]:
            action = Gio.SimpleAction.new(name, parameter)
            action.connect('activate', lambda _a, _p, cb=callback: self._action(cb))
            self.add_action(action)
        action = Gio.SimpleAction.new('import-audio', GLib.VariantType.new('s'))
        action.connect('activate', lambda _a, path: self._action(
            lambda: self.window.controller.import_audio(path.get_string())))
        self.add_action(action)
        for name, accelerators in {
            'toggle-recording': ['<Control><Shift>r'], 'new-draft': ['<Control>n'],
            'open-draft': ['<Control>o'], 'copy-all': ['<Control><Shift>c'],
        }.items():
            self.set_accels_for_action('app.' + name, accelerators)

    def _action(self, callback):
        self.do_activate()
        self.window.perform(callback)

    def do_activate(self):
        if self.window is None:
            sources = self.sources_provider
            if sources is None:
                from .audio import list_sources
                sources = list_sources
            self.window = ScratchpadWindow(self, self.controller_factory(), sources)
        self.window.present()

    def do_command_line(self, command_line):
        self.do_activate()
        options = command_line.get_options_dict()
        if options.contains('toggle-recording'):
            self.activate_action('toggle-recording', None)
        if options.contains('import-audio'):
            raw = options.lookup_value('import-audio', None).unpack()
            # G_OPTION_ARG_FILENAME is an ay variant including its C terminator.
            path = os.fsdecode(bytes(raw) if not isinstance(raw, (str, bytes)) else raw).removesuffix('\0')
            # Relative paths belong to the invoking process, not the primary instance.
            path = command_line.create_file_for_arg(path).get_path()
            self.activate_action('import-audio', GLib.Variant('s', path))
        return 0


def main(argv=None):
    return DogspeakApplication().run(sys.argv if argv is None else argv)


if __name__ == '__main__':
    raise SystemExit(main())
