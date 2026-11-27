"""Native, local Dogspeak scratchpad for Linux (GNOME). Run with system Python.

Field Recorder + ON AIR Focused Instrument layout: slim header with the
puppy-behind-the-microphone mark, eye-level Hero Instrument Strip with a 1:55
tape meter, generous numbered-line draft sheet, and a bottom Instrument Dock
that fits cleanly down to 440 px wide.
"""
from pathlib import Path
import os
import sys
import time

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, GtkSource  # noqa: E402

from .controller import Controller as _Controller, MAX_IMPORT_BYTES, background, move_lines  # noqa: E402,F401
from .paths import data_dir as _data_dir  # noqa: E402
from .ui_values import (  # noqa: E402
    CLIPBOARD_TIMEOUT_MS,
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    EDITOR_MAX_WIDTH,
    FEEDBACK_SECONDS,
    MAX_RECORDING_LABEL,
    MAX_RECORDING_SECONDS,
    MIN_WINDOW_WIDTH,
    NEAR_CAP_SECONDS,
    PALETTE_DARK,
    PALETTE_LIGHT,
    RADIUS_CARD,
    RADIUS_CONTROL,
    RADIUS_STRIP,
    format_cap_readout,
    format_duration,
)

ROOT = Path(__file__).resolve().parent.parent
APP_ID = 'io.github.Remco28.Dogspeak'
LOGO_PATH = ROOT / 'data' / 'icons' / 'hicolor' / '128x128' / 'apps' / f'{APP_ID}.png'
# The global binding desktop_install.py registers; also an in-app accelerator.
SHORTCUT_LABEL = 'Super+R'


OVERLAY_BUS = 'io.github.Remco28.Dogspeak.Overlay'
OVERLAY_PATH = '/io/github/Remco28/Dogspeak/Overlay'
OVERLAY_IFACE = 'io.github.Remco28.Dogspeak.Overlay'

_HERO_STATES = ('loading', 'ready', 'recording', 'nearcap', 'transcribing', 'llm', 'error')


