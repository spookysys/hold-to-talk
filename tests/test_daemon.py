"""The daemon's handling of events, with fakes for the engine, box and microphone."""

import os

import pytest

import voice.daemon as D
from voice import hold as H
from voice.engine import Result


class FakeEngine:
    load_s = 3.5

    def __init__(self, *args, **kwargs):
        self.calls = []

    def start(self):
        pass

    def __getattr__(self, name):
        return lambda *args: self.calls.append((name, *args))


class FakeHud:
    def __init__(self):
        self.shown = False
        self.calls = []

    def show(self, state, text="", **extra):
        self.shown = True
        self.calls.append(state)

    def level(self, value):
        pass

    def hide(self):
        self.shown = False
        self.calls.append("hide")

    def close(self):
        self.hide()


class FakeRecorder:
    """pw-record: a real pipe, so the selector can register it."""

    def __init__(self, *args, **kwargs):
        r, self.w = os.pipe()
        self.stdout = os.fdopen(r, "rb", buffering=0)

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


@pytest.fixture
def daemon(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "Engine", FakeEngine)
    monkeypatch.setattr(D, "Hud", FakeHud)
    monkeypatch.setattr(D, "LOADED", tmp_path / "loaded")
    monkeypatch.setattr(D.subprocess, "Popen", FakeRecorder)
    written = []
    monkeypatch.setattr(D.output, "insert", lambda text, paste: written.append(text.strip()) or True)
    d = D.Daemon(D.Config())
    d.model = "loaded"
    d.written = written
    return d


def key(d, name, now):
    d._act(d.hold.event(name, now))
    d._write_waiting()


def dictate(d, now=0.0):
    """Hold the key past the threshold: a dictation begins."""
    key(d, "down", now)
    for _ in d.hold.tick(now + 1):
        d._begin()


def test_a_tap_does_not_keep_the_model(daemon):
    daemon.unload_at = 100.0
    key(daemon, "down", 0)
    key(daemon, "up", 0.1)
    assert daemon.unload_at == 100.0


def test_a_hold_during_the_load_records_nothing(daemon):
    daemon.model = "loading"
    key(daemon, "down", 0)
    daemon._loaded(True)
    for _ in daemon.hold.tick(1):
        daemon._begin()
    assert not daemon.listening
    assert daemon.hud.calls[-1] == "ready"


def test_a_failed_load_can_be_tried_again(daemon):
    daemon.model = "loading"
    daemon._loaded(False)
    assert daemon.model == "unloaded"


def test_text_waits_while_the_key_is_down(daemon):
    dictate(daemon)
    daemon._text(Result(0, "earlier", final=True))   # of a dictation before this hold
    assert daemon.written == []
    key(daemon, "up", 2)
    assert daemon.written == ["earlier"]


def test_texts_keep_their_order(daemon):
    dictate(daemon)
    daemon._text(Result(0, "one", final=True))
    key(daemon, "up", 2)
    daemon._stop()
    daemon._text(Result(daemon.dictation, "two", final=True))
    assert daemon.written == ["one", "two"]


def test_a_canceled_dictation_does_not_open_the_box_again(daemon):
    dictate(daemon)
    key(daemon, "key", 1.5)
    daemon._text(Result(daemon.dictation, "late", final=False))
    assert daemon.hud.calls[-1] == "hide"


def test_audio_after_a_stop_in_the_same_pass(daemon):
    daemon.recorder = None
    daemon._read_audio()        # no error


def test_the_microphone_ends_by_itself(daemon):
    dictate(daemon)
    os.close(daemon.recorder.w)
    daemon._read_audio()
    assert daemon.recorder is None


def test_no_unload_during_a_dictation(daemon):
    dictate(daemon)
    daemon.unload_at = 0.0
    assert daemon._busy()
