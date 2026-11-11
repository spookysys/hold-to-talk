# Open issues

## Focus can change after window restoration

The daemon now retains a destination for each dictation. The GNOME extension
restores that window and confirms its focus before the helper requests paste.
A focus change between confirmation and key delivery can still redirect text.
This best-effort behavior was explicitly requested after reviewing the race.
A browser tab or field change within the same window is not detected.

See [window restoration](docs/focus-safe-insertion.md) for the implementation,
source findings, and remaining checks. Firefox and physical-keyboard verification
remain incomplete. A closed window or unavailable extension blocks automatic paste.

## Applications do not confirm paste delivery

Paste operations run in sequence. Each queued dictation keeps its own
destination and paste request.
The helper confirms command completion, not receipt by the focused application.
`voice/output.py::insert` waits one second before another dictation can replace
its clipboard text. Automatic restoration does not remove the text.

An application stalled beyond that interval could read a later dictation instead.
A delivery confirmation from the application would be needed to remove this
assumption. Test GTK, Qt, browsers, and terminals with delayed clipboard reads
on Wayland. Do not describe keyd command completion as confirmed text delivery.

## Target verification remains incomplete

The automated tests use simulated recognition, clipboard contents, and keyd.
They exercise real local sockets and compile the helper with warnings as errors.
The caption process also passed a smoke check on a temporary virtual display.
The installed speech command transcribed the public German sample after conversion
to the required 16 kHz mono PCM format.

Real microphone, keyboard, desktop clipboard, and target resource measurements
remain outstanding. Use the matching daemon and helper for those checks.

## Language keys need a hardware check

During a dictation, voice-keys binds Space, Left, and Right in keyd, and the
release drops the bindings. `keys/test-keyd-select.c` runs this through keyd's
real key handling, and `tests/test_keys.py` checks the helper. Neither uses a
real keyboard. Check on the keyboard: Super+Space before the caption switches
the layout; Space and the arrows during a dictation change the language and
reach no application; after the release, Super+Space and Super+arrows work.
A `keyd bind reset` also drops runtime bindings that the user made.
