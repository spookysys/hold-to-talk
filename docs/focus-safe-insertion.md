# Insert into the original destination

## Current behavior

The daemon captures a window token when it receives Super key-down. Capture
runs in a worker. The GNOME extension uses a bounded focus history to resolve
the window at that timestamp, even if focus changes before the worker runs.
The timestamp follows helper delivery; it is not the physical key's timestamp.

Each dictation retains its token through recognition and deferred output.
The output worker prepares the clipboard. The extension then activates the
original window and checks focus for at most 500 milliseconds. After confirmation,
the daemon drains key events and requests the existing guarded paste.
A new hold still defers the paste.

The extension identifies windows without reading their titles or contents.
Tokens include an extension-session identifier. Closed windows and tokens from
a previous extension session cannot select another window.

When restoration fails, voice never pastes into another window. Text stays in
the clipboard until another copy or dictation replaces it. The caption shows the
reason for 5 seconds: "window changed", or, when GNOME Shell has not loaded the
extension, "log out and back in". A new login is required after installing or
updating the extension; `voice status` reports a missing one.

## Remaining limitation

The user selected best-effort restoration after reviewing the race below.
Another application can take focus after confirmation but before key delivery.
Restoration reduces this problem; it does not provide atomic delivery.
The original browser tab, document, and field are not retained separately.

The launch key retains its existing handling. Nothing is pasted before release.
The integration currently targets GNOME 50. Other desktops need their own
restoration integration; unavailable integration leaves text for manual paste.

## Evidence

The installed desktop reports GNOME Shell 50.6 and Mutter 50.6 on Fedora 44.
The following findings come from source inspection, not a desktop race test.

- The output worker sets both clipboard selections, then requests Shift+Insert.
- The keyd guard checks the hold state. It does not check the destination.
- Mutter's virtual keyboard queues work through its input thread. Calling it
  after window activation does not make activation and delivery atomic.
  See [the 50.6 implementation](https://github.com/GNOME/mutter/blob/50.6/src/backends/native/meta-virtual-input-device-native.c).
- Mutter handles application events in its Clutter event filter. Filters run
  in registration order, before ordinary event signals. A later Shell handler
  cannot reliably guard events already delivered by Mutter.
  See [event routing](https://github.com/GNOME/mutter/blob/50.6/src/core/events.c)
  and [filter ordering](https://github.com/GNOME/mutter/blob/50.6/clutter/clutter/clutter-event.c).
- `Clutter.InputMethod.commit` also queues an event. The direct
  `clutter_input_focus_commit` function is private. These are not a supported
  extension API for inserting into a retained arbitrary destination.
  See [input method delivery](https://github.com/GNOME/mutter/blob/50.6/clutter/clutter/clutter-input-method.c)
  and [the private declaration](https://github.com/GNOME/mutter/blob/50.6/clutter/clutter/clutter-input-focus-private.h).

A check followed by global key injection still permits a focus change between
those operations. A shorter delay or another check does not remove that race.

## Candidate approaches

An application can expose a specific editable object through accessibility.
[AT-SPI insert_text](https://gnome.pages.gitlab.gnome.org/at-spi2-core/libatspi/method.EditableText.insert_text.html)
addresses such an object directly. This could avoid global keyboard routing,
but the affected application's implementation needs testing. Capturing the
object, preserving the insertion position, and handling selections also need checks.
It must not silently fall back to global key injection when unsupported.

A general desktop implementation needs destination-aware delivery inside the
compositor, or another interface that binds insertion to the intended client.
No supported GNOME 50.6 operation with that guarantee was identified in this
investigation. A compositor change would require a separate design and deployment
plan. This investigation does not establish that such a change is impossible.

## Firefox findings

Firefox's ATK adapter exposes targeted editable text operations.
However, [Mozilla bug 2087495](https://bugzilla.mozilla.org/show_bug.cgi?id=2087495)
reports that these operations returned success without editing text.
The report marks the fix for Firefox 161. Do not infer that an installed
release contains the fix without checking its version and behavior.

The [current adapter source](https://github.com/mozilla-firefox/firefox/blob/main/accessible/atk/nsMaiInterfaceEditableText.cpp)
passes insertion to the selected accessible object's `InsertText` method.
This is a candidate for testing, not a verified delivery guarantee.
The installed Firefox version and a disposable-field test remain necessary.

## Checks before delivery

Use disposable fields containing synthetic text. Test focus changes during the
hold, recognition, activation, and final delivery. Repeat with different windows
of the same application and with two dictations from different destinations.

Test closing the original window, opening a modal dialog, locking the session,
and changing the selected field or document within the original window.
Distinguish window identity from the identity of a field inside that window.

Verify delayed application handling and clipboard replacement separately.
A key injection acknowledgment is not proof that an application inserted text.
Automated daemon and focus-history checks pass. The isolated GNOME 50.6 test
restores an earlier synthetic window, resolves a delayed capture, and rejects
a closed window. Run it with `/usr/bin/python3 tests/gnome_integration.py`.
On the real keyboard, a paste into the window that had the focus works.
Focus changes during a dictation and Firefox are not checked yet.
