#!/bin/sh
# Install the user part of voice: the program, the model, and the user service.
# The root part is install-system.sh.
set -eu
cd "$(dirname "$0")"

make -C keys
./keys/build-keyd.sh
uv tool install --force --python 3.13 .

models="$HOME/.local/share/voice/models"
model=sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11
if [ ! -d "$models/$model" ]; then
	mkdir -p "$models"
	curl -fL --retry 2 \
		"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$model.tar.bz2" |
		tar xj -C "$models"
fi

install -D -m 0644 voice.service "$HOME/.config/systemd/user/voice.service"
systemctl --user daemon-reload
systemctl --user enable voice.service
systemctl --user restart voice.service   # a running service keeps old code otherwise
echo "voice installed. Root part: pkexec $(pwd)/install-system.sh $(id -un)"
