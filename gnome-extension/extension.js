import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `
<node>
  <interface name="com.aisisstant.WindowTracker">
    <method name="GetActiveWindow">
      <arg type="s" direction="out" name="json_info"/>
    </method>
    <signal name="FocusChanged">
      <arg type="s" name="json_info"/>
    </signal>
  </interface>
</node>`;

let _dbus = null;
let _indicator = null;

class WindowTrackerDBus {
    constructor() {
        this._dbusImpl = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbusImpl.export(Gio.DBus.session, '/com/aisisstant/WindowTracker');

        this._focusSignalId = global.display.connect(
            'notify::focus-window',
            this._onFocusChanged.bind(this)
        );
    }

    _getWindowInfo() {
        try {
            const win = global.display.focus_window;
            if (!win) return '{}';
            return JSON.stringify({
                wm_class: win.get_wm_class() || '',
                title: win.get_title() || '',
                pid: win.get_pid() || 0,
            });
        } catch (e) {
            return '{}';
        }
    }

    GetActiveWindow() {
        return this._getWindowInfo();
    }

    _onFocusChanged() {
        const info = this._getWindowInfo();
        this._dbusImpl.emit_signal('FocusChanged',
            new GLib.Variant('(s)', [info]));
    }

    destroy() {
        if (this._focusSignalId) {
            global.display.disconnect(this._focusSignalId);
            this._focusSignalId = null;
        }
        this._dbusImpl.unexport();
    }
}

const XsistantIndicator = GObject.registerClass(
class XsistantIndicator extends PanelMenu.Button {
    _init(extensionPath) {
        super._init(0.0, 'Xsistant', false);

        const logoFile = Gio.File.new_for_path(
            `${extensionPath}/xsistant-logo-symbolic.svg`
        );
        this._logo = new St.Icon({
            gicon: new Gio.FileIcon({file: logoFile}),
            icon_size: 16,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this.add_child(this._logo);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem('Xsistant'));

        const settingsItem = new PopupMenu.PopupMenuItem('Settings');
        settingsItem.connect('activate', () => {
            try {
                GLib.spawn_command_line_async('aisisstant-setup');
            } catch (e) {
                log(`Xsistant: failed to open settings: ${e}`);
            }
        });
        this.menu.addMenuItem(settingsItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const quitItem = new PopupMenu.PopupMenuItem('Quit');
        quitItem.connect('activate', () => {
            try {
                GLib.spawn_command_line_async(
                    'systemctl --user stop aisisstant.service'
                );
            } catch (e) {
                log(`Xsistant: failed to stop service: ${e}`);
            }
        });
        this.menu.addMenuItem(quitItem);
    }
});

export default class AisisstantTrackerExtension extends Extension {
    enable() {
        _dbus = new WindowTrackerDBus();
        _indicator = new XsistantIndicator(this.path);
        Main.panel.addToStatusArea('xsistant', _indicator, 0, 'right');
    }

    disable() {
        if (_indicator) {
            _indicator.destroy();
            _indicator = null;
        }
        if (_dbus) {
            _dbus.destroy();
            _dbus = null;
        }
    }
}
