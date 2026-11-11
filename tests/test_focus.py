from concurrent.futures import Future
import subprocess

from voice import focus


def test_capture_returns_the_token_without_window_content(monkeypatch):
    seen = []
    monkeypatch.setattr(focus, "call", lambda method, at: seen.append((method, int(at))) or "('abc-123:4',)")
    token = focus.Focus().capture().result(timeout=1)
    assert token == "abc-123:4"
    assert seen[0][0] == "Capture"
    assert seen[0][1] > 0


def test_unavailable_capture_cannot_restore_current_focus(monkeypatch):
    calls = []
    monkeypatch.setattr(focus, "call", lambda *args: calls.append(args) or "")
    target = focus.Focus().capture()
    assert not focus.Focus().restore(target)
    assert [call[0] for call in calls] == ["Capture"]


def test_restore_requires_positive_confirmation(monkeypatch):
    target = Future()
    target.set_result("abc:4")
    monkeypatch.setattr(focus, "call", lambda *args: "(false,)")
    assert not focus.Focus().restore(target)
    monkeypatch.setattr(focus, "call", lambda *args: "(true,)")
    assert focus.Focus().restore(target)


def test_bus_timeout_is_a_failure(monkeypatch):
    def run(*args, **kwargs):
        assert kwargs["timeout"] == 2
        raise subprocess.TimeoutExpired(args[0], 2)
    monkeypatch.setattr(focus.subprocess, "run", run)
    assert focus.call("Restore", "abc:4") == ""
