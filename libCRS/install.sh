#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
export DEBIAN_FRONTEND=noninteractive

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

if [ -f "$HOME/.local/bin/env" ]; then
  # shellcheck source=/dev/null
  source "$HOME/.local/bin/env"
fi
export PATH="$HOME/.local/bin:$PATH"

# Install runtime dependency required by libCRS copy helpers.
apt-get update
apt-get install -y --no-install-recommends rsync

uv tool install "$SCRIPT_DIR" --force

if [ ! -x "$HOME/.local/bin/libCRS" ]; then
  echo "libCRS binary missing after uv tool install: $HOME/.local/bin/libCRS" >&2
  exit 1
fi

ln -sf "$HOME/.local/bin/libCRS" /usr/local/bin/libCRS
command -v libCRS >/dev/null
