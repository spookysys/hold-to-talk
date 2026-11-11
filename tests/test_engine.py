import queue
from unittest.mock import Mock

import pytest

from voice.engine import Engine


def test_a_new_language_applies_from_now_on():
    results = []
    engine = Engine("", 2, "de", results.append, lambda ok: None)
    engine.recognizer = Mock()
    streams = []

    def create_stream():
        streams.append(Mock())
        return streams[-1]

    engine.recognizer.create_stream.side_effect = create_stream
    engine.recognizer.is_ready.return_value = False
    engine.recognizer.get_result.return_value = "Hallo"
    engine._do("begin", 1, b"\1\0")
    engine._do("feed", 1, b"\2\0")
    engine._do("choose", 1, "en")
    streams[0].input_finished.assert_called_once()      # the German part is finished
    streams[1].set_option.assert_called_with("language", "en")
    assert not streams[1].accept_waveform.called         # nothing is decoded again
    engine.recognizer.get_result.return_value = "hello"
    engine._do("feed", 1, b"\3\0")
    engine._do("end", 1, b"")
    assert results[-1].text == "Hallo hello" and results[-1].final


def test_a_choice_between_dictations_applies_to_the_next_one():
    engine = Engine("", 2, "de", lambda r: None, lambda ok: None)
    engine.recognizer = Mock()
    engine._do("choose", 1, "nb")
    assert engine.language == "nb"
    assert not engine.recognizer.create_stream.called


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
