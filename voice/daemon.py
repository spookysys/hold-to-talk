"""The voice daemon: one loop that waits on the key helper and the microphone.

While nothing happens, the loop sleeps in select() without a timeout, so it
costs no CPU. The microphone is open only while the launch key is down, and
AFTER_S longer after a dictation.

A dictation that begins while the model is not loaded starts the load. The
engine takes the audio in order after the load, so the text follows later.
"""

from __future__ import annotations

import os
import selectors
import signal
import socket
import subprocess
import threading
import time
import tomllib
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import hold as H
from . import output
from .engine import LOADED, RATE, Engine, Result
from .hud import Hud
from .focus import Focus

CONFIG = Path.home() / ".config/voice/config.toml"
# The language chosen last, so that the next dictation starts with it.
LANGUAGE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "voice/language"
# The microphone records this long after the release. People release the key
# on their last syllable, and pw-record loses up to 0.1 s when it stops.
AFTER_S = 0.3
SWAP_CHECK_S = 30.0
# Once this much of an idle model is in swap, free it for the other programs.
MAX_IDLE_SWAP_KB = 128 * 1024
MODEL = "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11"


@dataclass
class Config:
    model: str = str(Path.home() / ".local/share/voice/models" / MODEL)
    threads: int = 2             # the fastest on a CPU with few fast cores
    # Super+Space, Left and Right choose among these during a dictation.
    # A code such as "de" or "en", or "auto" for the model's own guess.
    languages: list[str] = field(default_factory=lambda: ["auto"])
    hold_ms: int = 300           # must match overload_tap_timeout in keyd
    idle_minutes: float = 30     # then the model is freed
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


def chosen_language(languages: list[str]) -> str:
    """The language chosen last, if it is still in the list; else the first one."""
    try:
        language = LANGUAGE.read_text().strip()
    except OSError:
        language = ""
    return language if language in languages else languages[0]


def remember_language(language: str) -> None:
    try:
        LANGUAGE.parent.mkdir(parents=True, exist_ok=True)
        LANGUAGE.write_text(language + "\n")
    except OSError as error:
        log(f"cannot remember the language: {error}")


