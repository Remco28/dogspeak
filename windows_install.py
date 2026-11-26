"""Add Dogspeak to the Windows Start menu, or take it away again.

    .venv\\Scripts\\python.exe windows_install.py              add the Start menu entry
    .venv\\Scripts\\python.exe windows_install.py --uninstall  remove what this added

Everything it creates (see "What the installer changes" in the README):
- the Start menu shortcut %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Dogspeak.lnk
- its icon, %LOCALAPPDATA%\\Dogspeak\\dogspeak.ico
- a ledger of the two, %LOCALAPPDATA%\\Dogspeak\\install.json
No registry, no startup entry, no PATH change. --uninstall removes the
shortcut only if it still points at this checkout, and never touches drafts.
"""
import argparse
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICON_SOURCE = ROOT / "data" / "icons" / "hicolor"
APP_ID = "io.github.Remco28.Dogspeak"


def start_menu(environ=os.environ):
    return Path(environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def app_data(environ=os.environ):
    return Path(environ["LOCALAPPDATA"]) / "Dogspeak"


def build_ico(pngs):
    """An .ico holding PNG images, which Windows Vista and later read. pngs: {size: bytes}."""
    sizes = sorted(pngs)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    offset = len(header) + 16 * len(sizes)
    entries, data = b"", b""
    for size in sizes:
        image = pngs[size]
        side = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(image), offset)
        data += image
        offset += len(image)
    return header + entries + data


def _powershell(script, env, run):
    result = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                  "-Command", script], env=dict(os.environ, **env), capture_output=True,
                 text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "PowerShell failed")
    return result.stdout.strip()


def shortcut_target(link, run=subprocess.run):
    return _powershell("(New-Object -ComObject WScript.Shell).CreateShortcut($env:DS_LINK).TargetPath",
                       {"DS_LINK": str(link)}, run)


def install(root=ROOT, environ=os.environ, run=subprocess.run):
    data = app_data(environ)
    data.mkdir(parents=True, exist_ok=True)
    link = start_menu(environ) / "Dogspeak.lnk"
    icon = data / "dogspeak.ico"
    target = Path(root) / ".venv" / "Scripts" / "pythonw.exe"
    if not target.exists():
        raise SystemExit("Run setup.cmd first: the Dogspeak environment is missing.")
    if link.exists() and Path(shortcut_target(link, run)).resolve() != target.resolve():
        raise SystemExit(f"{link} already exists and points somewhere else; nothing was changed.")
    ledger = data / "install.json"
    ledger.write_text(json.dumps({"shortcut": str(link), "icon": str(icon),
                                  "target": str(target)}, indent=2), encoding="utf-8")
    icon.write_bytes(build_ico({size: (Path(ICON_SOURCE) / f"{size}x{size}" / "apps"
                                       / f"{APP_ID}.png").read_bytes() for size in ICON_SIZES}))
    _powershell("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:DS_LINK); "
                "$s.TargetPath = $env:DS_TARGET; $s.Arguments = '-m dogspeak_app.qt_app'; "
                "$s.WorkingDirectory = $env:DS_DIR; $s.IconLocation = $env:DS_ICON; "
                "$s.Description = 'Talk freely, get a clear prompt'; $s.Save()",
                {"DS_LINK": str(link), "DS_TARGET": str(target), "DS_DIR": str(Path(root)),
                 "DS_ICON": str(icon)}, run)
    print(f"Added Dogspeak to the Start menu ({link}).")
    print("Remove it with: .venv\\Scripts\\python.exe windows_install.py --uninstall")


def uninstall(environ=os.environ, run=subprocess.run):
    data = app_data(environ)
    ledger = data / "install.json"
    if not ledger.exists():
        print("Nothing to remove: Dogspeak was not added to the Start menu from this account.")
        return
    record = json.loads(ledger.read_text(encoding="utf-8"))
    link, icon = Path(record["shortcut"]), Path(record["icon"])
    if link.exists():
        if Path(shortcut_target(link, run)).resolve() == Path(record["target"]).resolve():
            link.unlink()
            print(f"Removed {link}")
        else:
            print(f"Left {link}: it no longer points at Dogspeak.")
    icon.unlink(missing_ok=True)
    ledger.unlink()
    print("Done. Your drafts are still in", data, "- delete that folder to remove them too.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--uninstall", action="store_true", help="remove what this added")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("windows_install.py is for Windows; on Linux use desktop_install.py.")
    uninstall() if args.uninstall else install()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
