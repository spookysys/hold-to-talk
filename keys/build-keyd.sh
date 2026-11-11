#!/bin/sh
# Build the fixed keyd release with the guarded injection command.
set -eu
cd "$(dirname "$0")/.."
archive=build/keyd-2.6.0.tar.gz
keyd_source=build/keyd-guard
mkdir -p build
if [ ! -f "$archive" ]; then
    curl --fail --location --max-time 60 \
        https://codeload.github.com/rvaiya/keyd/tar.gz/refs/tags/v2.6.0 -o "$archive.tmp"
    mv "$archive.tmp" "$archive"
fi
printf '%s  %s\n' 697089681915b89d9e98caf93d870dbd4abce768af8a647d54650a6a90744e26 "$archive" | sha256sum -c -
mkdir -p "$keyd_source"
tar -xzf "$archive" -C "$keyd_source" --strip-components=1
patch --batch -d "$keyd_source" -p1 < keys/keyd-guard.patch
make -C "$keyd_source" COMMIT=voice-guard PREFIX=/usr
make -C "$keyd_source" test-io COMMIT=voice-guard
# Link the real command handler with captured output instead of input devices.
set --
for file in "$keyd_source"/src/*.c; do
    case "$file" in */daemon.c|*/keyd.c) continue ;; esac
    set -- "$@" "$file"
done
cc -D_DEFAULT_SOURCE -DDATA_DIR='"/usr/share/keyd"' -DCONFIG_DIR='"/etc/keyd"' \
    -DSOCKET_PATH='"/var/run/keyd.socket"' -DVERSION='"test"' \
    -iquote "$keyd_source/src" -Wl,--wrap=vkbd_send_key -o "$keyd_source/bin/test-guard" \
    keys/test-keyd-guard.c "$@" "$keyd_source/src/vkbd/uinput.c" -lpthread
"$keyd_source/bin/test-guard"
# Run the language key bindings through keyd's real key handling.
set --
for file in "$keyd_source"/src/*.c; do
    case "$file" in */daemon.c|*/keyd.c|*/monitor.c|*/ipc.c|*/evloop.c|*/device.c) continue ;; esac
    set -- "$@" "$file"
done
cc -D_DEFAULT_SOURCE -DDATA_DIR='"/usr/share/keyd"' -DCONFIG_DIR='"/etc/keyd"' \
    -iquote "$keyd_source/src" -o "$keyd_source/bin/test-select" keys/test-keyd-select.c "$@"
"$keyd_source/bin/test-select" keys/test-keyd-select.conf 2>/dev/null
