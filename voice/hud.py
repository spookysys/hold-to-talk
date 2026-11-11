"""The caption box that shows what voice hears.

The box runs in a child process, so a slow redraw never delays the key
handling. It is an X11 window without decorations (through XWayland), because
GNOME gives such a window no focus: the paste still goes to the field that the
user works in. A native Wayland window would take the focus.

The child runs in the system Python. The Tk in uv's Python has no Xft, so it
knows only bitmap fonts and draws all text at 13 pixels. This file therefore
uses the standard library only.

The parent sends one JSON message per change through a nonblocking socket:
    {"state": "listening", "text": "...", "hint": "speak now"}
        the header says the state; the body shows the text, or else the hint
    {"state": "loading", "eta": 3.5, "elapsed": 0}
        the bar instead of the level, at 90 % after eta seconds
    {"state": "...", "seconds": 5}   hide after this time
    {"state": ..., "languages": ["en", "de", "auto"], "language": "de"}
        the languages to choose from, the chosen one marked; without them, none
    {"level": 0.6}       microphone loudness, 0 to 1
    {"state": null}      hide
The child starts at the first dictation and ends when the model is freed.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import socket
import subprocess
import threading
import sys
import time

BG, FG, DIM, LINE, BAR = "#17171a", "#f4f4f5", "#8a8a96", "#3a3a44", "#62a0ea"
FONT = "Adwaita Sans"


class Hud:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.connection: socket.socket | None = None
        self.shown = False
        self.failed = False      # the box could not start; voice works on without it

    def show(self, state: str, text: str = "", **extra) -> None:
        self.shown = True
        self._send({"state": state, "text": text, **extra})

    def level(self, value: float) -> None:
        if self.shown:
            self._send({"level": round(value, 2)})

    def hide(self) -> None:
        if self.shown:
            self.shown = False
            self._send({"state": None})

    def close(self) -> None:
        self.shown = False
        self.failed = False      # the next message starts a new box
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        process, self.process = self.process, None
        if process is not None:
            try:
                process.terminate()
            except OSError:
                pass
            threading.Thread(target=self._reap, args=(process,), daemon=True).start()

    @staticmethod
    def _reap(process: subprocess.Popen) -> None:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _send(self, message: dict) -> None:
        if not os.environ.get("DISPLAY") or self.failed:
            return
        try:
            if self.process is not None and self.process.poll() is not None:
                raise OSError("the caption process ended")
            if self.process is None:
                if message.get("state") is None:
                    return
                python = shutil.which("python3", path="/usr/bin") or sys.executable
                parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                self.connection = parent
                parent.setblocking(False)
                try:
                    self.process = subprocess.Popen(
                        [python, "-I", os.path.abspath(__file__), str(child.fileno())],
                        pass_fds=(child.fileno(),), stdin=subprocess.DEVNULL)
                finally:
                    child.close()
            # Each message is complete or fails. A stalled box cannot block keys.
            self.connection.send(json.dumps(message).encode())
        except (OSError, ValueError) as error:
            self.close()
            self.failed = True
            print(f"voice: caption box unavailable: {error}; voice goes on without it",
                  flush=True)


def run(connection: socket.socket | None = None) -> int:
    """The child: draw the box from JSON messages."""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
    width = int(screen_w * 0.5)
    pad = int(root.winfo_fpixels("10p"))
    bar_h = int(pad * 0.6)

    frame = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=LINE)
    frame.pack(fill="both", expand=True)
    top = tk.Frame(frame, bg=BG)
    top.pack(fill="x", padx=2 * pad, pady=(pad, pad // 2))
    header = tk.Label(top, anchor="w", font=(FONT, 13, "bold"), fg=DIM, bg=BG)
    header.pack(side="left")
    choices = tk.Frame(top, bg=BG)
    choices.pack(side="left", padx=(2 * pad, 0))
    meter = tk.Canvas(top, height=bar_h, width=width // 4, bg=LINE, highlightthickness=0)
    meter.pack(side="right", pady=pad // 2)
    bar = meter.create_rectangle(0, 0, 0, bar_h, fill=BAR, width=0)
    body = tk.Text(frame, font=(FONT, 20), fg=FG, bg=BG, bd=0, highlightthickness=0,
                   wrap="word", height=1, cursor="arrow")
    body.tag_configure("hint", foreground=DIM)
    body.pack(fill="x", padx=2 * pad, pady=(0, pad))

    def place() -> None:
        # Wrap at the final width. The requested height is known before the first
        # layout; the real height is then 1 pixel, and GNOME did not show a box
        # that grew from 1 pixel on its first show.
        root.geometry(f"{width}x{frame.winfo_reqheight()}")
        root.update_idletasks()
        lines = body.count("1.0", "end", "displaylines") or 1
        lines = lines[0] if isinstance(lines, tuple) else lines
        body.configure(height=min(6, max(1, lines)))   # the newest lines when it is longer
        root.update_idletasks()
        root.geometry(f"{width}x{frame.winfo_reqheight()}"
                      f"+{(screen_w - width) // 2}+{int(screen_h * 0.06)}")

    def hide() -> None:
        root.withdraw()

    def open_small() -> None:
        # Tk shows a window at its last size first, so the box would open tall
        # and then shrink. It opens at one line and grows with the text.
        body.configure(height=1)
        root.update_idletasks()
        root.geometry(f"{width}x{frame.winfo_reqheight()}"
                      f"+{(screen_w - width) // 2}+{int(screen_h * 0.06)}")

    loading = {"since": 0.0, "eta": 1.0, "active": False, "header": ""}
    hiding = [None]          # the timer that hides a message after its seconds

    def tick() -> None:
        if not loading["active"]:
            return
        # Linear to 90 % at the expected time, then slower toward 99 %: the bar
        # never stops while a slow load takes longer than the last one.
        x = (time.monotonic() - loading["since"]) / loading["eta"]
        part = 0.9 * x if x < 1 else 0.99 - 0.09 * math.exp(-3 * (x - 1))
        header.configure(text=f"voice: {loading['header']}loading the model {int(part * 100)} %")
        meter.coords(bar, 0, 0, int(meter.winfo_width() * part), bar_h)
        root.after(100, tick)

    def apply(message: dict) -> None:
        if "level" in message and "state" not in message:
            meter.coords(bar, 0, 0, int(meter.winfo_width() * message["level"]), bar_h)
            return
        state = message.get("state")
        if hiding[0]:
            root.after_cancel(hiding[0])
            hiding[0] = None
        ticking, loading["active"] = loading["active"], state == "loading"
        if state is None:
            hide()
            return
        text = message.get("text") or ""
        hint = message.get("hint") or ""
        header.configure(text=f"voice: {state}")
        for label in choices.winfo_children():
            label.destroy()
        for language in message.get("languages", []):
            chosen = language == message.get("language")
            tk.Label(choices, text=language.upper(), font=(FONT, 13, "bold"), padx=pad // 2,
                     fg=FG if chosen else DIM, bg=LINE if chosen else BG).pack(side="left")
        if state != "listening":
            meter.coords(bar, 0, 0, 0, bar_h)
        if state == "loading":
            eta = max(message.get("eta", 3.5), 0.5)
            loading.update(since=time.monotonic() - message.get("elapsed", 0), eta=eta,
                           header="listening, " if hint else "")
            if not ticking:
                tick()
        if message.get("seconds"):
            hiding[0] = root.after(int(message["seconds"] * 1000), hide)
        if not text and not hint:            # only the header
            body.pack_forget()
        elif not body.winfo_manager():
            body.pack(fill="x", padx=2 * pad, pady=(0, pad))
        body.configure(state="normal")
        body.delete("1.0", "end")
        if text:
            body.insert("end", text)
        else:
            body.insert("end", hint, "hint")
        body.see("end")
        body.configure(state="disabled")
        if root.state() == "withdrawn":
            open_small()
        root.deiconify()       # Tk counts the wrapped lines only of a visible window
        place()

    def read() -> None:
        messages = sys.stdin if connection is None else iter(lambda: connection.recv(262144), b"")
        for line in messages:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            root.after(0, apply, message)
        root.after(0, root.destroy)

    threading.Thread(target=read, daemon=True).start()
    root.mainloop()
    return 0


if __name__ == "__main__":
    connection = socket.socket(fileno=int(sys.argv[1])) if len(sys.argv) == 2 else None
    sys.exit(run(connection))
