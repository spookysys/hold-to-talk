#!/bin/sh
# Install the user part of voice: the program, the model, and the user service.
# The root part is install-system.sh.
set -eu
cd "$(dirname "$0")"

make -C keys
uv tool install --force --python 3.13 .

models="$HOME/.local/share/voice/models"
model=ggml-small-q8_0.bin
if [ ! -e "$models/$model" ]; then
	mkdir -p "$models"
	curl -fL --retry 2 -o "$models/$model.part" \
		"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$model"
	mv "$models/$model.part" "$models/$model"
fi

install -D -m 0644 voice.service "$HOME/.config/systemd/user/voice.service"
systemctl --user daemon-reload
systemctl --user enable --now voice.service
echo "voice installed. Root part: pkexec $(pwd)/install-system.sh $(id -un)"
