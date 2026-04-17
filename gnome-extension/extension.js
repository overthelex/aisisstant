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

const STATS_INTERVALS = [
    {key: '10m', label: 'Last 10 min'},
    {key: '20m', label: 'Last 20 min'},
    {key: '30m', label: 'Last 30 min'},
    {key: '60m', label: 'Last 60 min'},
    {key: '1d',  label: 'Last 1 day'},
    {key: '2d',  label: 'Last 2 days'},
    {key: '3d',  label: 'Last 3 days'},
];

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

function _formatCount(n) {
    // Group thousands with a thin space for readability.
    return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, '\u2009');
}

const XsistantIndicator = GObject.registerClass(
class XsistantIndicator extends PanelMenu.Button {
    _init(extensionPath) {
        super._init(0.0, 'Xsistant', false);

        this._extensionPath = extensionPath;
        this._statsCancellable = null;

        const logoFile = Gio.File.new_for_path(
            `${extensionPath}/xsistant-logo-symbolic.svg`
        );
        this._logo = new St.Icon({
            gicon: new Gio.FileIcon({file: logoFile}),
            icon_size: 16,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this.add_child(this._logo);

        this._serviceActive = true;
        this._buildMenu();

        this.menu.connect('open-state-changed', (_menu, isOpen) => {
            if (isOpen) {
                this._refreshStats();
                this._refreshServiceState();
            }
        });

        // Prime the pause-label and indicator dim state on load.
        this._refreshServiceState();
    }

    _buildMenu() {
        this.menu.addMenuItem(
            new PopupMenu.PopupSeparatorMenuItem('Records in DB')
        );

        this._statsItems = {};
        for (const {key, label} of STATS_INTERVALS) {
            const item = new PopupMenu.PopupMenuItem(label, {reactive: false});
            const value = new St.Label({
                text: '…',
                x_align: Clutter.ActorAlign.END,
                x_expand: true,
                y_align: Clutter.ActorAlign.CENTER,
            });
            item.add_child(value);
            item._valueLabel = value;
            this._statsItems[key] = item;
            this.menu.addMenuItem(item);
        }

        this._errorItem = new PopupMenu.PopupMenuItem('', {reactive: false});
        this._errorItem.label.set_style('color: #e06c75;');
        this._errorItem.visible = false;
        this.menu.addMenuItem(this._errorItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._pauseItem = new PopupMenu.PopupMenuItem('Pause tracking');
        this._pauseItem.connect('activate', () => this._togglePause());
        this.menu.addMenuItem(this._pauseItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const reportItem = new PopupMenu.PopupMenuItem('Report');
        reportItem.connect('activate', () => this._openReport());
        this.menu.addMenuItem(reportItem);

        const settingsItem = new PopupMenu.PopupMenuItem('Settings');
        settingsItem.connect('activate', () => this._openSettings());
        this.menu.addMenuItem(settingsItem);

        const quitItem = new PopupMenu.PopupMenuItem('Quit');
        quitItem.connect('activate', () => this._quit());
        this.menu.addMenuItem(quitItem);
    }

    _openSettings() {
        try {
            GLib.spawn_command_line_async('aisisstant-setup');
        } catch (e) {
            log(`Xsistant: failed to open settings: ${e}`);
        }
    }

    _openReport() {
        try {
            GLib.spawn_command_line_async('aisisstant-report');
        } catch (e) {
            log(`Xsistant: failed to open report: ${e}`);
        }
    }

    _quit() {
        // Full shutdown: hide the indicator immediately, stop the tracker
        // service, and disable this extension so it stays hidden across
        // sessions until the user re-enables it from the Extensions app.
        if (this.menu.isOpen) this.menu.close();
        // PanelMenu.Button is added to the panel via its `container`, so
        // hiding `this` alone leaves the slot visible — hide the container.
        if (this.container) this.container.hide();
        this.hide();
        try {
            // `disable --now` stops it and clears the user-unit symlinks so
            // it won't auto-start on next login/boot.
            Gio.Subprocess.new(
                ['systemctl', '--user', 'disable', '--now', 'aisisstant.service'],
                Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE
            );
        } catch (e) {
            log(`Xsistant: failed to stop service: ${e}`);
        }
        try {
            Gio.Subprocess.new(
                ['/usr/bin/gnome-extensions', 'disable', 'aisisstant-tracker@vovkes'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            ).communicate_utf8_async(null, null, (p, res) => {
                try {
                    const [, out, err] = p.communicate_utf8_finish(res);
                    if (!p.get_successful()) {
                        log(`Xsistant: gnome-extensions disable failed: ${(err || out || '').trim()}`);
                    }
                } catch (e) {
                    log(`Xsistant: gnome-extensions disable error: ${e}`);
                }
            });
        } catch (e) {
            log(`Xsistant: failed to disable extension: ${e}`);
        }
    }

    _togglePause() {
        const cmd = this._serviceActive
            ? 'systemctl --user stop aisisstant.service'
            : 'systemctl --user start aisisstant.service';
        try {
            GLib.spawn_command_line_async(cmd);
        } catch (e) {
            log(`Xsistant: failed to toggle service: ${e}`);
            return;
        }
        // systemd's state flip isn't instant; re-query shortly after.
        GLib.timeout_add(GLib.PRIORITY_DEFAULT, 700, () => {
            this._refreshServiceState();
            return GLib.SOURCE_REMOVE;
        });
    }

    _refreshServiceState() {
        let proc;
        try {
            proc = Gio.Subprocess.new(
                ['systemctl', '--user', 'is-active', 'aisisstant.service'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
            );
        } catch (e) {
            return;
        }
        proc.communicate_utf8_async(null, null, (p, res) => {
            let ok, stdout;
            try {
                [ok, stdout, ] = p.communicate_utf8_finish(res);
            } catch (e) {
                return;
            }
            const active = (stdout || '').trim() === 'active';
            this._serviceActive = active;
            if (this._pauseItem) {
                this._pauseItem.label.set_text(
                    active ? 'Pause tracking' : 'Resume tracking'
                );
            }
            // Dim the top-bar icon when paused so the state is visible.
            if (this._logo) {
                this._logo.opacity = active ? 255 : 110;
            }
        });
    }

    // Split click handling: primary button toggles the stats menu,
    // secondary button jumps straight to the settings app.
    vfunc_event(event) {
        if (event.type() === Clutter.EventType.BUTTON_PRESS) {
            const button = event.get_button();
            if (button === Clutter.BUTTON_SECONDARY) {
                if (this.menu.isOpen) this.menu.close();
                this._openSettings();
                return Clutter.EVENT_STOP;
            }
        }
        return super.vfunc_event(event);
    }

    _setAllStatsText(text) {
        for (const key of Object.keys(this._statsItems)) {
            this._statsItems[key]._valueLabel.set_text(text);
        }
    }

    _showError(msg) {
        this._errorItem.label.set_text(msg);
        this._errorItem.visible = true;
    }

    _hideError() {
        this._errorItem.visible = false;
    }

    _spawnStats() {
        // Prefer the console-script; fall back to running the module directly
        // (useful for editable installs or when the .deb hasn't been rebuilt).
        const attempts = [
            ['aisisstant-stats'],
            ['python3', '-m', 'aisisstant.stats'],
        ];
        let lastErr = null;
        for (const argv of attempts) {
            try {
                return Gio.Subprocess.new(
                    argv,
                    Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE
                );
            } catch (e) {
                lastErr = e;
            }
        }
        throw lastErr || new Error('no stats binary available');
    }

    _refreshStats() {
        if (this._statsCancellable) {
            this._statsCancellable.cancel();
        }
        this._hideError();
        this._setAllStatsText('…');

        const cancellable = new Gio.Cancellable();
        this._statsCancellable = cancellable;

        let proc;
        try {
            proc = this._spawnStats();
        } catch (e) {
            this._setAllStatsText('?');
            this._showError(`Cannot run aisisstant-stats: ${e.message}`);
            return;
        }

        proc.communicate_utf8_async(null, cancellable, (p, res) => {
            if (cancellable.is_cancelled()) return;
            let ok, stdout, stderr;
            try {
                [ok, stdout, stderr] = p.communicate_utf8_finish(res);
            } catch (e) {
                this._setAllStatsText('?');
                this._showError(`aisisstant-stats failed: ${e.message}`);
                return;
            }

            let data = null;
            try {
                data = JSON.parse(stdout || '{}');
            } catch (_) {
                // fall through to error branch below
            }

            if (!data || data.ok !== true) {
                this._setAllStatsText('?');
                const msg = (data && data.error)
                    ? data.error
                    : (stderr || 'stats query failed').toString().trim();
                this._showError(msg);
                return;
            }

            const byKey = {};
            for (const row of data.intervals || []) {
                byKey[row.label] = row;
            }
            for (const {key} of STATS_INTERVALS) {
                const row = byKey[key];
                const item = this._statsItems[key];
                if (!item) continue;
                if (row) {
                    item._valueLabel.set_text(_formatCount(row.total));
                } else {
                    item._valueLabel.set_text('—');
                }
            }
        });
    }

    destroy() {
        if (this._statsCancellable) {
            this._statsCancellable.cancel();
            this._statsCancellable = null;
        }
        super.destroy();
    }
});

export default class AisisstantTrackerExtension extends Extension {
    enable() {
        _dbus = new WindowTrackerDBus();
        _indicator = new XsistantIndicator(this.path);
        Main.panel.addToStatusArea('xsistant', _indicator, 0, 'right');

        // If the user previously hit Quit, the systemd unit was disabled.
        // Re-enabling the extension should bring the tracker back — but only
        // in that case, so we don't override a deliberate Pause across a
        // Shell restart.
        try {
            const proc = Gio.Subprocess.new(
                ['systemctl', '--user', 'is-enabled', 'aisisstant.service'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE
            );
            proc.communicate_utf8_async(null, null, (p, res) => {
                let out = '';
                try { [, out, ] = p.communicate_utf8_finish(res); } catch (_) { return; }
                if ((out || '').trim() !== 'disabled') return;
                try {
                    Gio.Subprocess.new(
                        ['systemctl', '--user', 'enable', '--now', 'aisisstant.service'],
                        Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE
                    );
                } catch (e) {
                    log(`Xsistant: failed to re-enable service on extension enable: ${e}`);
                }
            });
        } catch (_) {
            // systemctl missing — nothing we can do, leave as-is.
        }
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
