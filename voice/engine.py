"""Streaming speech to text with NVIDIA Nemotron 3.5 ASR, in one worker thread.

The model is a streaming transducer: it takes the audio in pieces as they
arrive, processes each piece once, and the text grows with the speech. At the
release only the last piece is left, so the text is ready a few hundred
milliseconds later. A transducer writes text only for sounds it hears, so it
does not invent text for silence.

The model takes about 1 GB of RAM and a few seconds to load. The daemon asks
for the load and frees the model after some idle minutes; a dictation begins
only when the model is loaded.

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
import unicodedata
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


def foreign(text: str) -> bool:
    """True when the text has a letter that is not Latin: the language guess was wrong."""
    return any(c.isalpha() and "LATIN" not in unicodedata.name(c, "") for c in text)


def speechlike(samples: np.ndarray) -> bool:
    """True when at least 200 ms are 20 dB above the quiet tenth.

    A wrong language guess can also give no text at all; then the audio
    shows whether there was something to hear. A click of the key is loud
    for a few milliseconds only, so it does not count as speech.
    """
    frames = len(samples) // 320                     # of 20 ms
    if frames < 10:
        return False
    rms = np.sqrt((samples[: frames * 320].reshape(frames, 320) ** 2).mean(axis=1))
    loud = rms > max(10 * float(np.percentile(rms, 10)), 1e-3)
    return int(loud.sum()) >= 10


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
                 marker: Path | None = None, languages: list[str] | None = None):
        super().__init__(daemon=True, name="engine")
        self.model_dir = model
        self.threads = threads
        self.language = language
        # The user's languages. A text in another script is decoded again in each.
        self.languages = languages or []
        self.on_result = on_result          # called in this thread
        self.on_loaded = on_loaded          # called in this thread: True when ready, False on error
        self.marker = marker                # a file that exists while the model is loaded
        self.jobs: queue.Queue = queue.Queue()
        self.dropped: set[int] = set()      # canceled dictations
        self.recognizer = None
        self.stream = None                  # of the dictation in progress
        self.text = ""
        self.audio = bytearray()            # of the dictation in progress, for a second decode
        self.load_s = 3.5                   # how long the last load took; for the loading bar

    # Calls from the daemon thread. Each dictation is begin, feed..., end or drop,
    # after a load.

    def load(self) -> None:
        self._put("load", 0)

    def begin(self, dictation: int, audio: bytes) -> None:
        """Start a dictation with the audio recorded before it began."""
        self._put("begin", dictation, audio)

    def feed(self, dictation: int, audio: bytes) -> None:
        self._put("feed", dictation, audio)

    def end(self, dictation: int) -> None:
        self._put("end", dictation)

    def drop(self, dictation: int) -> None:
        """Forget a canceled dictation; its queued audio is skipped."""
        self.dropped.add(dictation)

    def unload(self) -> None:
        self._put("unload", 0)

    def _put(self, job: str, dictation: int, audio: bytes = b"") -> None:
        self.jobs.put((job, dictation, audio, time.monotonic()))

    # The worker.

    def run(self) -> None:
        while True:
            job, dictation, audio, queued = self.jobs.get()
            try:
                self._do(job, dictation, audio)
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

    def _do(self, job: str, dictation: int, audio: bytes) -> None:
        if job == "load":
            self._load()
        elif job == "unload":
            self._unload()
        elif dictation in self.dropped:
            self.stream, self.audio = None, bytearray()
        elif job == "begin":
            self._begin(dictation, audio)
        elif job == "feed":
            self.audio += audio
            self._feed(dictation, pcm(audio))
        elif job == "end":
            self._feed(dictation, np.zeros(int(TAIL_S * RATE), np.float32), quiet=True)
            self.stream.input_finished()
            self._decode()
            wrong = foreign(self.text) or (not self.text and speechlike(pcm(bytes(self.audio))))
            if self.language == "auto" and wrong and self.languages:
                self.text = self._again()
            self.on_result(Result(dictation, self.text, final=True))
            self.stream, self.audio = None, bytearray()

    def _begin(self, dictation: int, audio: bytes) -> None:
        self.stream = self.recognizer.create_stream()
        self.stream.set_option("language", self.language)
        self.text, self.audio = "", bytearray(audio)
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

    def _again(self) -> str:
        """Decode the dictation again in each of the user's languages; keep the surest text."""
        started, streams = time.monotonic(), []
        samples = np.concatenate([pcm(bytes(self.audio)), np.zeros(int(TAIL_S * RATE), np.float32)])
        for language in self.languages:
            stream = self.recognizer.create_stream()
            stream.set_option("language", language)
            stream.accept_waveform(RATE, samples)
            stream.input_finished()
            streams.append(stream)
        # Together in one batch: about 20 % faster than one after the other.
        while ready := [s for s in streams if self.recognizer.is_ready(s)]:
            self.recognizer.decode_streams(ready)
        found = []
        for language, stream in zip(self.languages, streams):
            result = self.recognizer.get_result_all(stream)
            score = float(np.mean(result.ys_probs)) if len(result.ys_probs) else -np.inf
            found.append((score, language, " ".join(result.text.split())))
        score, language, text = max(found)
        print(f"voice: {self.text!r} looked like a wrong language; decoded again as "
              f"{', '.join(self.languages)} in {(time.monotonic() - started) * 1000:.0f} ms, "
              f"kept {language}", flush=True)
        return text

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
