# Voice typing for AI coding assistants on Linux

Use hold-to-talk to dictate a prompt into an AI coding assistant on Linux Wayland.
Hold the left Super key, speak, and release to paste the recognized text.
Speech recognition runs on your computer, without an API key or speech service.

This is useful when a prompt needs more explanation than code: describe a bug,
explain the intended behavior, or ask for a review. You can speak the explanation
and type exact names afterward.

[hold-to-talk is open source](https://github.com/spookysys/hold-to-talk).
It provides local speech-to-text and push-to-talk voice input through the clipboard.
It does not require an integration with a particular AI assistant.

## Set up voice input

Follow the [installation guide](../README.md#install). It lists the dependencies,
key mapping, and two installation commands. Read the system-install section:
it installs a patched keyd binary through a service override.

The tested setup is GNOME Wayland (Fedora 44). Other Wayland desktops are unverified.
Your target application must accept
clipboard pasting with Shift+Insert. Individual AI clients have not been separately
verified, so check the text field you intend to use.

After installation, check the service:

```sh
voice status
```

For the first use, hold Super once to load the model. Wait for the ready message.
That first hold does not record a dictation.

## Dictate a prompt

Open a new prompt in your coding assistant. Keep that field focused until the
paste finishes. Hold the left Super key and speak, then release it.

For example:

> Please review this change for bugs. Focus on cancellation and error handling.
> Explain any problems before changing the code.

The caption shows the recognized words while you speak. Nothing is inserted
into the prompt until release. The app pastes text; it does not press Enter
or submit the prompt for you.

Read the result before sending. Correct names, paths, and code identifiers with
the keyboard. Speech is useful for explaining intent; exact syntax still needs
inspection.

For a planning request, try:

> I want users to cancel an upload without losing the rest of the form.
> Explain how the current upload works, then suggest the smallest change.

For a bug report, try:

> The Save button stays disabled after I change the project name.
> Find the condition that controls it. Explain the cause before editing anything.

These are example prompts, not recorded recognition results or compatibility tests.

## Keep control of the interaction

To cancel a dictation, press another key or a mouse button while holding Super.
The key still performs its Super shortcut. Choose a key without an assigned
shortcut, such as Escape. Shift, Ctrl, and Alt do not cancel.

Use the keyboard to edit the pasted text. Submit the prompt only when you are
satisfied with it. If you change focus before the paste, the text can go into
the newly focused field instead.

The dictated text stays in the clipboard and primary selection. The previous
clipboard is not restored. Recognized text also appears in the local systemd
journal. Local recognition does not change the privacy policy of the AI assistant
where you submit the prompt.

## If it does not paste

Run `voice status` and inspect `voice log`. The log includes recognized text.
Check the same clipboard shortcut in a normal text editor. If it works there
but not in the assistant, investigate that application's paste behavior.

The model unloads after an idle period. A later first hold loads it again;
wait for the ready message before dictating. The [README](../README.md#settings)
explains the settings, service commands, and update procedure.

Applications do not confirm paste delivery. A stalled application can read a later
clipboard value. See [known limitations](../ISSUES.md) before relying on unattended
input.

## Show the workflow

A short recording can show the held key, live caption, release, and inserted prompt.
The [demo recording checklist](demo-recording.md) uses an example prompt and keeps
the actual recognition result visible. A recording has not been added yet.