def _gtk_css(dark=False):
    """Field Recorder + ON AIR stylesheet for GTK4 / libadwaita."""
    p = PALETTE_DARK if dark else PALETTE_LIGHT
    return f"""
    window.dogspeak-window {{
        background-color: {p['canvas']};
        color: {p['ink']};
    }}
    headerbar.dogspeak-header {{
        background-color: {p['surface']};
        color: {p['ink']};
        border-bottom: 1px solid {p['border']};
        box-shadow: none;
    }}
    .dogspeak-canvas {{
        background-color: {p['canvas']};
        color: {p['ink']};
    }}
    textview.dogspeak-editor,
    textview.dogspeak-editor text {{
        background-color: {p['canvas']};
        color: {p['ink']};
    }}
    textview.dogspeak-editor border.left {{
        background-color: {p['canvas']};
        color: {p['muted']};
        font-family: monospace;
    }}

    /* Eye-level Hero Instrument Strip */
    .dogspeak-hero {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_STRIP}px;
        padding: 8px 12px;
    }}
    .dogspeak-hero.state-loading {{
        background-color: {p['warn_soft']};
        border-color: {p['warn']};
    }}
    .dogspeak-hero.state-recording {{
        background-color: {p['accent_soft']};
        border-color: {p['accent']};
    }}
    .dogspeak-hero.state-nearcap {{
        background-color: {p['warn_soft']};
        border-color: {p['warn']};
    }}
    .dogspeak-hero.state-transcribing {{
        background-color: {p['local_soft']};
        border-color: {p['local']};
    }}
    .dogspeak-hero.state-llm {{
        background-color: {p['ext_soft']};
        border-color: {p['ext']};
    }}
    .dogspeak-hero.state-error {{
        background-color: {p['danger_soft']};
        border-color: {p['danger']};
    }}

    .dogspeak-lamp {{
        font-family: monospace;
        font-size: 9pt;
        font-weight: 700;
        padding: 2px 7px;
        border-radius: {RADIUS_CONTROL}px;
        background-color: {p['local_soft']};
        color: {p['local']};
        border: 1px solid {p['local']};
    }}
    .dogspeak-lamp.state-loading {{
        background-color: {p['warn']};
        color: {p['warn_ink']};
        border-color: {p['warn']};
    }}
    .dogspeak-lamp.state-recording {{
        background-color: {p['accent']};
        color: {p['accent_ink']};
        border-color: {p['accent']};
    }}
    .dogspeak-lamp.state-nearcap {{
        background-color: {p['warn']};
        color: {p['warn_ink']};
        border-color: {p['warn']};
    }}
    .dogspeak-lamp.state-transcribing {{
        background-color: {p['local']};
        color: {p['accent_ink']};
        border-color: {p['local']};
    }}
    .dogspeak-lamp.state-llm {{
        background-color: {p['ext']};
        color: {p['ext_ink']};
        border-color: {p['ext']};
    }}
    .dogspeak-lamp.state-error {{
        background-color: {p['danger']};
        color: {p['accent_ink']};
        border-color: {p['danger']};
    }}

    .dogspeak-hero-status {{
        font-weight: 600;
        color: {p['ink']};
    }}
    .dogspeak-hero-status.error {{
        color: {p['danger']};
    }}
    .dogspeak-timer {{
        font-family: monospace;
        font-weight: 700;
        font-feature-settings: "tnum";
        color: {p['ink']};
    }}
    .dogspeak-hint {{
        color: {p['muted']};
        font-size: 9pt;
    }}

    /* Tape meter (1:55 recording cap & busy indicator) */
    progressbar.dogspeak-meter trough {{
        min-height: 6px;
        border-radius: 3px;
        background-color: {p['border']};
    }}
    progressbar.dogspeak-meter progress {{
        min-height: 6px;
        border-radius: 3px;
        background-color: {p['accent']};
    }}
    progressbar.dogspeak-meter.state-loading progress,
    progressbar.dogspeak-meter.state-nearcap progress {{
        background-color: {p['warn']};
    }}
    progressbar.dogspeak-meter.state-transcribing progress {{
        background-color: {p['local']};
    }}
    progressbar.dogspeak-meter.state-llm progress {{
        background-color: {p['ext']};
    }}

    /* Bottom Field Recorder Instrument Dock */
    .dogspeak-dock {{
        background-color: {p['surface']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_CARD}px;
        padding: 8px;
    }}
    button.dogspeak-record-onair {{
        background-color: {p['accent']};
        color: {p['accent_ink']};
        border: 1px solid {p['accent']};
        border-radius: {RADIUS_CONTROL}px;
    }}
    button.dogspeak-record-onair:hover {{
        background-color: {p['accent_hover']};
    }}
    button.dogspeak-clean-btn {{
        background-color: {p['ext']};
        color: {p['ext_ink']};
        border: 1px solid {p['ext']};
        border-radius: {RADIUS_CONTROL}px;
    }}
    button.dogspeak-clean-btn:hover {{
        background-color: {p['ext_hover']};
    }}
    button.dogspeak-copy-clear {{
        background-color: {p['ink']};
        color: {p['canvas']};
        border: 1px solid {p['ink']};
        border-radius: {RADIUS_CONTROL}px;
    }}
    button.dogspeak-copy-clear:disabled,
    button.dogspeak-clean-btn:disabled {{
        background-color: {p['surface']};
        color: {p['muted']};
        border-color: {p['border']};
    }}

    /* Suggestions rail */
    .dogspeak-sidebar {{
        background-color: {p['surface']};
    }}
    .dogspeak-card {{
        background-color: {p['elevated']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_CARD}px;
        box-shadow: none;
    }}
    """


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


class Controller(_Controller):
    """The shared Controller, dispatching through GLib's main loop."""

    def __init__(self, store, engine_factory, recorder, data_dir,
                 dispatch=GLib.idle_add, **kwargs):
        kwargs.setdefault('shortcut_label', SHORTCUT_LABEL)
        super().__init__(store, engine_factory, recorder, data_dir, dispatch, **kwargs)


