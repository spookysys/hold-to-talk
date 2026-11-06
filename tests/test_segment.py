import numpy as np

from voice.segment import RATE, Segmenter


def tone(seconds, level=0.3):
    t = np.arange(int(seconds * RATE)) / RATE
    return level * np.sin(2 * np.pi * 220 * t)


def hiss(seconds, level=0.001):
    return np.random.default_rng(1).normal(0, level, int(seconds * RATE))


def pcm(*parts):
    audio = np.concatenate(parts)
    return (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()


def feed(segmenter, data, chunk=1600 * 2):
    phrases = []
    for start in range(0, len(data), chunk):
        phrases += segmenter.feed(data[start:start + chunk])
    return phrases


def test_silence_gives_nothing():
    s = Segmenter()
    assert feed(s, pcm(hiss(5))) == [] and s.flush() is None


def test_long_pause_after_long_phrase_cuts():
    s = Segmenter()
    phrases = feed(s, pcm(hiss(0.5), tone(4), hiss(1.0), tone(2)))
    assert len(phrases) == 1
    assert 4.0 <= len(phrases[0]) / RATE <= 4.5          # speech plus padding
    last = s.flush()
    assert last is not None and 2.0 <= len(last) / RATE <= 2.5


def test_short_phrase_waits_for_more():
    s = Segmenter()
    assert feed(s, pcm(hiss(0.5), tone(1.5), hiss(1.0), tone(1))) == []
    assert s.flush() is not None


def test_short_pause_does_not_cut():
    s = Segmenter()
    assert feed(s, pcm(hiss(0.5), tone(4), hiss(0.3), tone(2))) == []


def syllables(seconds):
    """A tone with a short gap every 0.3 s, as speech has between syllables."""
    audio = tone(seconds)
    for start in range(0, len(audio), int(0.3 * RATE)):
        audio[start:start + int(0.05 * RATE)] = 0
    return audio + hiss(seconds)


def test_long_speech_is_cut_before_whisper_window():
    s = Segmenter()
    phrases = feed(s, pcm(hiss(0.5), syllables(40)))
    assert phrases and all(len(p) / RATE <= 25.5 for p in phrases)


def test_quiet_microphone_is_normalized():
    s = Segmenter()
    feed(s, pcm(hiss(0.5, 0.0005), tone(2, 0.01)))
    last = s.flush()
    assert last is not None and abs(np.abs(last).max() - 0.5) < 1e-3
