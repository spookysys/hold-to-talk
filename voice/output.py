"""Put text into the focused field through the clipboard and a paste chord.

GNOME on Wayland lets no program type into another window. The clipboard
reaches every application. voice-keys makes keyd send the paste chord, so this
user process needs no access to /dev/uinput.

Shift+Insert pastes the clipboard in GTK, Qt and browsers, and the primary
selection in some terminals. Both keep the dictated text until another copy
replaces it. Automatic restoration could overwrite a later user copy.
"""

from __future__ import annotations

import subprocess
import time

PASTE_INTERVAL_S = 1.0   # allow the application to read before another paste

TEXT = "text/plain;charset=utf-8"


def write(text: str, primary: bool) -> bool:
    # wl-copy leaves a process that owns the selection and inherits our open
    # files, so its output must not be a pipe that we wait on.
    command = ["wl-copy", "--type", TEXT] + (["--primary"] if primary else [])
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.communicate(text.encode(), timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        if process is not None:
            process.kill()
            process.communicate()
        return False
    return process.returncode == 0


def copy(text: str) -> bool:
    """Put the text in the clipboard and in the primary selection."""
    return all(write(text, primary) for primary in (False, True))


def insert(text: str, paste) -> str:
    """Run one paste transaction in a worker, without overlapping another one.

    `paste` returns the helper's result: pasted, held, or failed. Success starts
    a short interval before another transaction can replace the clipboard.
    """
    if not copy(text):
        return "failed"
    result = paste()
    if result == "pasted":
        time.sleep(PASTE_INTERVAL_S)
    return result
