#!/bin/sh
# Native GTK frontend uses distro PyGObject; ASR stays in the isolated worker.
set -eu
cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
exec /usr/bin/python3 -m dogspeak_app.gui "$@"
