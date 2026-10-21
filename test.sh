#!/bin/sh
# ASR dependencies stay in the venv; native GTK tests use distro PyGObject.
set -eu
cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
.venv/bin/python -m unittest discover -s tests -p 'test_[!g]*.py' -v
xvfb-run -a /usr/bin/python3 -W ignore::DeprecationWarning:gi.events -W ignore::DeprecationWarning:gi.overrides -m unittest discover -s tests -p 'test_gui.py' -v
sh -n app.sh
sh -n run.sh
