"""Streaming speech to text with NVIDIA Nemotron 3.5 ASR, in one worker thread.

The model is a streaming transducer: it takes the audio in pieces as they
arrive, processes each piece once, and the text grows with the speech. At the
release only the last piece is left, so the text is ready a few hundred
milliseconds later. A transducer writes text only for sounds it hears, so it
does not invent text for silence.

The model takes about 800 MB of RAM and a few seconds to load. The daemon asks
for the load and frees the model after some idle minutes.

The model takes the language as an input, a code such as "en", or "auto" for
its own guess; it does not report a guess. The user chooses the language. A
change during a dictation decodes the audio so far again, so the engine keeps
the dictation's audio: 32 kB per second, until the dictation ends.

Measured on an Intel i3-1215U, on mains power under load, for 30 s of speech:
two threads decode in 0.46 s per second of speech and use 0.81 CPU seconds.
One thread takes 0.62 s; three take 0.40 s but use more CPU.
"""

from __future__ import annotations

import ctypes
import gc
import glob
import os
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RATE = 16000

# onnxruntime settings: sherpa-onnx reads this file when the provider text is
# "cpu:FILE", and passes each SessionConfig.* entry to every session. The file
# lets idle worker threads sleep instead of spin. With spinning, two decoding
# threads kept four cores busy.
SESSION_CONFIG = Path(__file__).with_name("onnxruntime.conf")

# Exists while the model is in memory; `voice status` reads it.
LOADED = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "voice-model-loaded"

# Silence after the speech, so that the model finishes the last word.
# Nothing goes in front: digital silence there made the language detection
# worse. The microphone audio from the key-down on is the lead instead; a
# recording that began with speech lost its first word.
TAIL_S = 0.5


@dataclass
class Result:
    dictation: int
    text: str
    final: bool          # the dictation is over; this is its text


def trim_memory() -> None:
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)   # glibc keeps freed memory otherwise
    except OSError:
        pass


def pcm(data: bytes) -> np.ndarray:
    """16-bit mono samples as the float samples that the model takes."""
    return np.frombuffer(data[: len(data) // 2 * 2], np.int16).astype(np.float32) / 32768


class Engine(threading.Thread):
    def __init__(self, model: str, threads: int, language: str, on_result, on_loaded,
                 marker: Path | None = None):
        super().__init__(daemon=True, name="engine")
        self.model_dir = model
        self.threads = threads
        self.language = language            # a code such as "en", or "auto"
        self.on_result = on_result          # called in this thread
        self.on_loaded = on_loaded          # called in this thread: True when ready, False on error
        self.marker = marker                # a file that exists while the model is loaded
        self.jobs: queue.Queue = queue.Queue()
        self.dropped: set[int] = set()      # failed dictations; their later jobs are skipped
        self.recognizer = None
        self.stream = None                  # of the dictation in progress
        self.dictation = 0                  # the dictation in progress
        self.text = ""
        self.audio = bytearray()            # of the dictation in progress, for a new language
        self.load_s = 3.5                   # how long the last load took; for the loading bar

    # Calls from the daemon thread. Each dictation is begin, feed..., end. A
    # dictation can begin before the load is done; the jobs run in order.

    def load(self) -> None:
        self._put("load", 0)

    def begin(self, dictation: int, audio: bytes) -> None:
        """Start a dictation with the audio recorded before it began."""
        self._put("begin", dictation, audio)

    def feed(self, dictation: int, audio: bytes) -> None:
        self._put("feed", dictation, audio)

    def end(self, dictation: int) -> None:
        self._put("end", dictation)

    def choose(self, dictation: int, language: str) -> None:
        """Use this language from now on; decode the dictation again in it."""
        self._put("choose", dictation, language)

    def unload(self) -> None:
        self._put("unload", 0)

    def _put(self, job: str, dictation: int, data: bytes | str = b"") -> None:
        self.jobs.put((job, dictation, data, time.monotonic()))

    # The worker.

    def run(self) -> None:
        while True:
            job, dictation, data, queued = self.jobs.get()
            try:
                self._do(job, dictation, data)
                if job == "end" and dictation not in self.dropped:
                    # The microphone stopped when the job was queued. A long
                    # time here means that decoding fell behind the speech.
                    print(f"voice: text ready {time.monotonic() - queued:.2f} s after "
                          "the microphone stopped", flush=True)
            except Exception:            # the thread must live on; the daemon needs an answer
                traceback.print_exc()
                self.stream, self.audio = None, bytearray()
                if job == "load":
                    self.recognizer = None
                    self.on_loaded(False)
                elif job in ("begin", "feed", "end"):
                    self.dropped.add(dictation)
                    self.on_result(Result(dictation, "", final=True))

    def _do(self, job: str, dictation: int, data) -> None:
        if job == "load":
            self._load()
        elif job == "unload":
            self._unload()
        elif job == "choose":
            self.language = data
            if self.stream is not None and dictation == self.dictation:
                before = self.text
                self._begin(dictation, bytes(self.audio))
                if before and not self.text:     # the old text must not stay in the box
                    self.on_result(Result(dictation, "", final=False))
        elif dictation in self.dropped:
            self.stream, self.audio = None, bytearray()
        elif job == "begin":
            self._begin(dictation, data)
        elif job == "feed":
            self.audio += data
            self._feed(dictation, pcm(data))
        elif job == "end":
            self._feed(dictation, np.zeros(int(TAIL_S * RATE), np.float32), quiet=True)
            self.stream.input_finished()
            self._decode()
            self.on_result(Result(dictation, self.text, final=True))
            self.stream, self.audio = None, bytearray()

    def _begin(self, dictation: int, audio: bytes) -> None:
        """Start a stream in the current language, with the audio so far."""
        self.stream = self.recognizer.create_stream()
        self.stream.set_option("language", self.language)
        self.dictation, self.text, self.audio = dictation, "", bytearray(audio)
        self._feed(dictation, pcm(audio))

    def _feed(self, dictation: int, samples: np.ndarray, quiet: bool = False) -> None:
        if self.stream is None or not len(samples):
            return
        self.stream.accept_waveform(RATE, samples)
        before = self.text
        self._decode()
        if self.text != before and not quiet:
            self.on_result(Result(dictation, self.text, final=False))

    def _decode(self) -> None:
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        self.text = " ".join(self.recognizer.get_result(self.stream).split())

    def _load(self) -> None:
        if self.recognizer is not None:
            self.on_loaded(True)
            return
        import sherpa_onnx

        def one(pattern: str) -> str:
            return glob.glob(f"{self.model_dir}/{pattern}")[0]

        started = time.monotonic()
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=one("tokens.txt"), encoder=one("encoder*.onnx"),
            decoder=one("decoder*.onnx"), joiner=one("joiner*.onnx"),
            num_threads=self.threads, provider=f"cpu:{SESSION_CONFIG}")
        trim_memory()
        self.load_s = time.monotonic() - started
        if self.marker:
            self.marker.touch()
        print(f"voice: model ready in {self.load_s * 1000:.0f} ms", flush=True)
        self.on_loaded(True)

    def _unload(self) -> None:
        if self.recognizer is not None:
            self.recognizer = self.stream = None
            self.dropped.clear()
            trim_memory()
            if self.marker:
                self.marker.unlink(missing_ok=True)
            print("voice: model freed", flush=True)
