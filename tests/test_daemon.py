"""The daemon's handling of events, with fakes for the engine, box and microphone."""

import os
import signal
import socket
import time
import threading
from concurrent.futures import Future

import pytest

import voice.daemon as D
import voice.focus as focus_module
from voice import hold as H
from voice.engine import Result


class FakeFocus:
    def capture(self):
        result = Future()
        result.set_result("test-window")
        return result

    def restore(self, destination):
        return "restored"


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
    monkeypatch.setattr(D, "Focus", FakeFocus)
    monkeypatch.setattr(D, "Hud", FakeHud)
    monkeypatch.setattr(D, "LOADED", tmp_path / "loaded")
    monkeypatch.setattr(D, "LANGUAGE", tmp_path / "state/language")
    monkeypatch.setattr(D, "swapped_kb", lambda: 0)
    monkeypatch.setattr(D.subprocess, "Popen", FakeRecorder)
    written, copied = [], []
    monkeypatch.setattr(D.output, "insert", lambda text, paste: written.append(text.strip()) or "pasted")
    monkeypatch.setattr(D.output, "copy", lambda text: copied.append(text.strip()) or True)
    previous = signal.getsignal(signal.SIGUSR1)
    d = D.Daemon(D.Config())
    d.model = "loaded"
    d.written = written
    d.copied = copied
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


def test_a_hold_while_unloaded_loads_and_dictates(daemon):
    daemon.model = "unloaded"
    dictate(daemon)
    assert daemon.listening
    assert daemon.recorder is not None
    assert [call[0] for call in daemon.engine.calls] == ["load", "begin"]
    assert daemon.hud.calls[-1] == "loading"
    daemon._loaded(True)
    assert daemon.hud.calls[-1] == "listening"
    key(daemon, "up", 2)
    daemon._stop()
    daemon._text(Result(daemon.dictation, "late but complete", final=True))
    settle(daemon)
    assert daemon.written == ["late but complete"]


def test_a_tap_hides_the_box_only_when_no_text_is_on_its_way(daemon):
    daemon.model = "unloaded"
    dictate(daemon)
    key(daemon, "up", 2)
    daemon._stop()
    key(daemon, "down", 3)
    key(daemon, "up", 3.1)                  # a tap during the load
    assert daemon.hud.calls[-1] == "loading"
    daemon._loaded(True)
    daemon._text(Result(daemon.dictation, "done", final=True))
    settle(daemon)
    key(daemon, "down", 5)
    key(daemon, "up", 5.1)                  # a tap with nothing on its way
    assert daemon.hud.calls[-1] == "hide"


def test_a_release_during_the_load_keeps_the_bar(daemon):
    daemon.model = "unloaded"
    dictate(daemon)
    key(daemon, "up", 2)
    assert daemon.hud.calls[-1] == "loading"
    daemon._stop()
    daemon._loaded(True)
    assert daemon.hud.calls[-1] == "writing"


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


def test_a_canceled_dictation_goes_to_the_clipboard_only(daemon):
    dictate(daemon)
    key(daemon, "key", 1.5)
    assert daemon.recorder is None          # the key ends the speech at once
    assert ("end", daemon.dictation) in daemon.engine.calls
    daemon._text(Result(daemon.dictation, "kept", final=True))
    key(daemon, "up", 2)
    assert daemon.written == []
    assert daemon.copied == ["kept"]
    assert daemon.hud.calls[-1] == D.NOTICES["copied"][1]


def test_audio_after_a_stop_in_the_same_pass(daemon):
    daemon.recorder = None
    daemon._read_audio()        # no error


def test_the_microphone_ends_by_itself(daemon):
    dictate(daemon)
    os.close(daemon.recorder.w)
    daemon._read_audio()
    assert daemon.recorder is None


def test_unload_during_a_dictation_happens_after_it(daemon):
    dictate(daemon)
    daemon._unload_now(signal.SIGUSR1, None)
    daemon.unload_at = None              # the loop puts it off: the dictation is busy
    key(daemon, "up", 2)
    daemon._stop()
    daemon._text(Result(daemon.dictation, "text", final=True))
    settle(daemon)
    assert daemon.unload_at <= time.monotonic()
    daemon._free_model()
    daemon._schedule_unload()
    assert daemon.unload_at > time.monotonic() + 60   # the request does not stay


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


def test_queued_texts_keep_separate_destinations(daemon):
    dictate(daemon)
    daemon._text(Result(-1, "one", final=True))
    daemon._text(Result(0, "two", final=True))
    key(daemon, "up", 2)
    assert daemon.written == ["one", "two"]


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
        assert [pending.text for pending in daemon.waiting] == ["one"]
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
        pump(daemon, lambda: daemon.paste_sent)
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
        pump(daemon, lambda: daemon.paste_sent)
        assert peer.recv(64) == b"paste\n"
        daemon._disconnect_keys()
        assert request.result(timeout=1) == "failed"
        assert daemon.keys is None
        assert daemon.paste_at is None
        # A late completion cannot be delivered through the old connection.
        assert peer.recv(64) == b""


def test_window_is_restored_after_clipboard_preparation(daemon, monkeypatch):
    destination = Future()
    destination.set_result("original")
    seen = []
    monkeypatch.setattr(daemon.focus, "restore", lambda target: seen.append(target.result()) or "restored")
    with connect(daemon) as peer:
        request = Future()
        daemon._prepare_paste(request, destination)
        pump(daemon, lambda: daemon.paste_sent)
        assert seen == ["original"]
        assert peer.recv(64) == b"paste\n"
        peer.sendall(b"pasted\n")
        daemon._read_keys()
        assert request.result() == "pasted"


