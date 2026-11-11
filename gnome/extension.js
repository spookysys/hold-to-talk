import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {FocusHistory} from './history.js';

const XML = `<node>
<interface name="org.gnome.Shell.Extensions.HoldToTalk">
  <method name="Capture"><arg type="t" direction="in"/><arg type="s" direction="out"/></method>
  <method name="Restore"><arg type="s" direction="in"/><arg type="b" direction="out"/></method>
</interface></node>`;

export default class WindowRestore extends Extension {
    enable() {
        this._session = GLib.uuid_string_random();
        this._history = new FocusHistory();
        this._pending = new Map();
        this._focusSignal = global.display.connect('notify::focus-window', () => this._record());
        this._modeSignal = Main.sessionMode.connect('updated', () => this._record());
        this._record();
        this._bus = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._bus.export(Gio.DBus.session, '/org/gnome/Shell/Extensions/HoldToTalk');
    }

    _available() {
        return !Main.sessionMode.isLocked && !Main.sessionMode.isGreeter;
    }

    _token(window) {
        return window ? `${this._session}:${window.get_stable_sequence()}` : '';
    }

    _record() {
        const window = this._available() ? global.display.focus_window : null;
        this._history.record(GLib.get_monotonic_time(), this._token(window));
    }

    Capture(at) {
        if (!this._available() || at > GLib.get_monotonic_time())
            return '';
        return this._history.capture(at);
    }

    RestoreAsync([token], invocation) {
        const window = global.get_window_actors().map(actor => actor.meta_window)
            .find(candidate => this._token(candidate) === token);
        if (!this._available() || !window || !token) {
            invocation.return_value(new GLib.Variant('(b)', [false]));
            return;
        }
        Main.activateWindow(window);
        // XWayland activation can complete later. Confirm focus, with a bound.
        const deadline = GLib.get_monotonic_time() + 500000;
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 10, () => {
            const focused = this._available() && global.display.focus_window === window &&
                !global.stage.get_key_focus() && !global.stage.get_grab_actor();
            if (focused || !this._available() || GLib.get_monotonic_time() >= deadline) {
                this._pending.delete(id);
                invocation.return_value(new GLib.Variant('(b)', [focused]));
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        });
        this._pending.set(id, invocation);
    }

    disable() {
        this._bus?.unexport();
        global.display.disconnect(this._focusSignal);
        Main.sessionMode.disconnect(this._modeSignal);
        for (const [id, invocation] of this._pending) {
            GLib.source_remove(id);
            invocation.return_value(new GLib.Variant('(b)', [false]));
        }
        this._pending.clear();
        this._history = null;
        this._bus = null;
    }
}
