"""Clean up widgets: the purpose bar, the banner, the suggestions panel, Settings."""
from datetime import datetime

from gi.repository import Adw, GLib, Gtk

from . import llm
from .cleanup import PRESETS


def _time(created_at):
    try:
        return datetime.fromisoformat(created_at).astimezone().strftime('%-I:%M %p')
    except ValueError:
        return ''


def _clear(box):
    child = box.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        box.remove(child)
        child = following


def _menu_item(label, callback, dim=False, subtitle=None):
    button = Gtk.Button(halign=Gtk.Align.FILL)
    button.add_css_class('flat')
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=2, margin_bottom=2)
    title = Gtk.Label(label=label, xalign=0, ellipsize=3, max_width_chars=40)
    if dim:
        title.add_css_class('dim-label')
    box.append(title)
    if subtitle:
        small = Gtk.Label(label=subtitle, xalign=0)
        small.add_css_class('caption')
        small.add_css_class('dim-label')
        box.append(small)
    button.set_child(box)
    button.connect('clicked', lambda _b: callback())
    return button


def _heading(text):
    label = Gtk.Label(label=text, xalign=0, margin_start=10, margin_top=8, margin_bottom=2)
    label.add_css_class('caption-heading')
    label.add_css_class('dim-label')
    return label


class CleanupView:
    """Owns the Clean up widgets of one ScratchpadWindow."""

    def __init__(self, window, cleanup):
        self.window, self.cleanup = window, cleanup
        self.updating = False
        cleanup.on_change = self.refresh
        cleanup.on_replace = window.replace_text

        bar = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=8, margin_bottom=8)
        label = Gtk.Label(label='_Purpose', use_underline=True)
        label.add_css_class('heading')
        bar.append(label)
        purpose = Gtk.Box()
        purpose.add_css_class('linked')
        self.purpose = Gtk.Entry(placeholder_text='General purpose (leave blank)',
                                 hexpand=True, width_chars=24, max_width_chars=48)
        self.purpose.set_text(cleanup.purpose)
        self.purpose.connect('changed', self._purpose_changed)
        self.purpose.connect('icon-release', lambda *_: self.purpose.set_text(''))
        label.set_mnemonic_widget(self.purpose)
        purpose.append(self.purpose)
        self.purpose_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        popover = Gtk.Popover(child=self.purpose_list)
        popover.connect('show', lambda _p: self._fill_purposes())
        self.purpose_menu = Gtk.MenuButton(icon_name='pan-down-symbolic', popover=popover,
                                           tooltip_text='Choose a purpose')
        purpose.append(self.purpose_menu)
        bar.append(purpose)
        self.clean_button = Gtk.Button(label='Clean up', action_name='app.clean-up',
                                       tooltip_text='Clean up this draft (Ctrl+Shift+L)')
        self.clean_button.add_css_class('suggested-action')
        bar.append(self.clean_button)
        bar.append(Gtk.Box(hexpand=True))
        revert = Gtk.Box(spacing=6)
        revert.append(Gtk.Image(icon_name='edit-undo-symbolic'))
        revert.append(Gtk.Label(label='Revert'))
        self.revert_button = Gtk.Button(child=revert, tooltip_text='Go back one version')
        self.revert_button.connect('clicked', lambda _b: window.perform(cleanup.revert))
        bar.append(self.revert_button)
        self.version_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        versions = Gtk.Popover(child=self.version_list)
        versions.connect('show', lambda _p: self._fill_versions())
        self.version_menu = Gtk.MenuButton(label='No versions yet', popover=versions,
                                           tooltip_text='Versions of this draft (last 20)')
        bar.append(self.version_menu)
        self.bar = bar

        self.banner = Adw.Banner()
        self.banner.connect('button-clicked', self._banner_clicked)

        self.panel_button = Gtk.ToggleButton(tooltip_text='Show or hide suggestions')
        panel_box = Gtk.Box(spacing=6)
        panel_box.append(Gtk.Image(icon_name='sidebar-show-right-symbolic'))
        self.panel_count = Gtk.Label(label='0')
        panel_box.append(self.panel_count)
        self.panel_button.set_child(panel_box)
        show = cleanup.store.get_setting('suggestions_panel', '1') != '0'
        self.panel_button.set_active(show)
        self.panel_button.connect('toggled', self._panel_toggled)

        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, width_request=300,
                       margin_start=16, margin_end=16, margin_top=16, margin_bottom=16)
        head = Gtk.Box()
        title = Gtk.Label(label='Suggestions', xalign=0, hexpand=True)
        title.add_css_class('heading')
        head.append(title)
        hide = Gtk.Button(label='Hide')
        hide.add_css_class('flat')
        hide.connect('clicked', lambda _b: self.panel_button.set_active(False))
        head.append(hide)
        side.append(head)
        caption = Gtk.Label(label='From the last clean up. Dismiss what you’ve dealt with.',
                            xalign=0, wrap=True)
        caption.add_css_class('caption')
        caption.add_css_class('dim-label')
        side.append(caption)
        self.suggestion_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        side.append(self.suggestion_list)
        self.suggestion_empty = Gtk.Label(xalign=0, wrap=True)
        self.suggestion_empty.add_css_class('dim-label')
        side.append(self.suggestion_empty)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                    child=side)
        frame = Gtk.Box()
        frame.add_css_class('sidebar-pane')
        frame.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        frame.append(scroll)
        scroll.set_size_request(320, -1)
        self.panel = Gtk.Revealer(child=frame, reveal_child=show,
                                  transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT)
        # Wrapping labels inside would otherwise pull the panel to half the window.
        for widget in (self.panel, frame, scroll, side):
            widget.set_hexpand(False)

    # Purpose
    def _purpose_changed(self, entry):
        text = entry.get_text()
        entry.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY,
                                      'edit-clear-symbolic' if text else None)
        if not self.updating:
            self.cleanup.set_purpose(text)

    def pick_purpose(self, text):
        self.purpose.set_text(text)
        self.cleanup.set_purpose(text, remember=True)
        self.purpose_menu.popdown()

    def _fill_purposes(self):
        _clear(self.purpose_list)
        self.purpose_list.append(_menu_item('General purpose (blank)', lambda: self.pick_purpose(''), dim=True))
        self.purpose_list.append(_heading('Presets'))
        for preset in PRESETS:
            self.purpose_list.append(_menu_item(preset, lambda p=preset: self.pick_purpose(p)))
        recent = self.cleanup.recent_purposes()
        if recent:
            self.purpose_list.append(_heading('Recent'))
            for purpose in recent:
                self.purpose_list.append(_menu_item(purpose, lambda p=purpose: self.pick_purpose(p)))

    # Versions
    def _fill_versions(self):
        _clear(self.version_list)
        versions = self.cleanup.versions()
        current = self.cleanup.current_index()
        self.version_list.append(_heading(f'Versions of this draft (last {20} kept)'))
        if not versions:
            self.version_list.append(_menu_item('Clean up saves a version first', lambda: None, dim=True))
        for index in reversed(range(len(versions))):
            version = versions[index]
            label = f'Version {index + 1}' + ('  (showing)' if index == current else '')
            item = _menu_item(label, lambda i=index: self._show_version(i),
                              subtitle=f'{version["label"]} · {_time(version["created_at"])}')
            self.version_list.append(item)

    def _show_version(self, index):
        self.version_menu.popdown()
        self.window.perform(lambda: self.cleanup.show_version(index))

    # Banner
    def _banner_clicked(self, _banner):
        kind = self.cleanup.problem[0] if self.cleanup.problem else None
        self.cleanup.problem = None
        if kind == 'setup':
            present_settings(self.window, self.cleanup)
        else:
            self.window.get_application().activate_action('clean-up', None)
        self.refresh()

    def _panel_toggled(self, button):
        self.panel.set_reveal_child(button.get_active())
        self.cleanup.store.set_setting('suggestions_panel', '1' if button.get_active() else '0')

    def refresh(self):
        c, cleanup, window = self.window.controller, self.cleanup, self.window
        if c.closed:
            return
        busy_here = bool(cleanup.busy and cleanup.busy['draft_id'] == c.draft.id)
        self.updating = True
        if self.purpose.get_text() != cleanup.purpose:
            self.purpose.set_text(cleanup.purpose)
        self.updating = False
        self.clean_button.set_label('Cleaning up…' if cleanup.busy else 'Clean up')
        self.clean_button.set_sensitive(cleanup.can_start())
        versions = cleanup.versions()
        current = cleanup.current_index()
        self.version_menu.set_label(
            f'Version {current + 1} of {len(versions)}' if versions else 'No versions yet')
        self.revert_button.set_sensitive(bool(current) and not busy_here)
        self.version_menu.set_sensitive(bool(versions) and not busy_here)
        window.editor.set_editable(not busy_here)
        if busy_here:
            window.editor.add_css_class('dim-label')
        else:
            window.editor.remove_css_class('dim-label')
        version_id, suggestions = cleanup.suggestions()
        _clear(self.suggestion_list)
        for index, text in suggestions:
            row = Gtk.Box(spacing=8)
            row.add_css_class('card')
            body = Gtk.Label(label=text, xalign=0, wrap=True, hexpand=True, selectable=True,
                             margin_start=12, margin_top=10, margin_bottom=10)
            row.append(body)
            dismiss = Gtk.Button(icon_name='window-close-symbolic', valign=Gtk.Align.START,
                                 margin_top=4, margin_end=4, tooltip_text='Dismiss suggestion')
            dismiss.add_css_class('flat')
            dismiss.add_css_class('circular')
            dismiss.connect('clicked', lambda _b, v=version_id, i=index: cleanup.dismiss(v, i))
            row.append(dismiss)
            self.suggestion_list.append(row)
        self.panel_count.set_text(str(len(suggestions)))
        self.suggestion_empty.set_visible(not suggestions)
        self.suggestion_empty.set_text(
            'Press Clean up to get suggestions.' if version_id is None
            else 'All dismissed. A new clean up brings fresh ones.')
        problem = cleanup.problem
        self.banner.set_revealed(bool(problem))
        if problem:
            kind, message = problem
            self.banner.set_title(GLib.markup_escape_text(
                message + ('. Add one in Settings.' if kind == 'setup' else '')))
            self.banner.set_button_label('Open Settings' if kind == 'setup' else 'Try again')
            if kind == 'setup':
                self.banner.remove_css_class('error')
            else:
                self.banner.add_css_class('error')
        window.render_status()


