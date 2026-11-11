"""Run a synthetic window-restoration check in an isolated GNOME 50 session.

Run with /usr/bin/python3 tests/gnome_integration.py. This needs GNOME Shell,
PyGObject, GTK 3, and a render device. It does not use the physical keyboard.
"""

import ast
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[1]


def windows():
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib, GLibUnix
    opened = []

    def create():
        window = Gtk.Window(title="Synthetic dictation test")
        window.add(Gtk.Entry())
        window.show_all()
        window.present()
        opened.append(window)
        return GLib.SOURCE_CONTINUE

    create()
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, create)
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR2,
                       lambda: (opened[0].destroy(), GLib.SOURCE_CONTINUE)[1])
    Gtk.main()


def session():
    root = Path(os.environ["VOICE_TEST_ROOT"])
    env = dict(os.environ)
    env["XDG_DATA_DIRS"] = str(root / "data") + ":/usr/share"
    modes = root / "data/gnome-shell/modes"
    modes.mkdir(parents=True)
    (modes / "voice-test.json").write_text(json.dumps({
        "parentMode": "user", "components": [], "hasOverview": False,
        "showWelcomeDialog": False, "showCalendarEvents": False,
        "panel": {"left": [], "center": [], "right": []},
    }))
    uuid = "hold-to-talk@spookysys.github.io"
    shutil.copytree(REPO / "gnome", root / "data/gnome-shell/extensions" / uuid)
    subprocess.run(["gsettings", "set", "org.gnome.shell", "enabled-extensions", f"['{uuid}']"],
                   env=env, check=True, timeout=5)
    shell = app = None

    def call(method, *args):
        return subprocess.run([
            "gdbus", "call", "--session", "--dest", "org.gnome.Shell",
            "--object-path", "/org/gnome/Shell/Extensions/HoldToTalk",
            "--method", "org.gnome.Shell.Extensions.HoldToTalk." + method, *args,
        ], env=env, text=True, capture_output=True, timeout=3)

    def token():
        result = call("Capture", str(time.monotonic_ns() // 1000))
        return ast.literal_eval(result.stdout)[0] if result.returncode == 0 else ""

    def until(condition):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            if shell.poll() is not None:
                raise RuntimeError("The isolated GNOME Shell exited")
            time.sleep(0.1)
        raise RuntimeError("Window test timed out; inspect the isolated test logs")

    with (root / "shell.log").open("w") as log:
        try:
            shell = subprocess.Popen([
                "gnome-shell", "--mode=voice-test", "--headless", "--wayland",
                "--virtual-monitor", "1024x768", "--wayland-display", "voice-test",
            ], env=env, stdout=log, stderr=log)
            until(lambda: call("Capture", "0").returncode == 0)
            assert call("Restore", "invalid").stdout.strip() == "(false,)"
            env["WAYLAND_DISPLAY"] = "voice-test"
            app = subprocess.Popen([sys.executable, __file__, "--windows"],
                                   env=env, stdout=log, stderr=log)
            first = until(token)
            first_at = str(time.monotonic_ns() // 1000)
            app.send_signal(signal.SIGUSR1)
            second = until(lambda: t if (t := token()) and t != first else "")
            assert ast.literal_eval(call("Capture", first_at).stdout)[0] == first
            assert call("Restore", first).stdout.strip() == "(true,)"
            assert token() == first
            assert call("Restore", second).stdout.strip() == "(true,)"
            app.send_signal(signal.SIGUSR2)
            until(lambda: call("Restore", first).stdout.strip() == "(false,)")
            print("PASS: capture history, original window restoration, and closed-window rejection")
        finally:
            for process in (app, shell):
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()


def main():
    if sys.argv[1:] == ["--windows"]:
        windows()
        return
    if sys.argv[1:] == ["--session"]:
        session()
        return
    root = Path(tempfile.mkdtemp(prefix="voice-shell-check-"))
    # Set the environment before starting D-Bus. Activated services inherit it.
    env = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    for key, leaf in (("HOME", "home"), ("XDG_DATA_HOME", "data"),
                      ("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"),
                      ("XDG_RUNTIME_DIR", "run")):
        path = root / leaf
        path.mkdir(mode=0o700)
        env[key] = str(path)
    env.update(VOICE_TEST_ROOT=str(root), NO_AT_BRIDGE="1", GVFS_DISABLE_FUSE="1",
               XDG_SESSION_TYPE="wayland", XDG_CURRENT_DESKTOP="GNOME")
    with (root / "bus.log").open("w") as log:
        result = subprocess.run([
            "timeout", "-k", "5", "55", "dbus-run-session", "--",
            sys.executable, __file__, "--session",
        ], env=env, stderr=log, timeout=65)
    # The document portal can leave a disconnected FUSE mount after bus exit.
    subprocess.run(["fusermount3", "-u", "-z", str(root / "run/doc")],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    if result.returncode:
        print(f"Test failed. Logs: {root}")
        raise SystemExit(result.returncode)
    shutil.rmtree(root)


if __name__ == "__main__":
    main()
