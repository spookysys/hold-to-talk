# Open issues

## Applications do not confirm paste delivery

Paste operations run in sequence. Queued dictations use one paste where possible.
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
