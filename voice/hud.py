"""The caption box that shows what voice hears.

The box runs in a child process, so a slow redraw never delays the key
handling. It is an X11 window without decorations (through XWayland), because
GNOME gives such a window no focus: the paste still goes to the field that the
user works in. A native Wayland window would take the focus.

The parent sends one JSON line per change. The child starts at the first
dictation and ends when the model is freed, so it costs no memory when idle.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

BG, FG, DIM, LINE = "#17171a", "#f4f4f5", "#9595a0", "#3a3a44"


class Hud:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None

    def show(self, state: str, text: str = "") -> None:
        self._send({"state": state, "text": text})

    def hide(self) -> None:
        self._send({"state": None})

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        try:
            process.stdin.close()
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()

    def _send(self, message: dict) -> None:
        if not os.environ.get("DISPLAY"):
            return
        if self.process is None or self.process.poll() is not None:
            if message["state"] is None:
                return
            self.process = subprocess.Popen([sys.executable, "-m", "voice", "hud"],
                                            stdin=subprocess.PIPE, text=True, bufsize=1)
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
        except (OSError, ValueError):
            self.close()


def run() -> int:
    """The child: draw the box from the JSON lines on stdin."""
    import threading
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    frame = tk.Frame(root, bg=BG, highlightthickness=1, highlightbackground=LINE)
    frame.pack(fill="both", expand=True)
    header = tk.Label(frame, anchor="w", font=("Cantarell", 10, "bold"), fg=DIM, bg=BG, padx=20)
    header.pack(fill="x", pady=(12, 0))
    body = tk.Label(frame, anchor="w", justify="left", font=("Cantarell", 16), fg=FG, bg=BG,
                    padx=20, wraplength=800)
    body.pack(fill="x", pady=(2, 14))

    def apply(message: dict) -> None:
        if message.get("state") is None:
            root.withdraw()
            return
        text = " ".join(str(message.get("text") or "").split())
        header.configure(text=f"voice: {message['state']}")
        body.configure(text=("…" + text[-400:]) if len(text) > 400 else text)
        root.update_idletasks()
        width = max(420, min(860, frame.winfo_reqwidth()))
        root.geometry(f"{width}x{frame.winfo_reqheight()}"
                      f"+{(root.winfo_screenwidth() - width) // 2}+96")
        root.deiconify()

    def read() -> None:
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            root.after(0, apply, message)
        root.after(0, root.destroy)

    threading.Thread(target=read, daemon=True).start()
    root.mainloop()
    return 0
