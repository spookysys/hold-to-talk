# talk

Hold the launch key and speak. When you release the key, the text goes into
the field that has the focus: an editor, a browser, a terminal.

The launch key keeps all its other functions. A tap still opens the overview,
and launch plus another key still works as before, also when you press the
other key slowly. talk adds no delay to any key.

It runs on Linux with GNOME on Wayland and [keyd](https://github.com/rvaiya/keyd).
Speech recognition is local: [whisper.cpp](https://github.com/ggml-org/whisper.cpp)
with the Whisper `small` model, in German and English.

## How it works

```
keyboard -> keyd -> talk-keys (system service) -> talk (your session) -> clipboard + Shift+Insert
```

- **keyd** owns the keyboard. The launch key is `overload(overlay, leftmeta)`:
  held, it switches to the overlay layer; tapped, it sends the launch key.
- **talk-keys** is a small C program. It watches the layer through
  `keyd listen` and the keys that keyd sends. It tells your session only
  `down`, `key` (another key during this hold) and `up`. It runs as a
  throwaway system user with the groups `keyd` and `input`, and no other rights.
- **talk** decides. A hold of 300 ms with no other key is a dictation; any
  other key cancels it. The microphone records from the moment the key goes
  down, so the start of the first word is not lost. keyd uses the same
  300 ms (`overload_tap_timeout`): a longer hold does not open the overview.
- While you speak, talk cuts the audio at long pauses and transcribes each
  phrase once. After the release, only the last phrase must wait. A caption
  box shows what talk hears.
- The text goes into the clipboard and the primary selection, talk-keys makes
  keyd send Shift+Insert, and the old clipboard text comes back after a second.

## Resources

Measured on an Intel i3-1215U with 8 GB RAM:

| | |
|---|---|
| idle | 0 % CPU, about 35 MB RAM |
| dictation | about 540 MB RAM; the model loads in about 1.2 s while you start to speak |
| after the release | 2 to 3 s for a phrase of 10 s; less when you pause between sentences |
| after 5 idle minutes | the model is freed; RAM goes back to about 55 MB |

## Install

You need keyd, PipeWire (`pw-record`), `wl-clipboard`, `uv`, a C compiler and `make`.

```
./install.sh                              # program, model, user service
pkexec ./install-system.sh "$(id -un)"    # helper, keyd setting (root)
```

`install-system.sh` saves the old keyd configuration as
`/etc/keyd/default.conf.before-talk`.

## Settings

`~/.config/talk/config.toml`, all optional:

```toml
languages = ["de", "en"]   # the first is the fallback for an unclear phrase
threads = 6
idle_minutes = 5           # then the model is freed
hold_ms = 300              # must match overload_tap_timeout in keyd
```

## Commands

```
talk run                  # the daemon; the user service runs it
talk transcribe FILE.wav  # a 16 kHz mono WAV through the same path, for tests
journalctl --user -u talk # what it heard and how long it took
```

## Tests

```
uv run pytest
```
