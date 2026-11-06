"""The voice daemon: one loop that waits on the key helper and the microphone.

While nothing happens, the loop sleeps in select() without a timeout, so it
costs no CPU. The microphone is open only while the launch key is down.
"""

from __future__ import annotations

import os
import selectors
import signal
import socket
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import hold as H
from . import output
from .engine import LOADED, RATE, Engine, Result
from .hud import Hud

CONFIG = Path.home() / ".config/voice/config.toml"
MODEL = "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11"


@dataclass
class Config:
    model: str = str(Path.home() / ".local/share/voice/models" / MODEL)
    threads: int = 2             # the fastest on a CPU with few fast cores
    language: str = "auto"       # or a code such as "de" or "en"
    hold_ms: int = 300           # must match overload_tap_timeout in keyd
    idle_minutes: float = 30     # then the model is freed
    max_seconds: float = 120
    socket: str = "/run/voice-keys.sock"

    @classmethod
    def load(cls) -> "Config":
        try:
            data = tomllib.loads(CONFIG.read_text())
        except FileNotFoundError:
            data = {}
        known = cls.__dataclass_fields__
        unknown = set(data) - set(known)
        if unknown:
            raise SystemExit(f"voice: unknown settings in {CONFIG}: {', '.join(sorted(unknown))}")
        return cls(**data)


def log(message: str) -> None:
    print(f"voice: {message}", flush=True)


