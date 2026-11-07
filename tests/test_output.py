import time

from voice import output


def clipboard(monkeypatch, start: str):
    board = {False: start, True: start}
    seen = []                       # what the application reads at each paste
    monkeypatch.setattr(output, "read", lambda primary: board[primary])
    monkeypatch.setattr(output, "write", lambda text, primary: board.__setitem__(primary, text) or True)
    monkeypatch.setattr(output, "RESTORE_AFTER_S", 0.05)
    return board, lambda: seen.append(board[False]) or True, seen


def test_one_paste_restores_the_clipboard(monkeypatch):
    board, paste, seen = clipboard(monkeypatch, "mine")
    output.insert("hello", paste)
    assert seen == ["hello"]
    time.sleep(0.1)
    assert board == {False: "mine", True: "mine"}


def test_a_series_keeps_the_users_clipboard(monkeypatch):
    board, paste, seen = clipboard(monkeypatch, "mine")
    output.insert("one", paste)
    time.sleep(0.03)                # the second paste comes before the restore
    output.insert("two", paste)
    time.sleep(0.03)                # the first restore would be due now
    assert board[False] == "two"
    time.sleep(0.1)
    assert seen == ["one", "two"]
    assert board == {False: "mine", True: "mine"}
