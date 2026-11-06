#!/bin/sh
# Install the root part of voice: the key helper, its units, and the keyd setting.
# Usage: pkexec ./install-system.sh USER   (or sudo)
set -eu

user=${1:-}
[ -n "$user" ] || { echo "usage: install-system.sh USER" >&2; exit 2; }
id "$user" >/dev/null
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
cd "$(dirname "$0")"
[ -x keys/voice-keys ] || { echo "build first: make -C keys" >&2; exit 1; }

install -D -m 0755 keys/voice-keys /usr/local/libexec/voice-keys
install -m 0644 keys/voice-keys@.socket keys/voice-keys@.service /etc/systemd/system/

# A long hold of the launch key must not open the overview on release.
# The value must match hold_ms in the voice configuration.
conf=/etc/keyd/default.conf
if ! grep -q '^overload_tap_timeout' "$conf"; then
	cp -p "$conf" "$conf.before-voice"
	if grep -q '^\[global\]' "$conf"; then
		sed -i '/^\[global\]/a overload_tap_timeout = 300' "$conf"
	else
		printf '[global]\noverload_tap_timeout = 300\n\n' | cat - "$conf.before-voice" > "$conf"
	fi
fi
# `keyd check` is no gate: keyd 2.6 exits with an error for a mere warning.
keyd reload

systemctl daemon-reload
systemctl enable --now "voice-keys@$user.socket"
echo "voice-keys installed for $user"
