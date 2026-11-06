"""The talk daemon: one loop that waits on the key helper and the microphone.

While nothing happens, the loop sleeps in select() without a timeout, so it
costs no CPU. The microphone is open only while the launch key is down.
"""

from __future__ import annotations

import os
import selectors
import socket
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import hold as H
from . import output
from .engine import Engine, Result
from .hud import Hud
from .segment import Segmenter

CONFIG = Path.home() / ".config/talk/config.toml"


@dataclass
class Config:
    model: str = str(Path.home() / ".local/share/talk/models/ggml-small-q8_0.bin")
    threads: int = max(1, (os.cpu_count() or 2) - 2)
    languages: list[str] = field(default_factory=lambda: ["de", "en"])
    hold_ms: int = 300           # must match overload_tap_timeout in keyd
    idle_minutes: float = 5      # then the model is freed
    max_seconds: float = 120
    socket: str = "/run/talk-keys.sock"

    @classmethod
    def load(cls) -> "Config":
        try:
            data = tomllib.loads(CONFIG.read_text())
        except FileNotFoundError:
            data = {}
        known = cls.__dataclass_fields__
        unknown = set(data) - set(known)
        if unknown:
            raise SystemExit(f"talk: unknown settings in {CONFIG}: {', '.join(sorted(unknown))}")
        return cls(**data)


def log(message: str) -> None:
    print(f"talk: {message}", flush=True)


class Daemon:
    def __init__(self, config: Config):
        self.config = config
        self.hold = H.Hold(config.hold_ms / 1000)
        self.selector = selectors.DefaultSelector()
        self.keys: socket.socket | None = None
        self.keys_buffer = b""
        self.connect_at = 0.0
        self.recorder: subprocess.Popen | None = None
        self.segmenter: Segmenter | None = None
        self.dictation = 0               # the newest dictation
        self.listening = False           # it still records
        self.active_since = 0.0
        self.texts: dict[int, list[str]] = {}   # dictations that wait for text
        self.unload_at: float | None = None
        self.hud = Hud()
        self.wake_r, self.wake_w = os.pipe()
        self.results: list[Result] = []
        self.selector.register(self.wake_r, selectors.EVENT_READ, "wake")
        self.engine = Engine(config.model, config.threads, config.languages, self._result)
        self.engine.start()

    # Results arrive in the engine thread; the loop handles them.

    def _result(self, result: Result) -> None:
        self.results.append(result)
        os.write(self.wake_w, b"r")

    def run(self) -> None:
        log(f"model {Path(self.config.model).name}, {self.config.threads} threads, "
            f"languages {', '.join(self.config.languages)}")
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
                        self._finished(self.results.pop(0))

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
                log(f"waiting for talk-keys at {self.config.socket}: {error}")
            self.connect_at = time.monotonic() + 2
            return
        log("connected to talk-keys")
        self.keys = sock
        self.keys_buffer = b""
        self.selector.register(sock, selectors.EVENT_READ, "keys")

    def _read_keys(self) -> None:
        data = self.keys.recv(4096)
        if not data:
            log("talk-keys went away")
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
            ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", "--raw", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        os.set_blocking(self.recorder.stdout.fileno(), False)
        self.selector.register(self.recorder.stdout, selectors.EVENT_READ, "audio")
        self.segmenter = Segmenter()

    def _begin(self) -> None:
        self.dictation += 1
        self.listening = True
        self.active_since = time.monotonic()
        self.texts[self.dictation] = []
        self.unload_at = None
        self.engine.prepare(self.dictation)
        self.hud.show("listening")

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
        self.segmenter = None
        if self.listening:
            self.listening = False
            self.engine.drop(self.dictation)
            del self.texts[self.dictation]
        self.hud.hide()
        self._schedule_unload()

    def _finish(self) -> None:
        rest = self._stop_recorder()
        self.listening = False
        segmenter, self.segmenter = self.segmenter, None
        for phrase in segmenter.feed(rest):
            self.engine.submit(self.dictation, phrase)
        self.engine.submit(self.dictation, segmenter.flush(), last=True)
        if segmenter.speech_s >= 0.3:
            self.hud.show("writing", " ".join(self.texts[self.dictation]))

    def _read_audio(self) -> None:
        try:
            data = self.recorder.stdout.read()
        except OSError:
            data = None
        if not data:
            return
        for phrase in self.segmenter.feed(data):
            if self.hold.state == H.ACTIVE:
                self.engine.submit(self.dictation, phrase)

    # Text from the engine.

    def _finished(self, result: Result) -> None:
        texts = self.texts.get(result.dictation)
        if texts is None:
            return                       # canceled
        if result.text:
            texts.append(result.text)
            if not result.last and result.dictation == self.dictation and self.listening:
                self.hud.show("listening", " ".join(texts))
        if result.last:
            text = " ".join(self.texts.pop(result.dictation))
            if result.dictation == self.dictation and not self.listening:
                self.hud.hide()
            if text:
                log(f"insert {len(text)} characters")
                if not output.insert(text + " ", self._paste):
                    log("the clipboard failed")
            self._schedule_unload()

    def _schedule_unload(self) -> None:
        if not self.texts and not self.listening:
            self.unload_at = time.monotonic() + self.config.idle_minutes * 60


def main() -> int:
    Daemon(Config.load()).run()
    return 0
