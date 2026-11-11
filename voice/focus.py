"""Remember and restore windows through the GNOME companion extension.

Only opaque window tokens cross D-Bus. No titles or field contents are read.
All calls run in workers so a slow desktop cannot block the key loop.
"""

from concurrent.futures import Future
import re
import subprocess
import threading
import time

INTERFACE = "org.gnome.Shell.Extensions.HoldToTalk"
PATH = "/org/gnome/Shell/Extensions/HoldToTalk"


class NotLoaded(Exception):
    """GNOME Shell has not loaded this version of the extension.

    This happens after an installation or an update until the next login.
    """


def call(method: str, *args: str) -> str:
    """Return the reply, or "" when the call fails."""
    try:
        result = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
             "--object-path", PATH, "--method", f"{INTERFACE}.{method}", *args],
            capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if result.returncode == 0:
        return result.stdout.strip()
    if "UnknownMethod" in result.stderr:      # no such object, or an older version
        raise NotLoaded(result.stderr.strip())
    return ""


class Focus:
    def capture(self) -> Future[str]:
        # GNOME retains recent focus transitions, so worker scheduling does not
        # change which window was focused when the daemon received key-down.
        at = str(time.monotonic_ns() // 1000)
        result: Future[str] = Future()

        def capture() -> None:
            try:
                reply = call("Capture", at)
            except NotLoaded as error:
                result.set_exception(error)
                return
            match = re.fullmatch(r"\('([a-f0-9-]+:[0-9]+)',\)", reply)
            result.set_result(match[1] if match else "")

        threading.Thread(target=capture, daemon=True, name="focus-capture").start()
        return result

    def restore(self, destination: Future[str] | None) -> str:
        """Return "restored", "not-loaded", or "changed" for every other failure."""
        if destination is None:
            return "changed"
        try:
            token = destination.result(timeout=2)
            if token and call("Restore", token) == "(true,)":
                return "restored"
        except NotLoaded:
            return "not-loaded"
        except Exception:
            pass        # A capture failure must never turn into a paste to current focus.
        return "changed"
