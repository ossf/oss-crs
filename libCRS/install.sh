#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
export DEBIAN_FRONTEND=noninteractive

# Parse install mode (default: --all)
INSTALL_CLI=false
INSTALL_PYTHON=false
if [ $# -eq 0 ]; then
  INSTALL_CLI=true
  INSTALL_PYTHON=true
fi
for arg in "$@"; do
  case "$arg" in
    --all)    INSTALL_CLI=true; INSTALL_PYTHON=true ;;
    --cli)    INSTALL_CLI=true ;;
    --python) INSTALL_PYTHON=true ;;
    *)        echo "Unknown option: $arg (expected --all, --cli, --python)" >&2; exit 1 ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

if [ -f "$HOME/.local/bin/env" ]; then
  # shellcheck source=/dev/null
  source "$HOME/.local/bin/env"
fi
export PATH="$HOME/.local/bin:$PATH"

# libCRS no longer needs rsync (it copies in pure Python). It is still
# installed because CRS scripts may rely on it being present.
apt-get update
apt-get install -y --no-install-recommends rsync

if [ "$INSTALL_CLI" = true ]; then
  # Install as CLI tool (isolated venv for the entry point)
  uv tool install "$SCRIPT_DIR" --force

  if [ ! -x "$HOME/.local/bin/libCRS" ]; then
    echo "libCRS binary missing after uv tool install: $HOME/.local/bin/libCRS" >&2
    exit 1
  fi

  ln -sf "$HOME/.local/bin/libCRS" /usr/local/bin/libCRS
  command -v libCRS >/dev/null
fi

if [ "$INSTALL_PYTHON" = true ]; then
  # Install as importable package for Python API consumers
  uv pip install --system "$SCRIPT_DIR"
fi
