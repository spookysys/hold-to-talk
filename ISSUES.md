# Open issues

## Focus changes can send dictation to another application

A user reported that an automated application launch takes focus during a hold.
Releasing Super then inserts the dictation into the newly focused application.

`voice/daemon.py` retains text without a destination window identity.
`voice/output.py` fills the clipboard, then keyd sends Shift+Insert to current focus.
The same problem affects delayed recognition and queued dictations after release.

The fix must retain the intended destination for each dictation.
A focus check before key injection alone leaves a race with another focus change.
Desktop integration must prevent delivery to a different application, including
when focus changes during recognition, clipboard preparation, or queued output.
If delivery is blocked, retain the text and clearly explain how to recover it.
The requested behavior is to return to the window captured at key-down and
insert there after release. A later focus change must not redirect the text.
See [the insertion investigation](docs/focus-safe-insertion.md).

Verify with two windows and an automated focus change during each stage above.
Also test a closed destination, unavailable focus tracking, and successive
holds that begin in different windows. No fix or hardware check is complete yet.

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
