import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

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

export default class AisisstantTrackerExtension {
    enable() {
        _dbus = new WindowTrackerDBus();
    }

    disable() {
        if (_dbus) {
            _dbus.destroy();
            _dbus = null;
        }
    }
}
