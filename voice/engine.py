"""Speech to text with whisper.cpp, in one worker thread.

The model is loaded when a hold becomes a dictation, while the user starts to
speak, and freed after some idle minutes. A load from the page cache takes a
few hundred milliseconds, so a resident model would cost RAM for little gain.

Whisper always encodes a 30-second window. audio_ctx shrinks the window to the
phrase plus a margin, which makes a short phrase two to three times faster.
"""

from __future__ import annotations

import ctypes
import gc
import os
import queue
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .segment import RATE

MARGIN_S = 2.0   # a tighter window garbles short phrases

# Exists while the model is in memory; `voice status` reads it.
LOADED = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "voice-model-loaded"

# whisper.cpp detects the language before it applies the new audio_ctx, so the
# detection uses the window of the previous call (src/whisper.cpp,
# whisper_full_with_state). After a 3-second window, German was detected as
# English and then translated. Six seconds of the phrase are enough to detect
# the language, so no window is shorter.
MIN_CTX = 300


def window(seconds: float) -> int:
    """The audio_ctx for a phrase: 50 encoder frames per second of audio."""
    return max(MIN_CTX, min(1500, int(50 * (seconds + MARGIN_S)) + 1))

# Whisper answers silence and noise with lines from its subtitle training data.
INVENTED = {
    "thank you", "thanks for watching", "thank you for watching", "bye",
    "untertitel der amara org community", "untertitel im auftrag des zdf",
    "vielen dank", "vielen dank fürs zuschauen", "bis zum nächsten mal",
    "untertitelung des zdf für funk",
}


def invented(text: str) -> bool:
    plain = " ".join(re.sub(r"[^\w ]", " ", text.lower()).split())
    return plain in INVENTED


@dataclass
class Job:
    dictation: int
    audio: np.ndarray | None     # None: prepare the model only
    last: bool = False


@dataclass
class Result:
    dictation: int
    text: str
    last: bool


class Engine(threading.Thread):
    def __init__(self, model: str, threads: int, languages: list[str], on_result,
                 marker: Path | None = None):
        super().__init__(daemon=True, name="engine")
        self.marker = marker                # a file that exists while the model is loaded
        self.model_path = model
        self.threads = threads
        self.languages = languages
        self.on_result = on_result          # called in this thread
        self.jobs: queue.Queue = queue.Queue()
        self.dropped: set[int] = set()      # canceled dictations
        self.model = None
        self.language: dict[int, str] = {}

    # Calls from the daemon thread.

    def prepare(self, dictation: int) -> None:
        """Load the model while the user starts to speak."""
        self.jobs.put(Job(dictation, None))

    def submit(self, dictation: int, audio: np.ndarray | None, last: bool = False) -> None:
        self.jobs.put(Job(dictation, audio, last))

    def drop(self, dictation: int) -> None:
        """Forget a canceled dictation; its queued phrases are skipped."""
        self.dropped.add(dictation)

    def unload(self) -> None:
        self.jobs.put("unload")

    # The worker.

    def run(self) -> None:
        while True:
            job = self.jobs.get()
            if job == "unload":
                self._unload()
                continue
            if job.dictation in self.dropped:
                continue
            if self.model is None:
                self._load()
            text = ""
            if job.audio is not None and len(job.audio):
                text = self._transcribe(job)
            if job.audio is not None or job.last:
                self.on_result(Result(job.dictation, text, job.last))

    def _load(self) -> None:
        from pywhispercpp.model import Model
        started = time.monotonic()
        self.model = Model(self.model_path, n_threads=self.threads, print_progress=False,
                           print_realtime=False, redirect_whispercpp_logs_to=None)
        # The first pass after a load takes seconds longer than the next ones.
        # A pass over one second of silence pays that cost now, while the user
        # starts to speak.
        self.model.transcribe(np.zeros(RATE, np.float32), language=self.languages[0],
                              audio_ctx=MIN_CTX, no_context=True)
        if self.marker:
            self.marker.touch()
        print(f"voice: model ready in {(time.monotonic() - started) * 1000:.0f} ms", flush=True)

    def _unload(self) -> None:
        if self.model is not None:
            self.model = None
            self.language.clear()
            self.dropped.clear()
            gc.collect()
            ctypes.CDLL("libc.so.6").malloc_trim(0)   # glibc keeps freed memory otherwise
            if self.marker:
                self.marker.unlink(missing_ok=True)
            print("voice: model freed", flush=True)

    def _transcribe(self, job: Job) -> str:
        """The first phrase of a dictation sets its language for the rest."""
        started = time.monotonic()
        language = self.language.get(job.dictation)
        if language is None:
            text, language = self._run(job.audio, "auto")
            if language not in self.languages:
                # A short phrase can sound like a third language; ask again in the first one.
                language = self.languages[0]
                text, _ = self._run(job.audio, language)
            self.language[job.dictation] = language
        else:
            text, _ = self._run(job.audio, language)
        print(f"voice: {len(job.audio) / RATE:.1f} s of speech in "
              f"{(time.monotonic() - started) * 1000:.0f} ms ({language})", flush=True)
        return "" if invented(text) else text

    def _run(self, audio: np.ndarray, language: str) -> tuple[str, str]:
        """Return the text and the language that Whisper used."""
        import _pywhispercpp as pw
        segments = self.model.transcribe(
            audio,
            language=language,
            audio_ctx=window(len(audio) / RATE),
            no_context=True,
            suppress_non_speech_tokens=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        return text, pw.whisper_lang_str(pw.whisper_full_lang_id(self.model._ctx))
