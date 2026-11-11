#!/usr/bin/python3
"""Install and enable the companion without replacing the user's settings."""

import os
from pathlib import Path
import shutil
import subprocess

from gi.repository import Gio

UUID = "hold-to-talk@spookysys.github.io"
source = Path(__file__).resolve().parent
data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
destination = data / "gnome-shell/extensions" / UUID
files = ("extension.js", "history.js", "metadata.json")
changed = any(not (destination / name).exists() or
              (destination / name).read_bytes() != (source / name).read_bytes()
              for name in files)
destination.mkdir(parents=True, exist_ok=True)
for name in files:
    shutil.copyfile(source / name, destination / name)
settings = Gio.Settings.new("org.gnome.shell")
enabled = settings.get_strv("enabled-extensions")
if UUID not in enabled:
    settings.set_strv("enabled-extensions", [*enabled, UUID])
disabled = settings.get_strv("disabled-extensions")
if UUID in disabled:
    settings.set_strv("disabled-extensions", [item for item in disabled if item != UUID])
Gio.Settings.sync()
try:
    check = subprocess.run(
        ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
         "--object-path", "/org/gnome/Shell/Extensions/HoldToTalk",
         "--method", "org.gnome.Shell.Extensions.HoldToTalk.Capture", "0"],
        capture_output=True, timeout=3)
    ready = not changed and check.returncode == 0
except (OSError, subprocess.TimeoutExpired):
    ready = False
if not ready:
    print("Window restore installed. Log out and back in to load the GNOME extension.")
    print("The current voice process is left running until then.")
raise SystemExit(0 if ready else 2)