@pytest.mark.parametrize("failure", ["changed", "not-loaded"])
def test_missing_window_never_sends_paste(daemon, monkeypatch, failure):
    monkeypatch.setattr(daemon.focus, "restore", lambda target: failure)
    with connect(daemon) as peer:
        request = Future()
        daemon._prepare_paste(request)
        pump(daemon, request.done)
        assert request.result() == failure
        peer.setblocking(False)
        with pytest.raises(BlockingIOError):
            peer.recv(64)


def test_hold_during_window_restore_defers_paste(daemon, monkeypatch):
    started, finish = threading.Event(), threading.Event()

    def restore(target):
        started.set()
        assert finish.wait(1)
        return "restored"

    monkeypatch.setattr(daemon.focus, "restore", restore)
    with connect(daemon) as peer:
        request = Future()
        daemon._prepare_paste(request)
        assert started.wait(1)
        peer.sendall(b"down\n")
        finish.set()
        pump(daemon, request.done)
        assert request.result() == "held"
        peer.setblocking(False)
        with pytest.raises(BlockingIOError):
            peer.recv(64)


def test_each_dictation_keeps_its_key_down_destination(daemon, monkeypatch):
    first, second = Future(), Future()
    first.set_result("first")
    second.set_result("second")
    captures = iter([first, second])
    monkeypatch.setattr(daemon.focus, "capture", lambda: next(captures))
    dictate(daemon)
    daemon.output_busy = True  # keep results queued for inspection
    first_id = daemon.dictation
    daemon._act(daemon.hold.event("up", 2))
    daemon._stop()
    daemon._act(daemon.hold.event("down", 3))
    for _ in daemon.hold.tick(4):
        daemon._begin()
    daemon._text(Result(first_id, "first text", final=True))
    daemon._act(daemon.hold.event("up", 5))
    daemon._stop()
    daemon._text(Result(daemon.dictation, "second text", final=True))
    assert [item.destination for item in daemon.waiting] == [first, second]
    assert not daemon.destinations
    daemon.output_busy = False


def test_swapped_idle_model_is_freed_and_unloaded_sleeps_forever(daemon, monkeypatch):
    daemon._schedule_unload()
    assert daemon.swap_check_at is not None
    monkeypatch.setattr(D, "swapped_kb", lambda: D.MAX_IDLE_SWAP_KB)
    daemon._check_swap(daemon.swap_check_at)
    assert daemon.model == "unloaded"
    assert ("unload",) in daemon.engine.calls
    assert daemon.unload_at is None
    assert daemon.swap_check_at is None
    daemon.keys = object()
    assert daemon._timeout(0.0) is None
    daemon.keys = None


def test_a_swapped_model_still_takes_the_dictation(daemon, monkeypatch):
    monkeypatch.setattr(D, "swapped_kb", lambda: D.MAX_IDLE_SWAP_KB)
    dictate(daemon)
    assert daemon.listening
    assert ("unload",) not in daemon.engine.calls


def test_status_reports_swapped_memory_and_unload_waits(monkeypatch, tmp_path, capsys):
    import voice.__main__ as cli
    import voice.engine as E

    marker = tmp_path / "loaded"
    marker.touch()
    monkeypatch.setattr(E, "LOADED", marker)
    monkeypatch.setattr(cli, "systemctl", lambda *a: type("R", (), {"stdout": "active\n", "returncode": 0})())
    monkeypatch.setattr(cli, "main_pid", lambda: 42)
    monkeypatch.setattr(cli, "memory_kb", lambda pid: (22 * 1024, 1024 * 1024))
    monkeypatch.setattr(focus_module, "call", lambda *args: "")
    assert cli.status() == 0
    out = capsys.readouterr().out
    assert "model loaded, 1046 MB (1024 MB swapped)" in out
    assert "extension" not in out

    def not_loaded(*args):
        raise focus_module.NotLoaded("no object")
    monkeypatch.setattr(focus_module, "call", not_loaded)
    cli.status()
    assert "log out and back in" in capsys.readouterr().out

    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: marker.unlink())
    assert cli.unload() == 0
    assert "voice: model freed" in capsys.readouterr().out


@pytest.mark.parametrize("result", ["changed", "not-loaded", "failed"])
def test_failed_insert_shows_a_short_message(daemon, result):
    daemon._inserted(result, D.PendingText("text", None))
    assert daemon.hud.calls[-1] == D.NOTICES[result][1]


def test_space_and_arrows_choose_the_language_during_a_dictation(daemon):
    daemon.config.languages = ["en", "de", "auto"]
    daemon.language = "en"
    with connect(daemon) as peer:
        dictate(daemon)
        assert peer.recv(64) == b"select\n"     # the helper binds Space, Left and Right
        peer.sendall(b"next\nnext\n")
        daemon._read_keys()
        assert daemon.language == "auto"
        assert ("choose", daemon.dictation, "de") in daemon.engine.calls
        assert ("choose", daemon.dictation, "auto") in daemon.engine.calls
        peer.sendall(b"previous\n")
        daemon._read_keys()
        assert daemon.language == "de"
    assert D.chosen_language(daemon.config.languages) == "de"   # the next start keeps it


def test_one_language_needs_no_selection_keys(daemon):
    with connect(daemon) as peer:
        dictate(daemon)
        peer.setblocking(False)
        with pytest.raises(BlockingIOError):
            peer.recv(64)
        daemon._choose(1)
        assert daemon.language == "auto"


def test_a_removed_language_falls_back_to_the_first(daemon):
    D.remember_language("sv")
    assert D.chosen_language(["en", "de"]) == "en"
