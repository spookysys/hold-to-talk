#!/bin/sh
# Install the root part of voice: the key helper, its units, and the keyd setting.
# Usage: pkexec ./install-system.sh USER   (or sudo)
set -eu

user=${1:-}
[ -n "$user" ] || { echo "usage: install-system.sh USER" >&2; exit 2; }
id "$user" >/dev/null
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
cd "$(dirname "$0")"
[ keys/voice-keys -nt keys/voice-keys.c ] || { echo "build first: make -C keys" >&2; exit 1; }

# keyd must switch the left Super key to the layer "overlay" while it is held.
# ":M" keeps Super on all other keys, so Super+A still opens the app grid.
# A long hold must not open the overview on release, so keyd's tap timeout
# must equal hold_ms in the voice configuration.
# We write a configuration only where none exists; we never change the user's.
required='[global]
overload_tap_timeout = 300

[main]
leftmeta = overload(overlay, leftmeta)

[overlay:M]'
mapping='^[[:space:]]*leftmeta[[:space:]]*=[[:space:]]*overload\(overlay,[[:space:]]*leftmeta\)'
tap='^[[:space:]]*overload_tap_timeout[[:space:]]*=[[:space:]]*300[[:space:]]*$'
if ! ls /etc/keyd/*.conf >/dev/null 2>&1; then
	conf=/etc/keyd/default.conf
	mkdir -p /etc/keyd
	printf '# Written by hold-to-talk.\n[ids]\n*\n\n%s\n' "$required" > "$conf"
else
	conf=$(grep -El "$mapping" /etc/keyd/*.conf | head -n 1)
	if [ -z "$conf" ] || ! grep -q '^\[overlay:M\]' "$conf" || ! grep -Eq "$tap" "$conf"; then
		echo "Your keyd configuration for the keyboard must contain these lines." >&2
		echo "hold-to-talk does not change it for you:" >&2
		echo "$required" | sed 's/^/  /' >&2
		[ -n "$conf" ] && grep -q '^\[overlay\]' "$conf" &&
			echo "In $conf, rename [overlay] to [overlay:M]: without :M, Super+A types an a." >&2
		exit 1
	fi
fi
grep -Eq '^[[:space:]]*disable_modifier_guard[[:space:]]*=[[:space:]]*1' "$conf" &&
	echo "warning: disable_modifier_guard = 1 in $conf; the overview can open after each dictation" >&2

install -D -m 0755 keys/voice-keys /usr/local/libexec/voice-keys
install -m 0644 keys/voice-keys@.socket keys/voice-keys@.service /etc/systemd/system/

# `keyd check` is no gate: keyd 2.6 exits with an error for a mere warning.
if systemctl is-active --quiet keyd; then
	keyd reload
else
	echo "keyd is not running. Start it: systemctl enable --now keyd" >&2
fi

systemctl daemon-reload
systemctl enable --now "voice-keys@$user.socket"
echo "voice-keys installed for $user"
