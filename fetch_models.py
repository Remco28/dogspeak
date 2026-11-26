#!/usr/bin/env python3
"""Download the pinned model files and check each against upstream.json.

Used by setup.sh (Linux) and setup.cmd (Windows). Stdlib only, so any Python
3.12+ can run it. Files already present with the right checksum are kept, so
it is safe to run again.
"""
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def wanted(manifest):
    files = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    return {p: f for p, f in files.items()
            if (p.startswith("models/") or p == "samples/jfk.flac") and f.get("url")}


def main(root=ROOT, opener=urllib.request.urlopen):
    root = Path(root)
    for name, info in wanted(root / "upstream.json").items():
        path = root / name
        if path.exists() and digest(path) == info["sha256"]:
            print(f"  ok        {name}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        print(f"  download  {name} ({info['bytes'] / 1e6:.1f} MB)", flush=True)
        with opener(info["url"], timeout=60) as response, partial.open("wb") as out:
            while chunk := response.read(1 << 20):
                out.write(chunk)
        if digest(partial) != info["sha256"]:
            partial.unlink()
            sys.exit(f"Checksum mismatch for {name}; nothing was kept. Try again.")
        partial.replace(path)
    print("All model files verified against upstream.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
