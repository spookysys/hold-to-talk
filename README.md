# voice

Hold the launch key and speak. When you release the key, the text goes into
the field that has the focus: an editor, a browser, a terminal.

The launch key keeps all its other functions. A tap still opens the overview,
and launch plus another key still works as before, also when you press the
other key slowly. voice adds no delay to any key.

It runs on Linux with GNOME on Wayland and [keyd](https://github.com/rvaiya/keyd).
Speech recognition is local and streaming:
[Nemotron 3.5 ASR streaming](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
through [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx). It detects the
language by itself; German and English are tested.

## How it works

```
keyboard -> keyd -> voice-keys (system service) -> voice (your session) -> clipboard + Shift+Insert
```

- **keyd** owns the keyboard. The launch key is `overload(overlay, leftmeta)`:
  held, it switches to the overlay layer; tapped, it sends the launch key.
- **voice-keys** is a small C program. It watches the layer through
  `keyd listen` and the keys that keyd sends. It tells your session only
  `down`, `key` (another key during this hold) and `up`. It runs as a
  throwaway system user with the groups `keyd` and `input`, and no other rights.
- **voice** decides. A hold of 300 ms with no other key is a dictation; any
  other key cancels it. The microphone records from the moment the key goes
  down, so the start of the first word is not lost. keyd uses the same
  300 ms (`overload_tap_timeout`): a longer hold does not open the overview.
- The audio goes into the model in pieces as it arrives, and the text grows
  while you speak. A caption box shows it, with a level bar for the
  microphone. After the release, only the last piece is left.
- The text goes into the clipboard and the primary selection, voice-keys makes
  keyd send Shift+Insert, and the old clipboard text comes back after a second.

## Resources

Measured on an Intel i3-1215U with 8 GB RAM:

| | |
|---|---|
| idle | 0 % CPU, about 35 MB RAM |
| dictation | about half of 2 CPU threads while you speak; about 1 GB RAM |
| first dictation after a break | the model loads in about 3.5 s; the text then catches up |
| after the release | 0.1 to 0.3 s until the text is in the field |
| after 30 idle minutes | the model is freed, and the RAM with it |

## Install

You need keyd, PipeWire (`pw-record`), `wl-clipboard`, `uv`, a C compiler and `make`.

```
./install.sh                              # program, model, user service
pkexec ./install-system.sh "$(id -un)"    # helper, keyd setting (root)
```

`install-system.sh` saves the old keyd configuration as
`/etc/keyd/default.conf.before-voice`.

## Settings

`~/.config/voice/config.toml`, all optional:

```toml
language = "auto"          # or a code such as "de" or "en"
threads = 2                # more threads are slower on CPUs with few fast cores
idle_minutes = 30          # then the model is freed
hold_ms = 300              # must match overload_tap_timeout in keyd
```

## Commands

`voice` alone lists them: `start`, `stop`, `restart`, `unload` (free the
model now), `status`, `log`, and `transcribe FILE.wav` for tests.

## Tests

```
uv run pytest
```
