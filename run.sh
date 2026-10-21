#!/bin/sh
# Runtime only: use the preinstalled environment; never invoke uv or download.
set -eu
cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
exec "$PWD/.venv/bin/python" "$PWD/dogspeak.py" "$@"
