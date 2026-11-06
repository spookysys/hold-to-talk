"""Streaming speech to text with NVIDIA Nemotron 3.5 ASR, in one worker thread.

The model is a streaming transducer: it takes the audio in pieces as they
arrive, processes each piece once, and the text grows with the speech. At the
release only the last piece is left, so the text is ready a few hundred
milliseconds later. A transducer writes text only for sounds it hears, so it
does not invent text for silence.

The model takes about 1 GB of RAM and a few seconds to load. It loads when a
hold becomes a dictation; the audio waits in the queue meanwhile and the model
catches up, at about twice the speed of speech on a small laptop. The daemon
frees the model after some idle minutes.

Measured on an Intel i3-1215U: two threads are the fastest. More threads wait
for the slow cores of this CPU.
"""

from __future__ import annotations

import ctypes
import gc
import glob
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RATE = 16000

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


def pcm(data: bytes) -> np.ndarray:
    """16-bit mono samples as the float samples that the model takes."""
    return np.frombuffer(data[: len(data) // 2 * 2], np.int16).astype(np.float32) / 32768


class Engine(threading.Thread):
    def __init__(self, model: str, threads: int, language: str, on_result,
                 marker: Path | None = None):
        super().__init__(daemon=True, name="engine")
        self.model_dir = model
        self.threads = threads
        self.language = language
        self.on_result = on_result          # called in this thread
        self.marker = marker                # a file that exists while the model is loaded
        self.jobs: queue.Queue = queue.Queue()
        self.dropped: set[int] = set()      # canceled dictations
        self.recognizer = None
        self.stream = None                  # of the dictation in progress
        self.text = ""

    # Calls from the daemon thread. Each dictation is begin, feed..., end or drop.

    def begin(self, dictation: int, audio: bytes) -> None:
        """Start a dictation with the audio recorded before it began."""
        self.jobs.put(("begin", dictation, audio))

    def feed(self, dictation: int, audio: bytes) -> None:
        self.jobs.put(("feed", dictation, audio))

    def end(self, dictation: int) -> None:
        self.jobs.put(("end", dictation, b""))

    def drop(self, dictation: int) -> None:
        """Forget a canceled dictation; its queued audio is skipped."""
        self.dropped.add(dictation)

    def unload(self) -> None:
        self.jobs.put(("unload", 0, b""))

    # The worker.

    def run(self) -> None:
        while True:
            job, dictation, audio = self.jobs.get()
            if job == "unload":
                self._unload()
            elif dictation in self.dropped:
                self.stream = None
            elif job == "begin":
                self._begin(audio)
            elif job == "feed":
                self._feed(dictation, pcm(audio))
            elif job == "end":
                self._feed(dictation, np.zeros(int(TAIL_S * RATE), np.float32), quiet=True)
                self.stream.input_finished()
                self._decode()
                self.on_result(Result(dictation, self.text, final=True))
                self.stream = None

    def _begin(self, audio: bytes) -> None:
        if self.recognizer is None:
            self._load()
        self.stream = self.recognizer.create_stream()
        self.stream.set_option("language", self.language)
        self.text = ""
        self.stream.accept_waveform(RATE, pcm(audio))
        self._decode()

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
        import sherpa_onnx

        def one(pattern: str) -> str:
            return glob.glob(f"{self.model_dir}/{pattern}")[0]

        started = time.monotonic()
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=one("tokens.txt"), encoder=one("encoder*.onnx"),
            decoder=one("decoder*.onnx"), joiner=one("joiner*.onnx"),
            num_threads=self.threads)
        if self.marker:
            self.marker.touch()
        print(f"voice: model ready in {(time.monotonic() - started) * 1000:.0f} ms", flush=True)

    def _unload(self) -> None:
        if self.recognizer is not None:
            self.recognizer = self.stream = None
            self.dropped.clear()
            gc.collect()
            ctypes.CDLL("libc.so.6").malloc_trim(0)   # glibc keeps freed memory otherwise
            if self.marker:
                self.marker.unlink(missing_ok=True)
            print("voice: model freed", flush=True)
