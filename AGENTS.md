# talk

Visibility: publishable

Push-to-talk dictation for GNOME on Wayland with keyd. README.md explains the
design for users; read it first.

## Requirements that hold every change

- The launch key must behave exactly as without talk: no delay, no lost key,
  no extra key. Key combinations with the launch key must work at any speed.
  A change that cannot meet this is wrong, however good its other effects.
- The target laptop has little RAM and a slow CPU. Idle cost is zero CPU and a
  few tens of MB. Measure a change on that laptop before you claim it is faster.
- Nothing reaches the focused field until the release. A preview that is wrong
  must never damage the user's text.
- Keep privileges narrow. The session process needs no access to `/dev/uinput`
  or to input devices; talk-keys sends nothing but the three events.

## Layout

- `keys/` the C helper and its systemd units (system part, root installs it)
- `talk/` the Python daemon (user part)
- `tests/` unit tests: `uv run pytest`

## Tests and checks

- `uv run pytest` for the hold logic and the segmenter.
- `talk transcribe FILE.wav` for the speech path without the key.
- The key behavior can only be checked on the real keyboard. Say which checks
  ran on hardware and which did not.
- Test voices must be natural: a person or a natural text-to-speech service,
  never espeak.
