// Dogspeak overlay: GNOME Wayland will not keep an app window above the focused
// one, so the Shell draws the status pill. The app drives it over the session
// bus; nothing here records, transcribes or reads text other than what it is
// handed for the clipboard. The same extension adds Dogspeak's top-bar (tray)
// menu, because GTK 4 apps have no tray API of their own.
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'io.github.Remco28.Dogspeak.Overlay';
const OBJECT_PATH = '/io/github/Remco28/Dogspeak/Overlay';
const VERSION = 2;
// The Dogspeak GApplication; the tray shows only while it is running.
const APP_BUS_NAME = 'io.github.Remco28.Dogspeak';
const APP_OBJECT_PATH = '/io/github/Remco28/Dogspeak';
const SHORTCUT_LABEL = 'Super+R';
const STATES = ['recording', 'transcribing', 'saved', 'error', 'info'];
// A crashed client must never leave Enter/Esc grabbed; this is the backstop
// behind the bus-name watch. Recording stops at 115 s, so this is generous.
const KEY_GRAB_LIMIT_SECONDS = 600;
const KEYS = {
    stop: ['Return', 'KP_Enter'],
    cancel: ['Escape'],
};

const IFACE = `
<node>
  <interface name="io.github.Remco28.Dogspeak.Overlay">
    <method name="Show">
      <arg type="s" name="state" direction="in"/>
      <arg type="s" name="title" direction="in"/>
      <arg type="s" name="hint" direction="in"/>
    </method>
    <method name="Hide"/>
    <method name="SetKeys">
      <arg type="as" name="keys" direction="in"/>
    </method>
    <method name="SetClipboard">
      <arg type="s" name="text" direction="in"/>
    </method>
    <property name="Version" type="u" access="read"/>
    <signal name="KeyPressed">
      <arg type="s" name="key"/>
    </signal>
  </interface>
</node>`;

class Tray {
    constructor() {
        this._button = new PanelMenu.Button(0.5, 'Dogspeak', false);
        this._icon = new St.Icon({
            icon_name: 'audio-input-microphone-symbolic',
            style_class: 'system-status-icon',
        });
        this._button.add_child(this._icon);
        const menu = this._button.menu;
        menu.addAction('Show Dogspeak', () => this._activate('show-window'));
        this._record = menu.addAction('', () => this._activate('toggle-recording'));
        menu.addAction('New draft', () => {
            this._activate('new-draft');
            this._activate('show-window');
        });
        menu.addAction('Copy all', () => this._activate('copy-all'));
        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        menu.addAction('Quit Dogspeak', () => this._activate('quit'));
        this.setState('');
        this._button.visible = false;
        Main.panel.addToStatusArea('dogspeak-tray', this._button);
        this._watch = Gio.bus_watch_name_on_connection(
            Gio.DBus.session, APP_BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            () => {
                this._button.visible = true;
            },
            () => {
                this._button.visible = false;
                this.setState('');
            });
    }

    setState(state) {
        for (const name of ['recording', 'transcribing'])
            this._icon.remove_style_class_name(`dogspeak-tray-${name}`);
        if (state === 'recording' || state === 'transcribing')
            this._icon.add_style_class_name(`dogspeak-tray-${state}`);
        this._record.label.text = state === 'recording'
            ? `Stop recording (${SHORTCUT_LABEL})`
            : `Start recording (${SHORTCUT_LABEL})`;
    }

    _activate(action) {
        Gio.DBus.session.call(APP_BUS_NAME, APP_OBJECT_PATH, 'org.gtk.Actions', 'Activate',
            new GLib.Variant('(sava{sv})', [action, [], {}]), null,
            Gio.DBusCallFlags.NO_AUTO_START, -1, null, null);
    }

    destroy() {
        Gio.bus_unwatch_name(this._watch);
        this._button.destroy();
        this._button = null;
    }
}

class Overlay {
    constructor(onState = () => {}) {
        this._onState = onState;
        this._dot = new St.Widget({style_class: 'dogspeak-dot', y_align: Clutter.ActorAlign.CENTER});
        this._title = new St.Label({style_class: 'dogspeak-title', y_align: Clutter.ActorAlign.CENTER});
        this._hint = new St.Label({style_class: 'dogspeak-hint', y_align: Clutter.ActorAlign.CENTER});
        this._pill = new St.BoxLayout({
            style_class: 'dogspeak-pill',
            reactive: false,
            visible: false,
            opacity: 0,
        });
        this._pill.add_child(this._dot);
        this._pill.add_child(this._title);
        this._pill.add_child(this._hint);
        // GNOME 50 rejects unknown params (affectsInputRegion is gone); defaults are right.
        Main.layoutManager.addTopChrome(this._pill);
        this._pill.connect('notify::width', () => this._place());
        this._monitorsId = Main.layoutManager.connect('monitors-changed', () => this._place());
        this._grabs = new Map();
        this._owner = null;
        this._ownerWatch = 0;
        this._grabTimeout = 0;
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(
            Gio.DBus.session, BUS_NAME, Gio.BusNameOwnerFlags.NONE, null, null);
        this._acceleratorId = global.display.connect('accelerator-activated',
            (_display, action) => this._activated(action));
    }

