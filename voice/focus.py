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


def call(method: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ["gdbus", "call", "--session", "--dest", "org.gnome.Shell",
             "--object-path", PATH, "--method", f"{INTERFACE}.{method}", *args],
            capture_output=True, text=True, timeout=2)
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


class Focus:
    def capture(self) -> Future[str]:
        # GNOME retains recent focus transitions, so worker scheduling does not
        # change which window was focused when the daemon received key-down.
        at = str(time.monotonic_ns() // 1000)
        result: Future[str] = Future()

        def capture() -> None:
            reply = call("Capture", at)
            match = re.fullmatch(r"\('([a-f0-9-]+:[0-9]+)',\)", reply)
            result.set_result(match[1] if match else "")

        threading.Thread(target=capture, daemon=True, name="focus-capture").start()
        return result

    def restore(self, destination: Future[str] | None) -> bool:
        if destination is None:
            return False
        try:
            token = destination.result(timeout=2)
        except Exception:
            # A capture failure must never turn into a paste to current focus.
            return False
        return bool(token) and call("Restore", token) == "(true,)"