def present_settings(window, cleanup):
    config = cleanup.config()
    dialog = Adw.PreferencesDialog(title='Settings')
    page = Adw.PreferencesPage(title='Settings', icon_name='preferences-system-symbolic')
    dialog.add(page)
    group = Adw.PreferencesGroup(
        title='Clean up model',
        description='Any service that speaks the OpenAI-style API. Only the Clean up '
                    'button sends text; dictation never leaves this computer.')
    page.add(group)
    names = [name for name, _url, _key in llm.PROVIDERS]
    provider = Adw.ComboRow(title='Provider', model=Gtk.StringList.new(names))
    provider.set_selected(names.index(config['provider']) if config['provider'] in names else len(names) - 1)
    group.add(provider)
    address = Adw.EntryRow(title='Address', text=config['base_url'])
    group.add(address)
    key_row = Adw.PasswordEntryRow(title='API key')
    group.add(key_row)
    model = Adw.EntryRow(title='Model', text=config['model'])
    group.add(model)
    hint = Gtk.Label(
        label='The key is kept in GNOME’s password keyring, not in Dogspeak’s files. '
              'OpenRouter model names look like provider/model (openrouter.ai/models); '
              'Claude model names look like claude-opus-5-5 or claude-haiku-5-5.',
        xalign=0, wrap=True, margin_top=8)
    hint.add_css_class('caption')
    hint.add_css_class('dim-label')
    group.add(hint)

    test_group = Adw.PreferencesGroup()
    page.add(test_group)
    test_row = Adw.ActionRow(title='Test connection',
                             subtitle='Free: checks the key and model without using tokens.')
    test_button = Gtk.Button(label='Test', valign=Gtk.Align.CENTER)
    test_row.add_suffix(test_button)
    test_group.add(test_row)

    purposes = Adw.PreferencesGroup(
        title='Purpose list',
        description=f'The menu shows the presets plus your last {5} typed purposes.')
    page.add(purposes)
    clear_row = Adw.ActionRow(title='Recent purposes')
    clear = Gtk.Button(label='Clear', valign=Gtk.Align.CENTER)
    clear.connect('clicked', lambda _b: (cleanup.clear_recent(), clear.set_sensitive(False)))
    clear.set_sensitive(bool(cleanup.recent_purposes()))
    clear_row.add_suffix(clear)
    purposes.add(clear_row)

    state = {'loaded_key': None, 'address': config['base_url']}

    def load_key(url):
        def work():
            try:
                key = cleanup.credentials.get(url)
            except Exception:
                key = ''
            GLib.idle_add(show_key, url, key)
        cleanup.spawn(work)

    def show_key(url, key):
        if url == address.get_text().strip():
            state['loaded_key'] = key
            key_row.set_text(key)
        return False

    def provider_changed(*_):
        _name, url, _needs = llm.PROVIDERS[provider.get_selected()]
        if url:
            address.set_text(url)
        suggested = llm.DEFAULT_MODELS.get(url)
        current = model.get_text().strip()
        if suggested and not current.startswith(suggested.split('-')[0] + '-'):
            model.set_text(suggested)

    def address_changed(*_):
        url = address.get_text().strip()
        if url != state['address']:
            state['address'] = url
            state['loaded_key'] = None
            key_row.set_text('')
            load_key(url)

    def save():
        url = address.get_text().strip()
        cleanup.save_config(names[provider.get_selected()], url, model.get_text())
        key = key_row.get_text().strip()
        if state['loaded_key'] is not None and key != state['loaded_key']:
            cleanup.credentials.set(url, key)
            state['loaded_key'] = key

    def test(_button):
        try:
            save()
        except Exception as exc:
            test_row.set_subtitle(f'Could not save the key: {exc}')
            return
        test_button.set_sensitive(False)
        test_button.set_label('Testing…')
        url, key, name = address.get_text().strip(), key_row.get_text().strip(), model.get_text()

        def work():
            try:
                lines = cleanup.client_factory(url, key, name).test()
                result = ('Connected · ' + ' · '.join(lines), True)
            except llm.LLMError as exc:
                result = (str(exc), False)
            except Exception as exc:
                result = (f'Test failed: {exc}', False)
            GLib.idle_add(done, *result)
        cleanup.spawn(work)

    def done(message, ok):
        test_button.set_sensitive(True)
        test_button.set_label('Test')
        test_row.set_subtitle(GLib.markup_escape_text(message))
        if ok:
            test_row.remove_css_class('error')
        else:
            test_row.add_css_class('error')
        return False

    def closed(_dialog):
        try:
            save()
        except Exception as exc:
            cleanup.problem = ('failed', f'Could not save the API key: {exc}')
            cleanup.on_change()

    provider.connect('notify::selected', provider_changed)
    address.connect('changed', address_changed)
    test_button.connect('clicked', test)
    dialog.connect('closed', closed)
    load_key(config['base_url'])
    dialog.present(window)
    window.settings_dialog = dialog
    return dialog