    get Version() {
        return VERSION;
    }

    ShowAsync([state, title, hint], invocation) {
        this._watchOwner(invocation.get_sender());
        for (const name of STATES)
            this._dot.remove_style_class_name(name);
        if (STATES.includes(state))
            this._dot.add_style_class_name(state);
        this._title.text = title;
        this._hint.text = hint;
        this._hint.visible = hint.length > 0;
        this._onState(state);
        // Also cancels a fade-out that is still running.
        this._pill.remove_all_transitions();
        this._pill.visible = true;
        this._place();
        if (this._pill.opacity !== 255)
            this._pill.ease({opacity: 255, duration: 120, mode: Clutter.AnimationMode.EASE_OUT_QUAD});
        invocation.return_value(null);
    }

    HideAsync(_params, invocation) {
        this._hide();
        this._onState('');
        invocation.return_value(null);
    }

    SetKeysAsync([keys], invocation) {
        this._watchOwner(invocation.get_sender());
        this._setKeys(keys);
        invocation.return_value(null);
    }

    SetClipboard(text) {
        St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, text);
    }

    _hide() {
        if (!this._pill.visible)
            return;
        this._pill.remove_all_transitions();
        this._pill.ease({
            opacity: 0,
            duration: 180,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
            onStopped: isFinished => {
                if (isFinished && this._pill)
                    this._pill.visible = false;
            },
        });
    }

    _place() {
        // The monitor with keyboard focus is where the user is dictating.
        const monitor = Main.layoutManager.focusMonitor ?? Main.layoutManager.primaryMonitor;
        if (!monitor || !this._pill)
            return;
        const [, width] = this._pill.get_preferred_width(-1);
        const [, height] = this._pill.get_preferred_height(width);
        const bottomGap = Math.round(monitor.height * 0.08);
        this._pill.set_position(
            Math.round(monitor.x + (monitor.width - width) / 2),
            monitor.y + monitor.height - height - bottomGap);
    }

    _setKeys(keys) {
        const wanted = new Set();
        for (const key of keys) {
            for (const accelerator of KEYS[key] ?? [])
                wanted.add(`${key}:${accelerator}`);
        }
        for (const [action, id] of this._grabs) {
            if (!wanted.has(id)) {
                global.display.ungrab_accelerator(action);
                this._grabs.delete(action);
            }
        }
        const held = new Set(this._grabs.values());
        for (const id of wanted) {
            if (held.has(id))
                continue;
            const accelerator = id.slice(id.indexOf(':') + 1);
            const action = global.display.grab_accelerator(accelerator, Meta.KeyBindingFlags.NONE);
            if (action === Meta.KeyBindingAction.NONE) {
                console.warn(`Dogspeak overlay: could not grab ${accelerator}`);
                continue;
            }
            Main.wm.allowKeybinding(Meta.external_binding_name_for_action(action), Shell.ActionMode.ALL);
            this._grabs.set(action, id);
        }
        if (this._grabTimeout) {
            GLib.source_remove(this._grabTimeout);
            this._grabTimeout = 0;
        }
        if (this._grabs.size) {
            this._grabTimeout = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, KEY_GRAB_LIMIT_SECONDS, () => {
                this._grabTimeout = 0;
                this._setKeys([]);
                return GLib.SOURCE_REMOVE;
            });
        }
    }

    _activated(action) {
        const id = this._grabs.get(action);
        if (id === undefined)
            return;
        this._dbus.emit_signal('KeyPressed', new GLib.Variant('(s)', [id.slice(0, id.indexOf(':'))]));
    }

    _watchOwner(sender) {
        if (!sender || sender === this._owner)
            return;
        this._unwatchOwner();
        this._owner = sender;
        this._ownerWatch = Gio.bus_watch_name_on_connection(
            Gio.DBus.session, sender, Gio.BusNameWatcherFlags.NONE, null, () => {
                // The app quit or crashed: release its keys and its pill.
                this._setKeys([]);
                this._hide();
                this._onState('');
                this._unwatchOwner();
            });
    }

    _unwatchOwner() {
        if (this._ownerWatch)
            Gio.bus_unwatch_name(this._ownerWatch);
        this._ownerWatch = 0;
        this._owner = null;
    }

    destroy() {
        this._setKeys([]);
        this._unwatchOwner();
        global.display.disconnect(this._acceleratorId);
        Main.layoutManager.disconnect(this._monitorsId);
        Gio.bus_unown_name(this._nameId);
        this._dbus.unexport();
        this._dbus = null;
        Main.layoutManager.removeChrome(this._pill);
        this._pill.destroy();
        this._pill = null;
    }
}

export default class DogspeakOverlayExtension extends Extension {
    enable() {
        this._tray = new Tray();
        this._overlay = new Overlay(state => this._tray?.setState(state));
    }

    disable() {
        this._overlay?.destroy();
        this._overlay = null;
        this._tray?.destroy();
        this._tray = null;
    }
}
