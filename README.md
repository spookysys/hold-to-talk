# hold-to-talk - local voice typing for Linux

**Hold the Super key, speak, and release to insert your words.**

**Your voice never leaves your computer.** Recognition runs on your own CPU.
Nothing is sent to a cloud service, and it works without a network connection.

hold-to-talk provides local speech-to-text and push-to-talk dictation on Linux
Wayland. Use voice typing in an editor, a browser, a chat window, or a terminal.
Before pasting, it returns to the window where the hold started.

Speech recognition runs locally through
[sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), using
[Nemotron 3.5 ASR streaming](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b).
It needs no API key or speech service. German and English have been tested.

- See a live caption and microphone level while you speak.
- Keep Super shortcuts: a tap opens the GNOME overview; Super plus another key cancels dictation and keeps the shortcut.
  The text of a canceled dictation goes to the clipboard.
- Insert only after release. The caption never edits your text.
- Free the speech model after 30 idle minutes, or sooner when memory pressure swaps it out.

The tested setup is **GNOME Wayland (Fedora 44)**, with PipeWire, systemd, and
[keyd](https://github.com/rvaiya/keyd). X11 sessions are not supported.
Window restoration currently needs the included GNOME 50 extension. Other Wayland
desktops have no restoration integration yet; text remains available for manual paste.

## Use

After installation:

1. Focus the field where you want the text.
2. Hold the **left Super key** (usually the Windows key).
3. Speak when the caption box appears, after about 0.3 seconds.
4. Release Super. The app restores the original window, then pastes the recognized text.

On the first use, and after the model has unloaded, the caption shows a loading
bar. Speak as usual. The text appears when the model is ready, a few seconds later.

If you speak more than one language, list them in the [settings](#settings).
The caption then shows them, with the chosen one marked. While the caption is
visible, press Space or the arrow keys, still holding Super, to choose another
language. The text so far is recognized again in the new language. Voice
remembers the choice for the next dictation. Before the caption appears,
Super+Space still switches the keyboard layout as usual.

To cancel, press Escape while holding Super. The text is not pasted; it goes
to the clipboard, and the caption says so for 5 seconds.
Any other key or a mouse button also cancels, and still performs its Super
shortcut. Shift, Ctrl, and Alt do not cancel.
A touchpad tap does not count as a mouse button; press the touchpad down.

The microphone starts at key-down. It continues for 0.3 seconds after release
to capture the last syllable. If earlier text becomes ready during a new hold,
it waits for release before pasting.

Dictation replaces both the clipboard and the primary selection, which some
terminals use. **The dictated text stays there; the old clipboard is not restored.**

### Dictate prompts to AI coding assistants

Use voice input to explain a bug, describe a change, or draft a longer prompt.
Focus your coding assistant's text field, then hold Super and speak.
No assistant-specific plugin is required; the application must accept pasting
with Shift+Insert. Check the recognized text before submitting a prompt,
especially code identifiers and commands. Individual AI clients have not been
separately verified.

See [Voice typing for AI coding assistants on Linux](docs/voice-typing-ai-coding-assistants.md)
for a complete workflow and example prompts.

## Install

You need:

- GNOME 50 on Wayland for window restoration, with XWayland for the caption window.
- System Python with PyGObject, and `gdbus`, for extension setup and communication.
- systemd and keyd.
- PipeWire with `pw-record` (Fedora package: `pipewire-utils`).
- `wl-clipboard`.
- Tkinter for the system Python (Fedora package: `python3-tkinter`).
- `git`, `uv`, a C compiler, `make`, `curl`, `patch`, `tar`, `bzip2`, and `sha256sum`.
- Administrator access for the system helper.

Installation downloads Python dependencies, keyd source, and about 650 MB of
model files. Recognition needs no network connection after installation.

### 1. Get the source

```sh
git clone https://github.com/spookysys/hold-to-talk.git
cd hold-to-talk
```

### 2. Check the keyd configuration

The left Super key must activate an `overlay` layer. Add these entries to your
keyboard's existing file under `/etc/keyd/`:

```ini
[global]
overload_tap_timeout = 300

[main]
leftmeta = overload(overlay, leftmeta)

[overlay:M]
```

Merge the entries with existing sections rather than replacing your key mappings.
`:M` preserves Super in shortcuts. The tap timeout must match `hold_ms` in
[Settings](#settings).

If no keyd configuration exists, the system installer creates a default file.
If an existing configuration lacks these entries, it stops and prints what to add.

### 3. Install both parts

Run the user installer as your normal user, then the system installer as root:

```sh
./install.sh
pkexec ./install-system.sh "$(id -un)"
voice status
```

If `pkexec` is unavailable, use `sudo ./install-system.sh "$(id -un)"`.
The `voice` command is installed in `~/.local/bin`; include that directory in your `PATH`.

The user installer installs the program, model, and window-restoration extension.
It enables `voice.service`, which starts automatically when you log in.
After the first extension installation or an extension update, log out and back in.
The installer leaves an existing voice process running until that login.
Otherwise, it restarts the service immediately.

The system installer installs `voice-keys` and a patched keyd binary.
A systemd override selects this binary and restarts keyd. The distribution's
binary and your key mappings remain in place.

The patch checks the hold inside keyd before injecting the paste shortcut.
It prevents a new hold between an external check and key injection.
See [the key helper design](keys/README.md) for details and tests.

## Update or restart

To download and install updates, run these commands in your clone:

```sh
git pull --ff-only
./install.sh
pkexec ./install-system.sh "$(id -un)"
```

Update both parts together: the daemon and helper use the same paste protocol.
The installers restart affected services, except when the GNOME extension needs
a new login. The user installer prints that requirement.

To reload the **already installed** version or changed settings:

```sh
voice restart
```

Restarting does not download or install updates.

## Commands

| Command | Purpose |
|---|---|
| `voice status` | Show service state, model state, and daemon memory use. |
| `voice start` | Start the user service. |
| `voice stop` | Stop the user service. |
| `voice restart` | Restart the installed program. |
| `voice unload` | Request that the model be freed. |
| `voice log` | Follow local logs, including recognized text. |
| `voice transcribe FILE.wav` | Test recognition without the keyboard or clipboard. |

The transcription command requires a 16 kHz, mono, 16-bit PCM WAV file.
Run `voice` without arguments to list the commands.

## Settings

Create `~/.config/voice/config.toml` to override the defaults. All settings are optional.
Restart voice after editing the file.

```toml
languages = ["auto"]       # for example ["en", "de", "nb", "auto"]
threads = 2
idle_minutes = 30
hold_ms = 300              # must match keyd's overload_tap_timeout
```

`languages` lists the languages to choose from during a dictation. Codes are two
letters, such as "de", "en", or "nb" for Norwegian. "auto" lets the model guess;
it can guess wrong, for example Swedish for Norwegian, and it does not say what
it guessed. The first entry applies until you choose another one.
Increasing the thread count is not necessarily faster on a small CPU.

## Privacy and limitations

Audio is processed locally. Recognized text goes to your focused application,
the clipboard, and the local systemd journal. `voice log` displays that journal.
The destination application controls what happens to text after insertion.

The app remembers the window when the daemon receives key-down. Before each
paste, the extension activates that window and confirms its focus. This is a
best-effort check: another focus change after confirmation can still redirect
paste. It does not remember a browser tab, document, or field within the window.

If the window has closed or the extension is unavailable, voice does not paste
anywhere else. The text remains in the clipboard, and the caption says why for
5 seconds. A later dictation or copy can replace that clipboard text.

Paste requests run in sequence, with at least one second between them.
Applications do not confirm text delivery; a stalled application can still read a later clipboard value.
See [open issues and outstanding hardware checks](ISSUES.md).

Earlier measurements on an Intel i3-1215U with 8 GB RAM showed about 35 to 45 MB
of daemon memory while unloaded, and about 730 MB while the model is loaded.
Idle CPU use was 0%, with insertion usually 0.4 to 0.6 seconds after release.
These are reference measurements, not guarantees for the latest build or other computers.

## Troubleshooting

- **Nothing happens:** run `voice status`, then `voice log` for diagnostics.
- **The installer rejects the key mapping:** merge the printed entries into your existing keyd configuration, then rerun it.
- **The caption says "log out and back in to restore windows":** GNOME Shell loads a new or updated extension only at login. `voice status` also reports it.
- **The caption says "window changed":** the original window closed or did not take the focus. Paste the text from the clipboard.
- **The caption works but paste fails:** install both parts again and check the log for helper errors.
- **`voice` is not found:** check that `~/.local/bin` is in your `PATH`.

## How it works

```text
keyboard -> keyd -> voice-keys -> voice -> clipboard + Shift+Insert
```

keyd handles the keyboard. The system helper reports holds, releases, and
cancellation events to the session process. The session process streams microphone
audio to the local model and displays captions in a separate process.

The session process has no access to input devices or `/dev/uinput`.
The daemon uses a GNOME Shell extension to restore the original window.
The extension exposes opaque window tokens, not titles or field contents.
The helper requests the guarded paste through keyd after release.
See [window restoration](docs/focus-safe-insertion.md) for its remaining race.

## Development

```sh
uv run pytest
```

The tests use simulated recognition and clipboard data, local sockets, and a
fake keyd command. `./keys/build-keyd.sh` also runs upstream keyboard simulations
and tests the guarded command. An isolated GNOME test is available in
`tests/gnome_integration.py`.
Real keyboard and Firefox checks remain necessary.

Bug reports should include your desktop, session type, and reproduction steps.
Remove dictated text from logs before sharing them.

## Uninstall

```sh
gnome-extensions disable hold-to-talk@spookysys.github.io
rm -r ~/.local/share/gnome-shell/extensions/hold-to-talk@spookysys.github.io
systemctl --user disable --now voice.service
rm ~/.config/systemd/user/voice.service
systemctl --user daemon-reload
uv tool uninstall voice
rm -r ~/.local/share/voice
sudo systemctl disable --now "voice-keys@$(id -un).socket"
sudo systemctl stop "voice-keys@$(id -un).service"
sudo rm /usr/local/libexec/voice-keys /etc/systemd/system/voice-keys@.socket /etc/systemd/system/voice-keys@.service
sudo rm /etc/systemd/system/keyd.service.d/voice.conf
sudo systemctl daemon-reload
sudo systemctl restart keyd
sudo rm /usr/local/libexec/voice-keyd /usr/local/share/voice/keyd.LICENSE
```

If `/etc/keyd/default.conf` starts with `# Written by hold-to-talk`, remove
that generated file and run `sudo keyd reload`. Keep any configuration you maintained yourself.

## License

MIT. See [LICENSE](LICENSE). Model and dependency licenses apply separately.
