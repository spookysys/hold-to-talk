#!/bin/sh
# Install the root part of talk: the key helper, its units, and the keyd setting.
# Usage: pkexec ./install-system.sh USER   (or sudo)
set -eu

user=${1:-}
[ -n "$user" ] || { echo "usage: install-system.sh USER" >&2; exit 2; }
id "$user" >/dev/null
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
cd "$(dirname "$0")"
[ -x keys/talk-keys ] || { echo "build first: make -C keys" >&2; exit 1; }

install -D -m 0755 keys/talk-keys /usr/local/libexec/talk-keys
install -m 0644 keys/talk-keys@.socket keys/talk-keys@.service /etc/systemd/system/

# A long hold of the launch key must not open the overview on release.
# The value must match hold_ms in the talk configuration.
conf=/etc/keyd/default.conf
if ! grep -q '^overload_tap_timeout' "$conf"; then
	cp -p "$conf" "$conf.before-talk"
	if grep -q '^\[global\]' "$conf"; then
		sed -i '/^\[global\]/a overload_tap_timeout = 300' "$conf"
	else
		printf '[global]\noverload_tap_timeout = 300\n\n' | cat - "$conf.before-talk" > "$conf"
	fi
fi
# `keyd check` is no gate: keyd 2.6 exits with an error for a mere warning.
keyd reload

# dsh-voice let every program of the user write /dev/uinput and read the keyd
# virtual keyboard. talk needs neither.
rules=/etc/udev/rules.d/70-dsh-voice.rules
if [ -e "$rules" ]; then
	rm "$rules"
	udevadm control --reload
	udevadm trigger --action=change /sys/devices/virtual/misc/uinput
	for event in /sys/class/input/event*; do
		if [ "$(cat "$event/device/name" 2>/dev/null)" = "keyd virtual keyboard" ]; then
			udevadm trigger --action=change "$event"
		fi
	done
fi

systemctl daemon-reload
systemctl enable --now "talk-keys@$user.socket"
echo "talk-keys installed for $user"