def _make_logo_image(pixel_size):
    if LOGO_PATH.exists():
        img = Gtk.Image.new_from_file(str(LOGO_PATH))
    else:
        img = Gtk.Image.new_from_icon_name(APP_ID)
    img.set_pixel_size(pixel_size)
    return img


class ScratchpadWindow(Adw.ApplicationWindow):
    def __init__(self, app, controller, sources_provider, cleanup=None):
        super().__init__(application=app, title='Dogspeak',
                         default_width=DEFAULT_WINDOW_WIDTH,
                         default_height=DEFAULT_WINDOW_HEIGHT)
        self.controller = controller
        self.rendering, self.save_source, self.closing = False, 0, False
        self.copy_clear_request = self.clear_toast = None
        self._hero_state = None
        self.set_size_request(MIN_WINDOW_WIDTH, -1)
        self.add_css_class('dogspeak-window')
        self.last_edit, self.typing_pause = float('-inf'), 1.0
        self.drafts_dialog = self.save_error_dialog = self.settings_dialog = None
        if cleanup is None:
            from .cleanup import Cleanup
            from .credentials import Keyring
            cleanup = Cleanup(controller, Keyring())
        from .cleanup_ui import CleanupView
        self.cleanup = cleanup
        self.cleanup_view = CleanupView(self, cleanup)
        self.displayed_draft = None

        self._css_provider = Gtk.CssProvider()
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, self._css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._load_css()

        layout = Adw.ToolbarView()
        self.toasts = Adw.ToastOverlay(child=layout)
        self.set_content(self.toasts)

        # Slim Field Recorder header with the puppy-at-the-microphone mark
        header = Adw.HeaderBar()
        header.add_css_class('dogspeak-header')
        header.pack_start(_make_logo_image(24))
        for action, icon, tooltip in [
            ('new-draft', 'document-new-symbolic', 'New draft (Ctrl+N)'),
            ('open-draft', 'document-open-symbolic', 'Open a draft (Ctrl+O)'),
        ]:
            header.pack_start(Gtk.Button(icon_name=icon, action_name='app.' + action,
                                         tooltip_text=tooltip))
        self.title_entry = Gtk.Entry(placeholder_text='Draft title', width_chars=16,
                                     max_width_chars=30, hexpand=True)
        self.title_entry.set_tooltip_text('Draft title')
        header.set_title_widget(self.title_entry)

        line_menu = Gio.Menu()
        line_menu.append('Move Line Up', 'app.move-line-up')
        line_menu.append('Move Line Down', 'app.move-line-down')
        menu = Gio.Menu()
        transfer_menu = Gio.Menu()
        transfer_menu.append('Copy All', 'app.copy-all')
        transfer_menu.append('Copy & Clear', 'app.copy-clear')
        transfer_menu.append('Clear', 'app.clear')
        menu.append_section(None, transfer_menu)
        menu.append_section(None, line_menu)
        menu.append('Settings', 'app.settings')
        menu.append('Quit', 'app.quit')
        header.pack_end(Gtk.MenuButton(icon_name='open-menu-symbolic', menu_model=menu,
                                       tooltip_text='Main menu', primary=True))
        header.pack_end(self.cleanup_view.panel_button)
        self.sources = [None]
        self.updating_sources = False
        self.microphone = Gtk.DropDown(model=Gtk.StringList.new(['System default']))
        self.microphone.set_tooltip_text('Microphone')
        self.microphone.set_size_request(125, -1)
        self.microphone.connect('notify::selected', self._microphone_changed)
        header.pack_end(self.microphone)
        layout.add_top_bar(header)

        # Focused Instrument stage (Hero Strip + Banner + Editor + Bottom Dock)
        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        stage.add_css_class('dogspeak-canvas')

        # Eye-level Hero Instrument Strip
        self.hero_strip = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                                  margin_start=12, margin_end=12, margin_top=12, margin_bottom=4)
        self.hero_strip.add_css_class('dogspeak-hero')
        hero_top = Gtk.Box(spacing=8)
        self.hero_mascot = _make_logo_image(26)
        self.hero_mascot.set_visible(False)
        hero_top.append(self.hero_mascot)
        self.state_lamp = Gtk.Label(label='WARMING UP')
        self.state_lamp.add_css_class('dogspeak-lamp')
        hero_top.append(self.state_lamp)
        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)
        hero_top.append(self.spinner)
        self.status_label = Gtk.Label(xalign=0, hexpand=True, wrap=True, selectable=True)
        self.status_label.add_css_class('dogspeak-hero-status')
        hero_top.append(self.status_label)
        self.hero_hint = Gtk.Label(label='')
        self.hero_hint.add_css_class('dogspeak-hint')
        self.hero_hint.set_visible(False)
        hero_top.append(self.hero_hint)
        self.timer_label = Gtk.Label(label='')
        self.timer_label.add_css_class('dogspeak-timer')
        hero_top.append(self.timer_label)
        self.hero_strip.append(hero_top)

        self.tape_meter = Gtk.ProgressBar()
        self.tape_meter.add_css_class('dogspeak-meter')
        self.tape_meter.set_pulse_step(0.18)
        self.tape_meter.set_visible(False)
        self.hero_strip.append(self.tape_meter)

        hero_clamp = Adw.Clamp(maximum_size=EDITOR_MAX_WIDTH + 60, child=self.hero_strip)
        stage.append(hero_clamp)
        stage.append(self.cleanup_view.banner)

        # Draft sheet (keep editor direct child of ScrolledWindow)
        self.buffer = self._new_buffer()
        self.buffers = {}
        self.editor = GtkSource.View(buffer=self.buffer, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                     left_margin=20, right_margin=32, top_margin=22,
                                     bottom_margin=22, pixels_above_lines=3,
                                     pixels_below_lines=3, vexpand=True,
                                     show_line_numbers=True, highlight_current_line=True)
        self.editor.add_css_class('view')
        self.editor.add_css_class('dogspeak-editor')
        self.editor.set_extra_menu(line_menu)
        self.editor.connect('move-lines', self._move_lines)
        Adw.StyleManager.get_default().connect('notify::dark', self._theme_changed)
        # Scrolling does nothing until the view is laid out, so also do it when shown.
        self.editor.connect('map', lambda *_: GLib.idle_add(self._show_cursor))
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.editor)
        scroll.set_hexpand(True)
        stage.append(scroll)

        # Bottom Field Recorder Instrument Dock (fits cleanly down to 440 px wide)
        dock = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                       margin_start=12, margin_end=12, margin_top=6, margin_bottom=12)
        dock.add_css_class('dogspeak-dock')
        row1 = Gtk.Box(spacing=8)
        self.record_button = Gtk.Button(action_name='app.toggle-recording',
                                        tooltip_text=f'Record / stop ({SHORTCUT_LABEL})')
        record_box = Gtk.Box(spacing=6)
        self.record_icon = Gtk.Image(icon_name='media-record-symbolic')
        self.record_label = Gtk.Label(label='Record')
        record_box.append(self.record_icon)
        record_box.append(self.record_label)
        self.record_button.set_child(record_box)
        row1.append(self.record_button)
        row1.append(Gtk.Box(hexpand=True))
        self.copy_clear_button = Gtk.Button(label='Copy & Clear', action_name='app.copy-clear',
                                            tooltip_text='Copy all text and clear this draft (Ctrl+Shift+X)')
        self.copy_clear_button.add_css_class('dogspeak-copy-clear')
        row1.append(self.copy_clear_button)
        row1.append(Gtk.Button(icon_name='edit-copy-symbolic', action_name='app.copy-all',
                               tooltip_text='Copy all (Ctrl+Shift+C)'))
        dock.append(row1)
        dock.append(self.cleanup_view.bar)
        dock_clamp = Adw.Clamp(maximum_size=EDITOR_MAX_WIDTH + 60, child=dock)
        stage.append(dock_clamp)

        content = Gtk.Box()
        content.append(stage)
        content.append(self.cleanup_view.panel)
        layout.set_content(content)

        self.buffer.connect('changed', self._changed)
        self.title_entry.connect('changed', self._changed)
        self.controller.on_draft = self.render_draft
        self.controller.on_status = self.render_status
        self.controller.insertion_point = self._insertion_point
        self.controller.hold_result = self._hold_result
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

    def _load_css(self):
        dark = Adw.StyleManager.get_default().get_dark()
        css = _gtk_css(dark)
        if hasattr(self._css_provider, 'load_from_string'):
            self._css_provider.load_from_string(css)
        else:
            self._css_provider.load_from_data(css.encode('utf-8'))

    def _scheme(self):
        dark = Adw.StyleManager.get_default().get_dark()
        return GtkSource.StyleSchemeManager.get_default().get_scheme(
            'Adwaita-dark' if dark else 'Adwaita')

    def _new_buffer(self):
        buffer = GtkSource.Buffer(highlight_matching_brackets=False,
                                  style_scheme=self._scheme())
        buffer.set_enable_undo(True)
        return buffer

    def _theme_changed(self, *_):
        self._load_css()
        for buffer in self.buffers.values():
            buffer.set_style_scheme(self._scheme())

    def _show_cursor(self):
        self.editor.scroll_mark_onscreen(self.buffer.get_insert())
        return False

    def _insertion_point(self):
        # With the window closed the cursor can't be seen, so it is no choice of the user's.
        # Inside a word, the text goes after the word.
        if not self.get_visible():
            return None
        _start, end = self.buffer.get_selection_bounds() or (None, None)
        if end is None:
            end = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        if end.inside_word() and not end.starts_word():
            end.forward_word_end()
        return end.get_offset()

    def _hold_result(self, event):
        wait = self.typing_pause - (self.controller.clock() - self.last_edit)
        if wait <= 0:
            return False
        GLib.timeout_add(int(wait * 1000) + 1, self.controller._event, event)
        return True

    def move_line(self, down):
        self.editor.emit('move-lines', down)

    def _move_lines(self, editor, down):
        # Replaces GtkSource's own move, which breaks up blank-line spacing.
        editor.stop_emission_by_name('move-lines')
        if not editor.get_editable():
            return
        buffer = self.buffer
        cursor = buffer.get_iter_at_mark(buffer.get_insert())
        bound = buffer.get_iter_at_mark(buffer.get_selection_bound())
        start, end = sorted((cursor, bound), key=lambda i: i.get_offset())
        last = end.get_line()
        if end.starts_line() and last > start.get_line():
            last -= 1  # a selection ending at a line's start doesn't include that line
        lines = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).split('\n')
        moved = move_lines(lines, start.get_line(), last, down)
        if moved is None:
            return
        new, first, new_first = moved
        marks = [(mark.get_line() - first + new_first, mark.get_line_offset())
                 for mark in (cursor, bound)]
        if new != lines:  # identical neighbours: only the cursor moves
            top = next(i for i, (a, b) in enumerate(zip(lines, new)) if a != b)
            bottom = len(lines) - next(
                i for i, (a, b) in enumerate(zip(reversed(lines), reversed(new))) if a != b)
            buffer.begin_user_action()
            begin = buffer.get_iter_at_line(top)[1]
            stop = buffer.get_iter_at_line(bottom - 1)[1]
            if not stop.ends_line():
                stop.forward_to_line_end()
            buffer.delete(begin, stop)
            buffer.insert(buffer.get_iter_at_line(top)[1], '\n'.join(new[top:bottom]))
            buffer.end_user_action()

        def position(line, column):
            if line >= len(new):  # a selection's end, on the line after the moved text
                return buffer.get_end_iter()
            line = max(0, line)
            return buffer.get_iter_at_line_offset(line, min(column, len(new[line])))[1]
        buffer.select_range(position(*marks[0]), position(*marks[1]))
        self._show_cursor()

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

    def _changed(self, source=None):
        if self.rendering or self.closing:
            return
        self._dismiss_clear_toast()
        if source is not self.title_entry:
            self.last_edit = self.controller.clock()
        self.controller.edit(self.title_entry.get_text(), self.buffer.get_text(
            self.buffer.get_start_iter(), self.buffer.get_end_iter(), True))
        if self.save_source:
            GLib.source_remove(self.save_source)
        self.save_source = GLib.timeout_add(500, self._autosave)
        self._update_clear_actions()

    def _autosave(self):
        self.save_source = 0
        self.perform(self.controller.flush)
        self.cleanup_view.refresh()  # the shown version follows undo and typing
        return False

    def render_draft(self, draft, append=False, at=None):
        switching = self.displayed_draft != draft.id
        self._dismiss_clear_toast()
        if switching and self.copy_clear_request is not None:
            self.copy_clear_request['changed'] = True
        self.displayed_draft = draft.id
        self.rendering = True
        try:
            if draft.id in self.buffers:
                self.buffer = self.buffers[draft.id]
                self.editor.set_buffer(self.buffer)
                append = True
            else:
                if self.buffers:
                    self.buffer = self._new_buffer()
                    self.buffer.connect('changed', self._changed)
                    self.editor.set_buffer(self.buffer)
                self.buffers[draft.id] = self.buffer
            if append:
                old = self.buffer.get_text(self.buffer.get_start_iter(),
                                           self.buffer.get_end_iter(), True)
                start = len(old) if at is None else at
                added = draft.content[start:start + len(draft.content) - len(old)]
                if draft.content != old[:start] + added + old[start:]:
                    raise RuntimeError('Draft changed outside the editor; reopen it to reconcile')
                if added:
                    cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert()).get_offset()
                    bound = self.buffer.get_iter_at_mark(self.buffer.get_selection_bound()).get_offset()
                    self.buffer.begin_user_action()
                    self.buffer.insert(self.buffer.get_iter_at_offset(start), added)
                    self.buffer.end_user_action()
                    if at is not None:
                        # After the words, not their separator: they must stay in view.
                        self.buffer.place_cursor(
                            self.buffer.get_iter_at_offset(start + len(added.rstrip())))
                    elif cursor != bound:
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
            self.buffer.place_cursor(self.buffer.get_end_iter())
        self._show_cursor()
        if switching:
            self.editor.grab_focus()
            if getattr(self, 'cleanup_view', None) is not None:
                self.cleanup_view.refresh()
        self._update_clear_actions()

    def replace_text(self, draft_id, text):
        """Swap a draft's whole text as one undoable step (Ctrl+Z brings it back)."""
        buffer = self.buffers.get(draft_id)
        if buffer is None:
            return
        self._dismiss_clear_toast()
        self.rendering = True
        try:
            buffer.begin_user_action()
            buffer.delete(buffer.get_start_iter(), buffer.get_end_iter())
            buffer.insert(buffer.get_start_iter(), text)
            buffer.end_user_action()
        finally:
            self.rendering = False

    def _set_hero_state(self, state, lamp_text):
        if state != self._hero_state:
            for old in _HERO_STATES:
                cls = f'state-{old}'
                self.hero_strip.remove_css_class(cls)
                self.state_lamp.remove_css_class(cls)
                self.tape_meter.remove_css_class(cls)
            cls = f'state-{state}'
            self.hero_strip.add_css_class(cls)
            self.state_lamp.add_css_class(cls)
            self.tape_meter.add_css_class(cls)
            self._hero_state = state
        if self.state_lamp.get_text() != lamp_text:
            self.state_lamp.set_text(lamp_text)

    def render_status(self):
        c = self.controller
        recording = c.recording
        cleanup = getattr(self, 'cleanup', None)
        started_at = c.recording_job['started_at'] if recording else c.started_at
        elapsed = max(0.0, c.clock() - started_at)
        elapsed_int = int(elapsed)

        message = c.message
        show_mascot = False
        hint = ''
        meter_visible = False
        meter_fraction = None
        spinning = False

        if recording:
            near = elapsed_int >= NEAR_CAP_SECONDS
            left = max(0, MAX_RECORDING_SECONDS - elapsed_int)
            state = 'nearcap' if near else 'recording'
            lamp = f'● ON AIR · {left}s LEFT' if near else '● ON AIR'
            self._set_hero_state(state, lamp)
            timer_text = format_cap_readout(elapsed_int)
            hint = 'Enter stops · Esc cancels'
            meter_visible = True
            meter_fraction = min(1.0, elapsed / MAX_RECORDING_SECONDS)
        elif c.state == 'transcribing':
            self._set_hero_state('transcribing', 'LOCAL CPU')
            timer_text = format_duration(elapsed_int)
            hint = 'Esc drops'
            meter_visible = True
            spinning = True
        elif cleanup is not None and cleanup.busy:
            # Clean up doesn't need the speech model, so it outranks "Loading model…".
            from .llm import provider_name
            cfg = cleanup.busy['config']
            name = provider_name(cfg['base_url'])
            short = name.split(' (', 1)[0].upper()
            is_local_llm = not cleanup.needs_key(cfg['base_url'])
            message = f'Sending this draft to {name}… Esc cancels.'
            lamp = f'LOCAL LLM · {short}' if is_local_llm else f'↗ EXTERNAL · {short}'
            self._set_hero_state('llm', lamp)
            llm_elapsed = int(max(0, time.monotonic() - cleanup.busy.get('started_at', time.monotonic())))
            timer_text = format_duration(llm_elapsed)
            hint = cfg.get('model') or ''
            meter_visible = True
            spinning = True
            show_mascot = True
        elif c.state == 'loading':
            self._set_hero_state('loading', 'WARMING UP')
            timer_text = format_duration(elapsed_int)
            hint = 'You can type while Bark loads'
            meter_visible = True
            spinning = True
            show_mascot = True
        elif c.state == 'error':
            self._set_hero_state('error', 'ERROR')
            timer_text = ''
        else:
            self._set_hero_state('ready', 'STANDBY')
            timer_text = f'{MAX_RECORDING_LABEL} / take'

        self.hero_mascot.set_visible(show_mascot)
        self.status_label.set_text(message)
        self.status_label.set_tooltip_text(message)
        if c.state == 'error' and not (recording or (cleanup is not None and cleanup.busy)):
            self.status_label.add_css_class('error')
        else:
            self.status_label.remove_css_class('error')
        self.timer_label.set_text(timer_text)
        self.hero_hint.set_text(hint)
        self.hero_hint.set_visible(bool(hint))
        self.spinner.set_spinning(spinning)
        self.tape_meter.set_visible(meter_visible)
        if meter_visible:
            if meter_fraction is not None:
                self.tape_meter.set_fraction(meter_fraction)
            else:
                self.tape_meter.pulse()

        self.record_label.set_text('Stop' if recording else 'Record')
        self.record_icon.set_from_icon_name(
            'media-playback-stop-symbolic' if recording else 'media-record-symbolic')
        self.record_button.set_sensitive(not c.closed and (recording or c.engine_failed or (
            c.ready and not c.pending and not c.recording_job)))
        self.microphone.set_sensitive(not recording and not c.recording_job)
        if recording:
            self.record_button.add_css_class('dogspeak-record-onair')
        else:
            self.record_button.remove_css_class('dogspeak-record-onair')
        if cleanup is not None:
            self.cleanup_view.clean_button.set_sensitive(cleanup.can_start())
        self._sync_overlay()
        self._update_clear_actions()

    def _can_clear(self):
        c = self.controller
        return not (self.closing or c.closed or c.recording_job or c.pending
                    or self.cleanup.busy or self.copy_clear_request) and bool(self.buffer.get_char_count())

    def _update_clear_actions(self):
        app = self.get_application()
        if app is not None:
            for name in ('copy-clear', 'clear'):
                app.lookup_action(name).set_enabled(self._can_clear())

    def _dismiss_clear_toast(self):
        if self.clear_toast is not None:
            self.clear_toast.dismiss()
            self.clear_toast = None

    def _feedback(self, message):
        self.toasts.add_toast(Adw.Toast(title=message, timeout=FEEDBACK_SECONDS))

    def clear_text(self, copied=False):
        if not self._can_clear():
            return
        buffer = self.buffer
        buffer.begin_user_action()
        buffer.delete(buffer.get_start_iter(), buffer.get_end_iter())
        buffer.end_user_action()
        self.editor.grab_focus()
        self.cleanup_view.refresh()
        toast = Adw.Toast(title='Copied and cleared' if copied else 'Cleared',
                          button_label='Undo', timeout=FEEDBACK_SECONDS)
        self.clear_toast = toast

        def undo(*_):
            if self.clear_toast is toast and self.buffer is buffer and buffer.get_can_undo():
                buffer.undo()
                self.editor.grab_focus()
        toast.connect('button-clicked', undo)
        self.toasts.add_toast(toast)

    def copy_and_clear(self):
        if not self._can_clear():
            return
        buffer = self.buffer
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        request = {'buffer': buffer, 'draft': self.displayed_draft, 'text': text,
                   'changed': False, 'cancel': Gio.Cancellable(), 'timeout': 0}
        request['handler'] = buffer.connect('changed', lambda *_: request.update(changed=True))
        self.copy_clear_request = request
        self._update_clear_actions()

        def finished(clipboard, result):
            try:
                copied = clipboard.read_text_finish(result)
                error = None if copied == text else 'Clipboard could not be verified. Text kept.'
            except Exception:
                error = 'Could not read the clipboard. Text kept.'
            self._finish_copy_clear(request, error)

        def timed_out():
            request['timeout'] = 0
            self._finish_copy_clear(request, 'Clipboard check timed out. Text kept.')
            request['cancel'].cancel()
            return False

        try:
            self.copy_all()
            request['timeout'] = GLib.timeout_add(CLIPBOARD_TIMEOUT_MS, timed_out)
            self.get_clipboard().read_text_async(request['cancel'], finished)
        except Exception:
            self._finish_copy_clear(request, 'Could not copy the text. Text kept.')

    def _finish_copy_clear(self, request, error):
        if self.copy_clear_request is not request:
            return
        self.copy_clear_request = None
        request['buffer'].disconnect(request['handler'])
        if request['timeout']:
            GLib.source_remove(request['timeout'])
        self._update_clear_actions()
        if self.closing or self.controller.closed:
            return
        if error:
            self._feedback(error)
        elif (request['changed'] or not self.get_visible() or self.buffer is not request['buffer']
              or self.displayed_draft != request['draft'] or not self._can_clear()):
            self._feedback('Draft changed or busy. Text kept; copy again when ready.')
        else:
            self.clear_text(copied=True)

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
        if self.copy_clear_request is not None:
            request = self.copy_clear_request
            self._finish_copy_clear(request, None)
            request['cancel'].cancel()
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
            left = max(0, MAX_RECORDING_SECONDS - elapsed)
            detail = format_cap_readout(elapsed) + (f' · {left}s left' if elapsed >= NEAR_CAP_SECONDS else '')
            return 'recording', 'Recording', detail, self._stop_hint()
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
        data = _data_dir()
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
            ('copy-clear', None, lambda: self.window.copy_and_clear()),
            ('clear', None, lambda: self.window.clear_text()),
            ('quit', None, lambda: self.window.quit_session()),
            ('clean-up', None, lambda: self.window.cleanup.start()),
            ('settings', None, lambda: self.window.show_settings()),
            ('show-window', None, lambda: self.window.present()),
            ('move-line-up', None, lambda: self.window.move_line(False)),
            ('move-line-down', None, lambda: self.window.move_line(True)),
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
            'copy-clear': ['<Control><Shift>x'],
            'quit': ['<Control>q'], 'clean-up': ['<Control><Shift>l'],
            'settings': ['<Control>comma'], 'move-line-up': ['<Alt>Up'],
            'move-line-down': ['<Alt>Down'],
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
