import json
import socket
from unittest.mock import Mock

from voice.hud import Hud


def test_stalled_caption_cannot_block_keys(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":test")
    hud = Hud()
    hud.process = Mock()
    hud.process.poll.return_value = None
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    parent.setblocking(False)
    parent.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    hud.connection = parent
    try:
        # Fill the real socket, as if the caption stopped reading.
        while True:
            try:
                parent.send(b"x" * 1024)
            except BlockingIOError:
                break
        hud.show("listening", "hello")
        assert hud.failed
        assert hud.connection is None
        assert hud.process is None
    finally:
        child.close()
        hud.close()


def test_caption_messages_are_complete(monkeypatch):
    monkeypatch.setenv("DISPLAY", ":test")
    hud = Hud()
    hud.process = Mock()
    hud.process.poll.return_value = None
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    parent.setblocking(False)
    child.settimeout(1)
    hud.connection = parent
    try:
        hud.show("listening", "Grüße")
        assert json.loads(child.recv(4096)) == {"state": "listening", "text": "Grüße"}
        hud.hide()
        assert json.loads(child.recv(4096)) == {"state": None}
    finally:
        child.close()
        hud.close()
