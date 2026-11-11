import queue
from unittest.mock import Mock

import pytest

from voice.engine import Engine, foreign


def test_latin_text_with_umlauts_and_accents_is_not_foreign():
    for text in ["Hallo, wie geht es?", "Grüße aus Köln", "naïve café", "10 Minuten.", ""]:
        assert not foreign(text)


def test_mostly_other_scripts_are_foreign():
    for text in ["हेलो", "Привет", "こんにちは", "Hi Привет"]:
        assert foreign(text)


def test_one_misheard_word_is_not_foreign():
    assert not foreign("Das ist ein langer Satz mit einem Wort Привет darin")


def test_speech_is_loud_against_its_quiet_parts():
    import numpy as np
    from voice.engine import RATE, speechlike

    rng = np.random.default_rng(1)
    room = rng.normal(0, 0.001, RATE)
    voice = 0.2 * np.sin(2 * np.pi * 220 * np.arange(RATE) / RATE)
    assert speechlike(np.concatenate([room, voice, room]))
    assert not speechlike(np.concatenate([room, room]))
    assert not speechlike(np.zeros(100))
    click = np.concatenate([room[:16000], 0.5 * np.ones(320), room[:16000]])   # 20 ms, the key
    assert not speechlike(click)


@pytest.mark.parametrize("language, text, retried", [
    ("ru", "Привет", False),
    ("auto", "Привет", True),
    ("de", "Hallo", False),
])
def test_fallback_respects_explicit_language(language, text, retried):
    results = []
    engine = Engine("", 2, language, results.append, lambda ok: None, languages=["de", "en"])
    engine.stream = Mock()
    engine.text = text
    engine._feed = Mock()
    engine._decode = Mock()
    engine._again = Mock(return_value="fallback")
    engine._do("end", 1, b"")
    assert engine._again.called == retried
    assert results[0].text == ("fallback" if retried else text)
    assert results[0].final


@pytest.mark.parametrize("failed_job", ["begin", "feed", "end"])
def test_engine_failure_reports_completion_once(failed_job):
    results = []
    engine = Engine("", 2, "auto", results.append, lambda ok: None)
    engine.recognizer = Mock()
    engine.recognizer.is_ready.return_value = False
    engine.recognizer.get_result.return_value = ""
    perform = engine._do

    def fail(job, dictation, audio):
        if job == failed_job:
            raise RuntimeError("test recognition failure")
        perform(job, dictation, audio)

    engine._do = fail
    engine.begin(1, b"")
    engine.feed(1, b"\0\0")
    engine.end(1)
    # Run the real worker loop until it has consumed this finite job sequence.
    get = engine.jobs.get
    engine.jobs.get = lambda: get(block=False)
    with pytest.raises(queue.Empty):
        engine.run()
    assert len(results) == 1
    assert results[0].final and results[0].text == ""


def test_unload_trims_memory_and_removes_marker(monkeypatch, tmp_path):
    import voice.engine as E

    trimmed = []
    marker = tmp_path / "loaded"
    marker.touch()
    monkeypatch.setattr(E, "trim_memory", lambda: trimmed.append(True))
    engine = Engine("", 2, "auto", lambda r: None, lambda ok: None, marker=marker)
    engine.recognizer = Mock()
    engine._unload()
    assert trimmed == [True]
    assert not marker.exists()


def test_threads_sleep_instead_of_spinning():
    from voice.engine import SESSION_CONFIG

    # sherpa-onnx reads key=value lines; keep comments out of this file.
    lines = SESSION_CONFIG.read_text().splitlines()
    assert "SessionConfig.session.intra_op.allow_spinning=0" in lines
    assert "SessionConfig.session.inter_op.allow_spinning=0" in lines
    assert all("=" in line for line in lines)


def test_a_long_dictation_keeps_its_first_text():
    from voice.engine import AGAIN_MAX_S, RATE

    results = []
    engine = Engine("", 2, "auto", results.append, lambda ok: None, languages=["de", "en"])
    engine.stream = Mock()
    engine._feed = Mock()
    engine._decode = Mock()
    engine._again = Mock(return_value="fallback")
    engine.audio = bytearray()
    second = b"\0\0" * RATE
    for _ in range(AGAIN_MAX_S + 1):
        engine._do("feed", 1, second)
    assert engine.audio is None               # the memory stays constant
    engine.text = "Привет"
    engine._do("end", 1, b"")
    assert not engine._again.called
    assert results[-1].text == "Привет"
