#!/bin/sh
# One-time setup for a fresh clone: checks system packages, builds the
# inference venv, then downloads and verifies the pinned model files.
# Safe to run again; files already present with the right checksum are kept.
set -eu
cd -- "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"

missing=""
/usr/bin/python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1'); gi.require_version('Secret', '1'); from gi.repository import Gtk, Adw, Secret" 2>/dev/null \
  || missing="$missing python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-secret-1"
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
uv sync --frozen

echo "Fetching model files…"
/usr/bin/python3 - <<'PY'
import hashlib, json, sys, urllib.request
from pathlib import Path

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

files = json.loads(Path("upstream.json").read_text())["files"]
wanted = {p: f for p, f in files.items()
          if (p.startswith("models/") or p == "samples/jfk.flac") and f.get("url")}
for name, info in wanted.items():
    path = Path(name)
    if path.exists() and digest(path) == info["sha256"]:
        print(f"  ok        {name}")
        continue
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    print(f"  download  {name} ({info['bytes'] / 1e6:.1f} MB)")
    with urllib.request.urlopen(info["url"], timeout=60) as response, partial.open("wb") as out:
        while chunk := response.read(1 << 20):
            out.write(chunk)
    if digest(partial) != info["sha256"]:
        partial.unlink()
        sys.exit(f"Checksum mismatch for {name}; nothing was kept. Try again.")
    partial.replace(path)
print("All model files verified against upstream.json.")
PY

echo
echo "Setup complete. Start Dogspeak with ./app.sh"
echo "Optional: /usr/bin/python3 desktop_install.py --shortcut  (launcher, Super+R, overlay; then log out and in)"
