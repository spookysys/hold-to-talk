# hold-to-talk

Dictation for Linux. Hold the Super key (the Windows key) and speak. When you
release it, the text goes into the field that has the focus: an editor, a
browser, a terminal.

Speech recognition runs on your computer. No audio and no text leave it. It
uses [Nemotron 3.5 ASR streaming](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
through [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx). It detects the
language by itself; German and English are tested.

The Super key keeps all its other functions. A tap still opens the overview,
and Super plus another key still works as before, also when you press the
other key slowly. hold-to-talk adds no delay to any key.

It runs on Linux with Wayland and [keyd](https://github.com/rvaiya/keyd).
It is tested on GNOME. Other Wayland desktops probably work, but there the
caption box could take the focus, and then the text would go into the box.
An X11 session does not work.

## Use

1. Hold the left Super key. After 0.3 s, a caption box opens at the top of
   the screen.
2. Speak. The box shows the text while you speak.
3. Release the key. The text goes into the focused field about half a second
   later. The microphone runs 0.3 s longer, so your last word is not cut off.

To cancel, press any other key or a mouse button while you hold Super, except
Shift, Ctrl and Alt. Nothing is written. Because Super is held, that key also
does what Super plus that key does, so choose one without a shortcut, such as
Escape. A drag with Super, which moves a window in GNOME, is no dictation
either. A tap on a touchpad does not count as a click here, so press the
touchpad down.

Nothing reaches the field while you hold the key. A text that is ready while
you hold Super for the next dictation waits until you release it.

After a break of 30 minutes, the model is not in memory. Then the first hold
only loads it, in about 3.5 s, and records nothing. The box shows a loading
bar and tells you when to hold the key again.

## How it works

```
keyboard -> keyd -> voice-keys (system service) -> voice (your session) -> clipboard + Shift+Insert
```

- **keyd** owns the keyboard. The Super key is `overload(overlay, leftmeta)`:
  held, it switches to the overlay layer; tapped, it sends Super.
- **voice-keys** is a small C program. It watches the layer through
  `keyd listen` and the keys that keyd sends. During a hold, it also watches
  the mouse buttons. It tells your session only
  `down`, `key` (another key during this hold, not a modifier) and `up`. It runs as a
  throwaway system user with the groups `keyd` and `input`, and no other rights.
- **voice** decides. A hold of 300 ms with no other key is a dictation; any
  other key cancels it. Shift, Ctrl, Alt and Super do not cancel: keyd sends
  Super during the hold itself, and at the release it taps Ctrl, so that
  GNOME does not open the overview. The microphone records from the moment the key goes
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
| first hold after a break | loads the model in about 3.5 s and records nothing; the box says when to hold again |
| after the release | 0.4 to 0.6 s until the text is in the field |
| after 30 idle minutes | the model is freed, and the RAM with it |

## Install

You need:

- keyd
- PipeWire with `pw-record` (Fedora: `pipewire-utils`)
- `wl-clipboard`
- Python's tkinter for the system Python (Fedora: `python3-tkinter`)
- `uv`, a C compiler and `make`

keyd must make the left Super key switch to a layer named `overlay` while you
hold it. `:M` keeps Super on all other keys, so Super+A still opens the app
grid. The tap timeout must equal `hold_ms` (see [Settings](#settings)), so
that a long hold does not open the overview:

```ini
[global]
overload_tap_timeout = 300

[main]
leftmeta = overload(overlay, leftmeta)

[overlay:M]
```

If keyd has no configuration yet, `install-system.sh` writes
`/etc/keyd/default.conf` with these lines. It does not change a configuration
that you already have: if these lines are missing, it shows them and stops,
and you add them yourself.

Then run, from this directory:

```
./install.sh                              # program, model, user service
pkexec ./install-system.sh "$(id -un)"    # key helper, keyd setting (root)
```

`install.sh` downloads the model, about 650 MB, to
`~/.local/share/voice/models`.

The service starts by itself each time you log in. The command to control it
is `voice` (see [Commands](#commands)).

### Uninstall

```
systemctl --user disable --now voice.service
rm ~/.config/systemd/user/voice.service
uv tool uninstall voice
rm -r ~/.local/share/voice
sudo systemctl disable --now "voice-keys@$(id -un).socket"
sudo rm /usr/local/libexec/voice-keys /etc/systemd/system/voice-keys@.socket /etc/systemd/system/voice-keys@.service
```

If `/etc/keyd/default.conf` starts with `# Written by hold-to-talk`, remove
it and run `sudo keyd reload`.

## Settings

`~/.config/voice/config.toml`, all optional:

```toml
language = "auto"          # or a code such as "de" or "en"
languages = ["de", "en"]   # yours: a text in another script is decoded again in these
threads = 2                # more threads are slower on CPUs with few fast cores
idle_minutes = 30          # then the model is freed
hold_ms = 300              # must match overload_tap_timeout in keyd
max_seconds = 120          # a longer dictation ends here; the text waits for the release
```

## Commands

`voice` alone lists them: `start`, `stop`, `restart`, `unload` (free the
model now), `status`, `log`, and `transcribe FILE.wav` for tests.

## Tests

```
uv run pytest
```

## License

MIT. See [LICENSE](LICENSE).
