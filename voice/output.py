"""Put text into the focused field: clipboard, paste chord, clipboard back.

GNOME on Wayland lets no program type into another window. The clipboard
reaches every application. voice-keys makes keyd send the paste chord, so this
user process needs no access to /dev/uinput.

Shift+Insert pastes the clipboard in GTK, Qt and browsers, and the primary
selection in some terminals. Both get the text. Afterwards both get their old
text back. Content that is not text (an image) is not restored.
"""

from __future__ import annotations

import subprocess
import threading

RESTORE_AFTER_S = 1.0   # an application reads the selection when it pastes

TEXT = "text/plain;charset=utf-8"


def read(primary: bool) -> str | None:
    command = ["wl-paste", "--no-newline", "--type", "text"] + (["--primary"] if primary else [])
    try:
        result = subprocess.run(command, capture_output=True, timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.decode(errors="replace") if result.returncode == 0 else None


def write(text: str, primary: bool) -> bool:
    # wl-copy leaves a process that owns the selection and inherits our open
    # files, so its output must not be a pipe that we wait on.
    command = ["wl-copy", "--type", TEXT] + (["--primary"] if primary else [])
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process.communicate(text.encode(), timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return process.returncode == 0


# The user's own clipboard texts, saved before the first paste of a series.
# A paste soon after another must not save our own text as the user's, and the
# restore of the first paste must not come before the application reads the
# second one. So one restore serves the series and moves later with each paste.
_lock = threading.Lock()
_saved: dict[bool, str | None] = {}
_restore: threading.Timer | None = None


def insert(text: str, paste) -> bool:
    """Paste `text` into the focused field; `paste` sends the chord and says if it could."""
    global _saved, _restore
    with _lock:
        if _restore is not None:
            _restore.cancel()
        else:
            _saved = {primary: read(primary) for primary in (False, True)}
        if not all([write(text, False), write(text, True)]):
            _restore_now()
            return False
        if not paste():
            _restore_now()
            return False
        timer = threading.Timer(RESTORE_AFTER_S, lambda: _restore_later(timer))
        _restore = timer
        timer.start()
    return True


def _restore_later(timer: threading.Timer) -> None:
    with _lock:
        if timer is _restore:       # else a newer paste canceled it too late
            _restore_now()


def _restore_now() -> None:
    global _restore
    _restore = None
    for primary, value in _saved.items():
        if value is not None:
            write(value, primary)
