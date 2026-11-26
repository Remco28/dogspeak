#!/bin/sh
# One-time setup for a fresh clone: checks system packages, builds the
# inference venv, then downloads and verifies the pinned model files.
# Safe to run again; files already present with the right checksum are kept.
set -eu
cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

missing=""
/usr/bin/python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); gi.require_version('GtkSource', '5'); gi.require_version('Secret', '1'); from gi.repository import Gtk, Adw, GtkSource, Secret" 2>/dev/null \
  || missing="$missing python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtksource-5 gir1.2-secret-1"
if ! command -v pw-record >/dev/null || ! command -v pw-dump >/dev/null; then
  missing="$missing pipewire-bin"
fi
if [ -n "$missing" ]; then
  echo "Missing system packages. On Ubuntu or Debian:"
  echo "  sudo apt install$missing"
  exit 1
fi
if ! command -v uv >/dev/null; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

echo "Building the inference environment (.venv)…"
uv sync --frozen --no-dev

echo "Fetching model files…"
/usr/bin/python3 fetch_models.py

echo
echo "Setup complete. Start Dogspeak with ./app.sh"
echo "Optional: /usr/bin/python3 desktop_install.py --shortcut  (launcher, Super+R, overlay; then log out and in)"
