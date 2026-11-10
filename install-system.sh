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
[ build/keyd-guard/bin/keyd -nt keys/keyd-guard.patch ] || {
    echo "build first: ./keys/build-keyd.sh" >&2
    exit 1
}

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
install -D -m 0755 build/keyd-guard/bin/keyd /usr/local/libexec/voice-keyd
install -D -m 0644 build/keyd-guard/LICENSE /usr/local/share/voice/keyd.LICENSE
install -D -m 0644 keys/keyd-voice.conf /etc/systemd/system/keyd.service.d/voice.conf
install -m 0644 keys/voice-keys@.socket keys/voice-keys@.service /etc/systemd/system/

# Restart keyd so the guard runs in the process that injects the keys.
systemctl daemon-reload
systemctl restart keyd

systemctl enable --now "voice-keys@$user.socket"
systemctl try-restart "voice-keys@$user.service"
echo "voice-keys installed for $user"
