"""voice: hold the launch key and speak; the text goes into the focused field.

Commands:
    voice start      start listening to the launch key
    voice stop       stop listening to the launch key
    voice restart    restart, for example after a change of the settings
    voice unload     free the model now, instead of after the idle minutes
    voice status     running or not, model loaded or not, memory in use
    voice log        follow what voice hears and how long it takes

    voice transcribe FILE   a 16 kHz mono WAV through the live path, for tests
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys

SERVICE = "voice.service"


def systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args, SERVICE],
                          capture_output=True, text=True)


def main_pid() -> int:
    return int(systemctl("show", "-p", "MainPID", "--value").stdout.strip() or 0)


def control(action: str) -> int:
    result = systemctl(action)
    if result.returncode:
        print(f"voice: {action} failed: {result.stderr.strip()}", file=sys.stderr)
        return 1
    return status()


def memory_kb(pid: int) -> tuple[int, int]:
    rss = swap = 0
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss = int(line.split()[1])
                elif line.startswith("VmSwap:"):
                    swap = int(line.split()[1])
    except OSError:
        pass
    return rss, swap


def unload() -> int:
    import time
    from .engine import LOADED

    pid = main_pid()
    if not pid:
        print("voice: not running")
        return 1
    if not LOADED.exists():
        print("voice: the model is not loaded")
        return 0
    os.kill(pid, signal.SIGUSR1)       # the daemon frees the model
    deadline = time.monotonic() + 10
    while LOADED.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    if LOADED.exists():
        print("voice: unload requested; waiting for active work to finish")
        return 0
    print("voice: model freed")
    return 0


def status() -> int:
    from .engine import LOADED

    state = systemctl("is-active").stdout.strip()
    pid = main_pid()
    if state != "active" or not pid:
        print(f"voice: {state}")
        return 0 if state == "inactive" else 1
    rss, swap = memory_kb(pid)
    total_mb = (rss + swap) // 1024
    swap_mb = swap // 1024
    model = "loaded" if LOADED.exists() else "not loaded"
    detail = f"{total_mb} MB ({swap_mb} MB swapped)" if swap_mb else f"{total_mb} MB"
    print(f"voice: running, model {model}, {detail}")
    from . import focus
    try:
        focus.call("Capture", "0")
    except focus.NotLoaded:
        print("voice: GNOME Shell has not loaded the window-restore extension; "
              "log out and back in")
    return 0


def log() -> int:
    try:
        return subprocess.run(["journalctl", "--user", "-u", SERVICE, "-f", "-n", "20",
                               "-o", "cat"]).returncode
    except KeyboardInterrupt:
        return 0


def transcribe(path: str) -> int:
    import queue
    import time
    import wave

    from .daemon import Config
    from .engine import RATE, Engine

    config = Config.load()
    with wave.open(path) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (RATE, 1, 2):
            print("voice: the file must be 16 kHz, mono, 16 bit", file=sys.stderr)
            return 2
        data = w.readframes(w.getnframes())
    results: queue.Queue = queue.Queue()
    loaded: queue.Queue = queue.Queue()
    engine = Engine(config.model, config.threads, config.language, results.put,
                    on_loaded=loaded.put, languages=config.languages)
    engine.start()
    engine.load()
    if not loaded.get():
        print("voice: the model failed to load", file=sys.stderr)
        return 1
    engine.begin(1, b"")
    piece = RATE // 10 * 2          # 100 ms, as the microphone delivers it
    for start in range(0, len(data), piece):
        engine.feed(1, data[start:start + piece])
    # As at a release: the audio so far is decoded, only the end is left.
    while not engine.jobs.empty():
        time.sleep(0.01)
    engine.end(1)
    started = time.monotonic()
    while not (result := results.get()).final:
        pass
    print(f"voice: {time.monotonic() - started:.2f} s after the end of the file", file=sys.stderr)
    print(result.text)
    return 0


def main(argv: list[str]) -> int:
    args = argv[1:]
    commands = {
        ("start",): lambda: control("start"),
        ("stop",): lambda: control("stop"),
        ("restart",): lambda: control("restart"),
        ("unload",): unload,
        ("status",): status,
        ("log",): log,
    }
    if tuple(args) in commands:
        return commands[tuple(args)]()
    if args == ["run"]:                         # the service runs this
        from .daemon import main as run
        return run()
    if args == ["hud"]:                         # the daemon starts this
        from .hud import run as hud
        return hud()
    if len(args) == 2 and args[0] == "transcribe":
        return transcribe(args[1])
    print(__doc__.strip())
    return 0 if not args or args[0] in ("help", "-h", "--help") else 2


def entry() -> None:
    sys.exit(main(sys.argv))


if __name__ == "__main__":
    entry()
