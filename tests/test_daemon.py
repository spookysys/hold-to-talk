"""The daemon's handling of events, with fakes for the engine, box and microphone."""

import os
import signal
import socket
import time
import threading
from concurrent.futures import Future

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
        try:
            os.close(self.w)
        except OSError:
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
    monkeypatch.setattr(D.output, "insert", lambda text, paste: written.append(text.strip()) or "pasted")
    previous = signal.getsignal(signal.SIGUSR1)
    d = D.Daemon(D.Config())
    d.model = "loaded"
    d.written = written
    yield d
    d._stop_recorder()
    if d.keys is not None:
        d.keys.close()
    d.selector.close()
    os.close(d.wake_r)
    os.close(d.wake_w)
    signal.signal(signal.SIGUSR1, previous)


def pump(d, done):
    deadline = time.monotonic() + 2
    while not done():
        assert time.monotonic() < deadline, "worker did not complete"
        if d.events:
            function, arguments = d.events.pop(0)
            function(*arguments)
        else:
            time.sleep(0.001)


def settle(d):
    pump(d, lambda: not d.output_busy and not d.events)


def key(d, name, now):
    d._act(d.hold.event(name, now))
    d._write_waiting()
    settle(d)


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
    settle(daemon)
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


def connect(d):
    d.keys, peer = socket.socketpair()
    d.keys.setblocking(False)
    peer.settimeout(1)
    d.selector.register(d.keys, D.selectors.EVENT_READ, "keys")
    return peer


def test_early_engine_failure_completes_before_release(daemon):
    dictate(daemon)
    daemon._text(Result(daemon.dictation, "", final=True))
    key(daemon, "up", 2)
    daemon._stop()
    assert not daemon._busy()
    assert daemon.recorder is None
    assert daemon.hud.calls[-1] == "hide"
    assert daemon.unload_at is not None
    assert not any(call[0] == "end" for call in daemon.engine.calls)


def test_queued_texts_use_one_paste(daemon):
    dictate(daemon)
    daemon._text(Result(-1, "one", final=True))
    daemon._text(Result(0, "two", final=True))
    key(daemon, "up", 2)
    assert daemon.written == ["one two"]


def test_next_paste_waits_for_previous_application_read(daemon, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    seen = []

    def insert(text, paste):
        seen.append(text)
        started.set()
        assert finish.wait(1)
        return "pasted"

    monkeypatch.setattr(D.output, "insert", insert)
    daemon._text(Result(-1, "one", final=True))
    assert started.wait(1)
    daemon._text(Result(0, "two", final=True))
    assert seen == ["one "]
    finish.set()
    settle(daemon)
    assert seen == ["one ", "two "]


def test_new_hold_during_clipboard_preparation_defers_paste(daemon, monkeypatch):
    started, finish = threading.Event(), threading.Event()
    results = []

    def insert(text, paste):
        started.set()
        assert finish.wait(1)
        result = paste()
        results.append(result)
        return result

    monkeypatch.setattr(D.output, "insert", insert)
    with connect(daemon) as peer:
        daemon._text(Result(0, "one", final=True))
        assert started.wait(1)
        peer.sendall(b"down\n")
        finish.set()
        settle(daemon)
        assert results == ["held"]
        assert daemon.waiting == ["one"]
        assert daemon.hold.state == H.PENDING
        peer.sendall(b"up\n")
        daemon._read_keys()
        pump(daemon, lambda: daemon.paste_sent)
        assert peer.recv(64) == b"paste\n"
        peer.sendall(b"pasted\n")
        daemon._read_keys()
        settle(daemon)
        assert results == ["held", "pasted"]
        assert not daemon.waiting


def test_helper_rejection_defers_paste(daemon):
    with connect(daemon) as peer:
        request = Future()
        daemon._prepare_paste(request)
        assert peer.recv(64) == b"paste\n"
        peer.sendall(b"down\nheld\n")
        daemon._read_keys()
        assert request.result(timeout=1) == "held"
        assert daemon.paste_at is None


def test_helper_disconnect_finishes_pending_paste(daemon):
    peer = connect(daemon)
    request = Future()
    daemon._prepare_paste(request)
    peer.close()
    daemon._read_keys()
    assert request.result(timeout=1) == "failed"
    assert daemon.paste_at is None


def test_timeout_discards_the_connection_and_pending_request(daemon):
    with connect(daemon) as peer:
        request = Future()
        daemon._prepare_paste(request)
        assert peer.recv(64) == b"paste\n"
        daemon._disconnect_keys()
        assert request.result(timeout=1) == "failed"
        assert daemon.keys is None
        assert daemon.paste_at is None
        # A late completion cannot be delivered through the old connection.
        assert peer.recv(64) == b""
