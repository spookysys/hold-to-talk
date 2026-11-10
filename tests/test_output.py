from unittest.mock import Mock

import pytest

from voice import output


@pytest.fixture
def clipboard(monkeypatch):
    board = {False: "mine", True: "selection"}
    monkeypatch.setattr(output, "write", lambda text, primary: board.__setitem__(primary, text) or True)
    monkeypatch.setattr(output.time, "sleep", lambda seconds: None)
    return board


def test_paste_keeps_text_until_the_application_reads_it(clipboard, monkeypatch):
    seen = []
    monkeypatch.setattr(output.time, "sleep", lambda seconds: seen.append(clipboard[False]))
    assert output.insert("hello", lambda: "pasted") == "pasted"
    assert seen == ["hello"]
    assert clipboard == {False: "hello", True: "hello"}


@pytest.mark.parametrize("primary", [False, True])
def test_paste_preserves_a_new_copy(clipboard, monkeypatch, primary):
    monkeypatch.setattr(output.time, "sleep", lambda seconds: clipboard.__setitem__(primary, "new copy"))
    output.insert("hello", lambda: "pasted")
    assert clipboard[primary] == "new copy"
    assert clipboard[not primary] == "hello"


@pytest.mark.parametrize("result", ["held", "failed"])
def test_unsent_paste_leaves_text_available(clipboard, result):
    assert output.insert("hello", lambda: result) == result
    assert clipboard == {False: "hello", True: "hello"}


def test_partial_clipboard_failure_does_not_send_paste(clipboard, monkeypatch):
    def write(text, primary):
        if primary:
            return False
        clipboard[primary] = text
        return True

    monkeypatch.setattr(output, "write", write)
    paste = Mock()
    assert output.insert("hello", paste) == "failed"
    paste.assert_not_called()
    assert clipboard == {False: "hello", True: "selection"}


def test_timed_out_copy_is_killed_and_reaped(monkeypatch):
    process = Mock()
    process.communicate.side_effect = [output.subprocess.TimeoutExpired("wl-copy", 2), (None, None)]
    monkeypatch.setattr(output.subprocess, "Popen", lambda *a, **kw: process)
    assert not output.write("hello", False)
    process.kill.assert_called_once()
    assert process.communicate.call_count == 2
