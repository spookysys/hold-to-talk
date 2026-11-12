# Open issues

## Focus can change after window restoration

The daemon now retains a destination for each dictation. The GNOME extension
restores that window and confirms its focus before the helper requests paste.
A focus change between confirmation and key delivery can still redirect text.
This best-effort behavior was explicitly requested after reviewing the race.
A browser tab or field change within the same window is not detected.

See [window restoration](docs/focus-safe-insertion.md) for the implementation,
source findings, and remaining checks. When restoration fails, the text stays in
the clipboard and the caption shows the reason. On the real keyboard, a paste
into the window that had the focus works. Focus changes during a dictation and
Firefox are not checked yet.

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

## Speed on battery is not measured

Speech must decode faster than it arrives, also on battery while other programs
run. On mains power under load, the target laptop decodes one second of speech
in about 0.46 s (see `voice/engine.py`), and the text is ready 0.25 to 0.47 s
after the release. Nobody has measured on battery.

Check: on battery, with other programs busy, dictate for a minute or more. The
journal line "text ready X s after the microphone stopped" must stay below about
half a second. A value that grows with the length of the dictation means that
decoding falls behind.

## A silent hold keeps the caption for about 0.6 s

After a hold with no speech, the caption stays 0.6 to 0.75 s after the release.
The microphone runs 0.3 s longer (`AFTER_S`), then the engine decodes the rest
of the audio with 0.5 s of silence after it. No text before the release does not
prove silence: the model works in pieces of 560 ms, so a short word in a short
hold has no text yet at the release.

Proposed: when there is no text at the release, hide the caption at once and let
the decode go on. A late word is still pasted, but it arrives after the caption
has gone. A check of the audio level could skip the decode, but it would lose
quiet speech.

## Language keys need a hardware check

During a dictation, voice-keys binds Space, Left, and Right in keyd, and the
release drops the bindings. `keys/test-keyd-select.c` runs this through keyd's
real key handling, and `tests/test_keys.py` checks the helper. On the real
keyboard, Space and the arrows change the language during a dictation. Still to
check on the keyboard: Super+Space before the caption switches the layout;
Space and the arrows reach no application during a dictation; after the
release, Super+Space and Super+arrows work.
A `keyd bind reset` also drops runtime bindings that the user made.