class Daemon:
    def __init__(self, config: Config):
        self.config = config
        self.hold = H.Hold(config.hold_ms / 1000)
        self.selector = selectors.DefaultSelector()
        self.keys: socket.socket | None = None
        self.keys_buffer = b""
        self.connect_at = 0.0
        self.recorder: subprocess.Popen | None = None
        self.before = bytearray()        # audio from the key-down to the start of the dictation
        self.dictation = 0               # the newest dictation
        self.listening = False           # it still records
        self.active_since = 0.0
        self.writing: set[int] = set()   # dictations that wait for their final text
        self.text = ""                   # of the newest dictation, as the box shows it
        self.loading = False             # the model loads for the newest dictation
        self.unload_at: float | None = None
        self.level_at = 0.0
        self.hud = Hud()
        self.wake_r, self.wake_w = os.pipe()
        self.results: list[Result] = []
        self.selector.register(self.wake_r, selectors.EVENT_READ, "wake")
        LOADED.unlink(missing_ok=True)       # left by a daemon that crashed
        self.engine = Engine(config.model, config.threads, config.language, self._result,
                             marker=LOADED)
        self.engine.start()
        signal.signal(signal.SIGUSR1, self._unload_now)   # `voice unload`

    def _unload_now(self, signum, frame) -> None:
        self.unload_at = 0.0                 # the loop frees the model at its next pass
        os.write(self.wake_w, b"u")          # and select() must return for that

    # Results arrive in the engine thread; the loop handles them.

    def _result(self, result: Result) -> None:
        self.results.append(result)
        os.write(self.wake_w, b"r")

    def run(self) -> None:
        log(f"model {Path(self.config.model).name}, {self.config.threads} threads, "
            f"language {self.config.language}")
        while True:
            now = time.monotonic()
            if self.keys is None and now >= self.connect_at:
                self._connect()
            for _ in self.hold.tick(now):
                self._begin()
            if self.hold.state == H.ACTIVE and now - self.active_since > self.config.max_seconds:
                self._act(self.hold.event("limit", now))
            if self.unload_at is not None and now >= self.unload_at:
                self.unload_at = None
                self.engine.unload()
                self.hud.close()

            for key, _ in self.selector.select(self._timeout(time.monotonic())):
                if key.data == "keys":
                    self._read_keys()
                elif key.data == "audio":
                    self._read_audio()
                elif key.data == "wake":
                    os.read(self.wake_r, 4096)
                    while self.results:
                        self._text(self.results.pop(0))

    def _timeout(self, now: float) -> float | None:
        times = [self.hold.deadline(), self.unload_at]
        if self.keys is None:
            times.append(self.connect_at)
        if self.hold.state == H.ACTIVE:
            times.append(self.active_since + self.config.max_seconds)
        times = [t for t in times if t is not None]
        return max(0.0, min(times) - now) if times else None

    # The key helper.

    def _connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.config.socket)
        except OSError as error:
            sock.close()
            if self.connect_at == 0.0:
                log(f"waiting for voice-keys at {self.config.socket}: {error}")
            self.connect_at = time.monotonic() + 2
            return
        log("connected to voice-keys")
        self.keys = sock
        self.keys_buffer = b""
        self.selector.register(sock, selectors.EVENT_READ, "keys")

    def _read_keys(self) -> None:
        data = self.keys.recv(4096)
        if not data:
            log("voice-keys went away")
            self.selector.unregister(self.keys)
            self.keys.close()
            self.keys = None
            self.connect_at = time.monotonic() + 1
            self._act(self.hold.event("lost", time.monotonic()))
            return
        self.keys_buffer += data
        *lines, self.keys_buffer = self.keys_buffer.split(b"\n")
        for line in lines:
            name = line.decode(errors="replace").strip()
            if name in ("down", "key", "up"):
                self._act(self.hold.event(name, time.monotonic()))

    def _paste(self) -> None:
        if self.keys is not None:
            self.keys.sendall(b"paste\n")

    # Actions of the hold.

    def _act(self, actions: list[str]) -> None:
        for action in actions:
            {H.RECORD: self._record, H.BEGIN: self._begin,
             H.DISCARD: self._discard, H.FINISH: self._finish}[action]()

    def _record(self) -> None:
        self.recorder = subprocess.Popen(
            ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "--raw", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        os.set_blocking(self.recorder.stdout.fileno(), False)
        self.selector.register(self.recorder.stdout, selectors.EVENT_READ, "audio")
        self.before = bytearray()

    def _begin(self) -> None:
        self.dictation += 1
        self.listening = True
        self.active_since = time.monotonic()
        self.text = ""
        self.unload_at = None
        self.engine.begin(self.dictation, bytes(self.before))
        self.loading = not LOADED.exists()
        if self.loading:
            self.hud.show("loading", eta=round(self.engine.load_s, 1))
        else:
            self.hud.show("listening")
        log("listening")

    def _stop_recorder(self) -> bytes:
        recorder, self.recorder = self.recorder, None
        if recorder is None:
            return b""
        self.selector.unregister(recorder.stdout)
        recorder.terminate()
        try:
            recorder.wait(timeout=2)
        except subprocess.TimeoutExpired:
            recorder.kill()
            recorder.wait()
        try:
            rest = recorder.stdout.read() or b""
        except OSError:
            rest = b""
        recorder.stdout.close()
        return rest

    def _discard(self) -> None:
        self._stop_recorder()
        if self.listening:
            log("canceled: another key, or voice-keys went away")
            self.listening = False
            self.engine.drop(self.dictation)
        self.hud.hide()
        self._schedule_unload()

    def _finish(self) -> None:
        self.engine.feed(self.dictation, self._stop_recorder())
        self.engine.end(self.dictation)
        self.listening = False
        self.writing.add(self.dictation)
        log(f"stopped listening after {time.monotonic() - self.active_since:.1f} s")
        if not self.loading:             # else the loading bar stays until the model is ready
            self.hud.show("writing", self.text)

    def _read_audio(self) -> None:
        try:
            data = self.recorder.stdout.read()
        except OSError:
            data = None
        if not data:
            return
        if self.listening:
            self.engine.feed(self.dictation, data)
            now = time.monotonic()
            if now - self.level_at >= 0.1 and not self.loading:
                self.level_at = now
                samples = np.frombuffer(data[: len(data) // 2 * 2], np.int16) / 32768
                db = 10 * np.log10(max(float(np.mean(samples ** 2)), 1e-12))
                self.hud.level(min(1.0, max(0.0, (db + 60) / 50)))   # -60 dB to -10 dB
        else:
            self.before += data          # the start of the first word, before the threshold

    # Text from the engine.

    def _text(self, result: Result) -> None:
        if result.dictation == self.dictation:
            self.loading = False
            self.text = result.text
            if self.listening:
                self.hud.show("listening", result.text)
            elif not result.final:
                self.hud.show("writing", result.text)
        if not result.final:
            return
        self.writing.discard(result.dictation)
        if result.dictation == self.dictation and not self.listening:
            self.hud.hide()
        if not result.text:
            log("heard nothing, so nothing is written")
        else:
            log(f"heard: {result.text}")
            if output.insert(result.text + " ", self._paste):
                log(f"wrote {len(result.text)} characters into the focused field")
            else:
                log("the clipboard failed, so nothing is written")
        self._schedule_unload()

    def _schedule_unload(self) -> None:
        if not self.writing and not self.listening:
            self.unload_at = time.monotonic() + self.config.idle_minutes * 60


def main() -> int:
    Daemon(Config.load()).run()
    return 0
