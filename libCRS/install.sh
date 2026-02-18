#!/bin/bash

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env

# Install rsync
apt-get update && apt-get install -y rsync

uv tool install "$SCRIPT_DIR"
ln -sf "$HOME/.local/bin/libCRS" /usr/local/bin/libCRS