"""Cut a recording into phrases at long pauses.

Each phrase is transcribed once, while the user still speaks, so after the
release only the last phrase waits for the engine. Cuts cost accuracy, because
the engine loses the context across a cut. A short pause inside a sentence
is therefore no cut; only a long pause after a long enough phrase is.
"""

from __future__ import annotations

import numpy as np

RATE = 16000
FRAME = 320                    # 20 ms
FRAME_S = FRAME / RATE

PAUSE_S = 0.7                  # silence that ends a phrase
MIN_PHRASE_S = 3.0             # a shorter phrase waits for more speech
MAX_PHRASE_S = 25.0            # Whisper's window is 30 s
PAD_S = 0.2                    # silence kept around speech
MIN_SPEECH_S = 0.3             # less speech than this is noise

FLOOR = 10 ** (-60 / 20)       # quieter than this is never speech
NOISE_RISE = 10 ** (3 / 20 * FRAME_S)   # the noise estimate rises 3 dB per second


class Segmenter:
    def __init__(self) -> None:
        self.samples = np.zeros(0, np.float32)   # the open phrase
        self.loud = np.zeros(0, bool)            # one flag per frame of it
        self.rest = b""                          # bytes short of a frame
        self.noise: float | None = None          # level of the background noise
        self.speech_s = 0.0                      # in the whole dictation

    def feed(self, data: bytes) -> list[np.ndarray]:
        """Add 16-bit mono audio; return the phrases that it closes."""
        data = self.rest + data
        usable = len(data) // (2 * FRAME) * 2 * FRAME
        self.rest = data[usable:]
        if not usable:
            return []
        audio = np.frombuffer(data[:usable], np.int16).astype(np.float32) / 32768
        rms = np.sqrt((audio.reshape(-1, FRAME) ** 2).mean(axis=1))
        self.samples = np.concatenate((self.samples, audio))
        self.loud = np.concatenate((self.loud, self._loud(rms)))
        self.speech_s += float(self.loud[-len(rms):].sum()) * FRAME_S

        phrases = []
        while True:
            cut = self._cut()
            if cut is None:
                break
            phrases.append(self._take(cut))
        return [p for p in phrases if p is not None]

    def flush(self) -> np.ndarray | None:
        """Return what is left as the last phrase."""
        return self._take(len(self.loud))

    def _loud(self, rms: np.ndarray) -> np.ndarray:
        # The noise level drops at once to a quieter frame and rises slowly,
        # so speech, which has gaps between words, never becomes the noise.
        loud = np.empty(len(rms), bool)
        for i, level in enumerate(rms):
            self.noise = level if self.noise is None else min(level, self.noise * NOISE_RISE)
            self.noise = max(self.noise, 1e-6)
            loud[i] = level > max(self.noise * 4, FLOOR)
        return loud

    def _cut(self) -> int | None:
        frames = len(self.loud)
        if frames * FRAME_S >= MAX_PHRASE_S:
            # Cut at the quietest stretch in the last five seconds.
            tail = int(5 / FRAME_S)
            run = np.convolve(self.loud[-tail:], np.ones(10), "same")
            return frames - tail + int(np.argmin(run))
        pause = int(PAUSE_S / FRAME_S)
        if frames * FRAME_S < MIN_PHRASE_S + PAUSE_S or self.loud[-pause:].any():
            return None
        return frames - pause // 2

    def _take(self, frames: int) -> np.ndarray | None:
        loud, samples = self.loud[:frames], self.samples[:frames * FRAME]
        self.loud, self.samples = self.loud[frames:], self.samples[frames * FRAME:]
        if loud.sum() * FRAME_S < MIN_SPEECH_S:
            return None                          # Whisper invents text for silence
        on = np.flatnonzero(loud)
        pad = int(PAD_S / FRAME_S)
        first, last = max(on[0] - pad, 0), min(on[-1] + 1 + pad, frames)
        phrase = samples[first * FRAME:last * FRAME]
        peak = float(np.abs(phrase).max())
        return phrase * (0.5 / peak) if peak > 0 else phrase   # quiet microphones
