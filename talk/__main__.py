"""talk: hold the launch key and speak; the text goes into the focused field.

Commands:
    talk run              the daemon (the systemd user service runs this)
    talk transcribe FILE  send a 16 kHz mono WAV through the live path, print the text
    talk hud              the caption box (the daemon starts it)
"""

from __future__ import annotations

import sys


def transcribe(path: str) -> int:
    import queue
    import time
    import wave

    from .daemon import Config
    from .engine import Engine
    from .segment import RATE, Segmenter

    config = Config.load()
    with wave.open(path) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (RATE, 1, 2):
            print("talk: the file must be 16 kHz, mono, 16 bit", file=sys.stderr)
            return 2
        data = w.readframes(w.getnframes())
    results: queue.Queue = queue.Queue()
    engine = Engine(config.model, config.threads, config.languages, results.put)
    engine.start()
    segmenter = Segmenter()
    chunk = RATE // 10 * 2          # 100 ms, as the microphone delivers it
    for start in range(0, len(data), chunk):
        for phrase in segmenter.feed(data[start:start + chunk]):
            engine.submit(1, phrase)
    released = time.monotonic()
    engine.submit(1, segmenter.flush(), last=True)
    texts = []
    while True:
        result = results.get()
        if result.text:
            texts.append(result.text)
        if result.last:
            break
    print(f"talk: {time.monotonic() - released:.2f} s after the end", file=sys.stderr)
    print(" ".join(texts))
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command == "run" and len(argv) == 2:
        from .daemon import main as run
        return run()
    if command == "transcribe" and len(argv) == 3:
        return transcribe(argv[2])
    if command == "hud" and len(argv) == 2:
        from .hud import run as hud
        return hud()
    print(__doc__.strip(), file=sys.stderr)
    return 2


def entry() -> None:
    sys.exit(main(sys.argv))


if __name__ == "__main__":
    entry()