def swapped_kb() -> int:
    try:
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("VmSwap:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return 0


HINT = "speak now"         # the caption text while the dictation has no text yet

# The output results that the user must know about: the log line and the caption message.
NOTICES = {
    "copied": ("canceled; the text is in the clipboard",
               "canceled - text in clipboard"),
    "changed": ("the original window is gone or did not take the focus; "
                "the text is in the clipboard",
                "window changed - text in clipboard"),
    "not-loaded": ("GNOME Shell has not loaded the window-restore extension; "
                   "log out and back in. The text is in the clipboard",
                   "log out and back in to restore windows - text in clipboard"),
    "failed": ("the clipboard or voice-keys failed; paste was not confirmed",
               "insert failed - see voice log"),
}
MESSAGE_S = 5


@dataclass
class PendingText:
    text: str
    destination: Future[str] | None
    paste: bool = True         # False: the dictation was canceled; only copy it


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
        self.output_busy = False
        self.paste_request: Future[str] | None = None
        self.paste_sent = False
        self.paste_at: float | None = None
        self.focus = Focus()
        self.destination: Future[str] | None = None
        self.destinations: dict[int, Future[str] | None] = {}
        self.paste_destination: Future[str] | None = None
        self.paste_restoring = False
        self.paste_restored = False
        self.waiting: list[PendingText] = []     # texts that are ready while the key is down
        self.stop_at: float | None = None   # the release came; the microphone runs until then
        self.writing: set[int] = set()   # dictations that wait for their final text
        self.canceled: set[int] = set()  # their text goes to the clipboard only
        self.text = ""                   # of the newest dictation, as the box shows it
        self.hidden = False              # the newest dictation's box stays hidden until its end
        self.model = "unloaded"          # or "loading", "loaded"
        self.load_since = 0.0
        self.unload_at: float | None = None
        self.unload_requested = False    # `voice unload` came; free at the next idle moment
        self.swap_check_at: float | None = None
        self.level_at = 0.0
        self.hud = Hud()
        self.wake_r, self.wake_w = os.pipe()
        self.events: list = []           # (function, arguments) from the engine thread
        self.selector.register(self.wake_r, selectors.EVENT_READ, "wake")
        LOADED.unlink(missing_ok=True)       # left by a daemon that crashed
        self.language = chosen_language(config.languages)
        self.engine = Engine(config.model, config.threads, self.language,
                             lambda result: self._post(self._text, result),
                             lambda ok: self._post(self._loaded, ok),
                             marker=LOADED)
        self.engine.start()
        signal.signal(signal.SIGUSR1, self._unload_now)   # `voice unload`

    def _unload_now(self, signum, frame) -> None:
        self.unload_requested = True         # kept until the model is freed
        self.unload_at = 0.0                 # the loop frees the model at its next pass
        os.write(self.wake_w, b"u")          # and select() must return for that

    # Events arrive in worker threads; the loop handles them.

    def _post(self, function, *arguments) -> None:
        self.events.append((function, arguments))
        os.write(self.wake_w, b"e")

    def run(self) -> None:
        log(f"model {Path(self.config.model).name}, {self.config.threads} threads, "
            f"language {self.language} of {', '.join(self.config.languages)}")
        while True:
            now = time.monotonic()
            if self.keys is None and now >= self.connect_at:
                self._connect()
            for _ in self.hold.tick(now):
                self._begin()
            if self.paste_at is not None and now >= self.paste_at:
                log("voice-keys did not confirm paste; check that the helper is up to date")
                # Close this connection so a late reply cannot confirm a later paste.
                self._disconnect_keys()
            if self.stop_at is not None and now >= self.stop_at:
                self._stop()
            if self.unload_at is not None and now >= self.unload_at:
                self.unload_at = None
                if self._busy():         # the end of the hold or dictation schedules it again
                    continue
                self._free_model()
            if self.swap_check_at is not None and now >= self.swap_check_at:
                self._check_swap(now)

            # Read key events before worker results from the same selector pass.
            ready = self.selector.select(self._timeout(time.monotonic()))
            for key, _ in sorted(ready, key=lambda item: item[0].data != "keys"):
                if key.data == "keys":
                    self._read_keys()
                elif key.data == "audio":
                    self._read_audio()
                elif key.data == "wake":
                    os.read(self.wake_r, 4096)
                    while self.events:
                        function, arguments = self.events.pop(0)
                        function(*arguments)

    def _free_model(self) -> None:
        self.unload_requested = False
        self.unload_at = None
        self.swap_check_at = None
        self.model = "unloaded"
        self.engine.unload()
        self.hud.close()

    def _check_swap(self, now: float) -> None:
        if self.model != "loaded":
            self.swap_check_at = None
        elif self._busy():
            self.swap_check_at = now + SWAP_CHECK_S
        elif swapped_kb() >= MAX_IDLE_SWAP_KB:
            log("memory pressure swapped the model out; freeing it")
            self._free_model()
        else:
            self.swap_check_at = now + SWAP_CHECK_S

    def _busy(self) -> bool:
        return (self.hold.state != H.IDLE or self.listening or bool(self.writing)
                or self.stop_at is not None)

    def _timeout(self, now: float) -> float | None:
        times = [self.hold.deadline(), self.unload_at, self.swap_check_at,
                 self.stop_at, self.paste_at]
        if self.keys is None:
            times.append(self.connect_at)
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
        sock.setblocking(False)
        self.keys = sock
        self.keys_buffer = b""
        self.selector.register(sock, selectors.EVENT_READ, "keys")

    def _read_keys(self) -> None:
        if self.keys is None:            # it went away earlier in this select() pass
            return
        try:
            data = self.keys.recv(4096)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._disconnect_keys()
            return
        self.keys_buffer += data
        *lines, self.keys_buffer = self.keys_buffer.split(b"\n")
        for line in lines:
            name = line.decode(errors="replace").strip()
            if name == "pasted":
                self._paste_done("pasted")
            elif name == "held":
                self._paste_done("held")
            elif name == "failed":
                self._paste_done("failed")
            elif name in ("down", "key", "up"):
                self._act(self.hold.event(name, time.monotonic()))
            elif name in ("next", "previous"):
                self._choose(1 if name == "next" else -1)
        self._send_paste()
        self._write_waiting()

    def _disconnect_keys(self) -> None:
        if self.keys is not None:
            log("voice-keys went away")
            self.selector.unregister(self.keys)
            self.keys.close()
            self.keys = None
        self.connect_at = time.monotonic() + 1
        self._act(self.hold.event("lost", time.monotonic()))
        self._paste_done("failed")
        self._write_waiting()

    def _paste(self, pending: PendingText) -> str:
        """The output worker waits while the event loop processes keys."""
        request: Future[str] = Future()
        self._post(self._prepare_paste, request, pending.destination)
        result = request.result()
        if result == "pasted":
            log(f"sent {len(pending.text)} characters to the focused field")
        return result

    def _prepare_paste(self, request: Future[str], destination: Future[str] | None = None) -> None:
        self.paste_request = request
        self.paste_destination = destination
        self.paste_restored = False
        # A key can arrive after select() returned the worker notification.
        self._read_keys()
        self._send_paste()

    def _send_paste(self) -> None:
        if self.paste_request is None or self.paste_sent:
            return
        if self.keys is None:
            self._paste_done("failed")
            return
        if self.hold.state != H.IDLE:
            self._paste_done("held")
            return
        if not self.paste_restored:
            if not self.paste_restoring:
                self.paste_restoring = True
                threading.Thread(target=self._restore, args=(self.paste_request, self.paste_destination),
                                 daemon=True, name="focus-restore").start()
            return
        if not self._send_keys(b"paste\n"):
            self._paste_done("failed")
            return
        self.paste_sent = True
        self.paste_at = time.monotonic() + 3

    def _send_keys(self, request: bytes) -> bool:
        if self.keys is None:
            return False
        try:
            self.keys.sendall(request)
        except OSError:
            return False
        return True

    def _restore(self, request: Future[str], destination: Future[str] | None) -> None:
        try:
            result = self.focus.restore(destination)
        except Exception:
            result = "changed"
        self._post(self._restored, request, result)

    def _restored(self, request: Future[str], result: str) -> None:
        if self.paste_request is not request:
            return                      # a new hold or disconnect canceled this request
        self.paste_restoring = False
        if result != "restored":
            self._paste_done(result)
            return
        self.paste_restored = True
        self._read_keys()               # a hold during restoration must still defer paste
        self._send_paste()

    def _paste_done(self, result: str) -> None:
        self.paste_sent = False
        self.paste_at = None
        self.paste_restoring = False
        self.paste_restored = False
        self.paste_destination = None
        request, self.paste_request = self.paste_request, None
        if request is not None:
            request.set_result(result)

    # Actions of the hold.

    def _act(self, actions: list[str]) -> None:
        if H.RECORD in actions:
            self.destination = self.focus.capture()
        for action in actions:
            self._stop()                 # a new hold ends the one before it at once
            {H.RECORD: self._record, H.BEGIN: self._begin, H.DISCARD: self._discard,
             H.FINISH: self._finish, H.CANCEL: self._cancel}[action]()

    def _record(self) -> None:
        self.recorder = subprocess.Popen(
            ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "--raw", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
        os.set_blocking(self.recorder.stdout.fileno(), False)
        self.selector.register(self.recorder.stdout, selectors.EVENT_READ, "audio")
        self.before = bytearray()

    def _begin(self) -> None:
        self.active_since = time.monotonic()
        self.unload_at = None
        self.swap_check_at = None
        if self.model == "unloaded":
            # The engine loads first, then takes this dictation's audio.
            self.model, self.load_since = "loading", time.monotonic()
            self.engine.load()
            log("loading the model; the text follows when it is ready")
        self.dictation += 1
        self.destinations[self.dictation] = self.destination
        self.listening = True
        self.text = ""
        self.hidden = False
        self.engine.begin(self.dictation, bytes(self.before))
        if len(self.config.languages) > 1:
            self._send_keys(b"select\n")    # Space, Left and Right choose the language
        if self.model == "loading":
            self._show_load(HINT)
        else:
            self._show("listening", hint=HINT)
        log(f"listening, language {self.language}")

    def _choose(self, step: int) -> None:
        """Super+Space, Left or Right during the dictation: another language."""
        languages = self.config.languages
        if not self.listening or len(languages) < 2:
            return
        index = languages.index(self.language) if self.language in languages else 0
        self.language = languages[(index + step) % len(languages)]
        remember_language(self.language)
        self.engine.choose(self.dictation, self.language)
        log(f"language {self.language}")
        if self.model == "loading":
            self._show_load(HINT)
        else:
            self._show("listening", self.text, hint=HINT)

    def _show(self, state: str, text: str = "", **extra) -> None:
        """Show a state of the dictation, with the languages to choose from."""
        if len(self.config.languages) > 1:
            extra.update(languages=self.config.languages, language=self.language)
        self.hud.show(state, text, **extra)

    def _show_load(self, hint: str = "") -> None:
        self._show("loading", hint=hint, eta=round(self.engine.load_s, 1),
                   elapsed=round(time.monotonic() - self.load_since, 1))

    def _loaded(self, ok: bool) -> None:
        if self.model != "loading":      # `voice unload` came during the load
            return
        if not ok:
            self.model = "unloaded"
            log("the model failed to load; see the error above")
            return                       # the engine ends the dictation without text
        self.model = "loaded"
        if self.listening and self.stop_at is None:
            self._show("listening", self.text, hint=HINT)
        elif not self.hidden and (self.stop_at is not None or self.dictation in self.writing):
            self._show("writing", self.text)
        self._schedule_unload()

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
        """A tap or a quick key combination: no dictation began."""
        self._stop_recorder()
        if self.unload_at is None:       # a tap or shortcut does not keep the model
            self._schedule_unload()
        # The box of an earlier dictation stays until its text is done.
        if self.model != "loading" and not self.writing and self.stop_at is None:
            self.hud.hide()

    def _finish(self) -> None:
        if not self.listening:
            return
        self.stop_at = time.monotonic() + AFTER_S
        log(f"stopped listening after {time.monotonic() - self.active_since:.1f} s")
        if self.model == "loading":
            self._show_load()
        elif self.text:
            self._show("writing", self.text)
        else:
            # Most likely nothing was said. The decode goes on, because a short
            # word has no text yet at the release, and a late word is pasted.
            self.hidden = True
            self.hud.hide()

    def _cancel(self) -> None:
        """Another key, or voice-keys went away: copy the text, but do not paste it."""
        if not self.listening:
            return
        log(f"canceled after {time.monotonic() - self.active_since:.1f} s; "
            "the text goes to the clipboard only")
        self.canceled.add(self.dictation)
        self.stop_at = time.monotonic()  # the key ends the speech; no time after it
        self._stop()
        self.hidden = True
        self.hud.hide()                  # the shortcut of the key goes on

    def _stop(self) -> None:
        """End the dictation whose key went up; the microphone has run AFTER_S longer."""
        if self.stop_at is None:
            return
        self.stop_at = None
        self.engine.feed(self.dictation, self._stop_recorder())
        self.engine.end(self.dictation)
        self.listening = False
        self.writing.add(self.dictation)

    def _read_audio(self) -> None:
        if self.recorder is None:        # stopped earlier in this select() pass
            return
        try:
            data = self.recorder.stdout.read()
        except OSError:
            data = None
        if data == b"":                  # pw-record ended by itself
            log("the microphone stopped")
            self._stop_recorder()
            return
        if not data:
            return
        if self.listening:
            self.engine.feed(self.dictation, data)
            now = time.monotonic()
            if now - self.level_at >= 0.1 and self.stop_at is None and self.model == "loaded":
                self.level_at = now
                samples = np.frombuffer(data[: len(data) // 2 * 2], np.int16) / 32768
                db = 10 * np.log10(max(float(np.mean(samples ** 2)), 1e-12))
                self.hud.level(min(1.0, max(0.0, (db + 60) / 50)))   # -60 dB to -10 dB
        else:
            self.before += data          # the start of the first word, before the threshold

    # Text from the engine.

    def _text(self, result: Result) -> None:
        if result.dictation == self.dictation:
            self.text = result.text
            if self.listening and self.stop_at is None and not result.final:
                self._show("listening", result.text, hint=HINT)
            elif (not result.final and not self.hidden
                    and (self.stop_at is not None or result.dictation in self.writing)):
                self._show("writing", result.text)
        if not result.final:
            return
        if result.dictation == self.dictation and self.listening:
            # The engine can fail before release. Its final result ends recording.
            self.stop_at = None
            self._stop_recorder()
            self.listening = False
            self.hold.event("key", time.monotonic())
        destination = self.destinations.pop(result.dictation, None)
        paste = result.dictation not in self.canceled
        self.canceled.discard(result.dictation)
        self.writing.discard(result.dictation)
        if result.dictation == self.dictation and not self.listening:
            self.hud.hide()
        if not result.text:
            log("heard nothing, so nothing is written")
        else:
            log(f"heard: {result.text}")
            self.waiting.append(PendingText(result.text, destination, paste))
            if self.hold.state != H.IDLE:
                log("the text waits for the release")
            self._write_waiting()
        self._schedule_unload()

    def _write_waiting(self) -> None:
        """Write the ready texts, but only while the launch key is up.

        A paste while the key is down would reach the field before the
        release, and keyd would send it with Super held.
        """
        if self.hold.state != H.IDLE or self.output_busy or not self.waiting:
            return
        pending = self.waiting.pop(0)
        self.output_busy = True
        threading.Thread(target=self._insert, args=(pending,), daemon=True,
                         name="output").start()

    def _insert(self, pending: PendingText) -> None:
        text = pending.text + " "
        try:
            if pending.paste:
                result = output.insert(text, lambda: self._paste(pending))
            else:
                result = "copied" if output.copy(text) else "failed"
        except Exception as error:
            log(f"insert failed: {error}")
            result = "failed"
        self._post(self._inserted, result, pending)

    def _inserted(self, result: str, pending: PendingText) -> None:
        self.output_busy = False
        if result == "held":
            self.waiting.insert(0, pending)
        elif result in NOTICES:
            line, message = NOTICES[result]
            log(line)
            self.hud.show(message, seconds=MESSAGE_S)
        self._write_waiting()

    def _schedule_unload(self) -> None:
        if not self.writing and not self.listening:
            now = time.monotonic()
            self.unload_at = now + (0 if self.unload_requested else self.config.idle_minutes * 60)
            if self.model == "loaded":
                self.swap_check_at = now + SWAP_CHECK_S


def main() -> int:
    Daemon(Config.load()).run()
    return 0
