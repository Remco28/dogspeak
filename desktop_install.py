#!/usr/bin/env python3
"""Install a user-scoped launcher; global GNOME shortcut is explicit opt-in."""
import argparse
import os
from pathlib import Path
import shlex
import sys

APP_ID = "io.github.Remco28.Dogspeak"
SHORTCUT_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dogspeak/"
ROOT = Path(__file__).resolve().parent


def desktop_entry(root):
    path = str(Path(root).resolve() / "app.sh")
    if any(ord(char) < 32 for char in path):
        raise ValueError("Launcher path must not contain control characters")
    quoted = path.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
    return ("[Desktop Entry]\nType=Application\nName=Dogspeak\n"
            "Comment=Local dictation scratchpad and prompt design lab\n"
            f'Exec="{quoted}"\nIcon=audio-input-microphone\n'
            "Terminal=false\nCategories=Utility;TextEditor;\n"
            f"StartupWMClass={APP_ID}\nActions=ToggleRecording;\n\n"
            "[Desktop Action ToggleRecording]\nName=Toggle Recording\n"
            f'Exec="{quoted}" --toggle-recording\n')


def install_entry(root=ROOT, data_home=None):
    root = Path(root).resolve()
    if not os.access(root / "app.sh", os.X_OK):
        raise ValueError(f"Missing executable launcher: {root / 'app.sh'}")
    data_home = Path(data_home or os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    target = data_home / "applications" / f"{APP_ID}.desktop"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".desktop.tmp")
    temporary.write_text(desktop_entry(root), encoding="utf-8")
    temporary.chmod(0o644)
    temporary.replace(target)
    return target


def configure_shortcut(root, settings, binding_factory, binding="<Super><Shift>d"):
    """Preserve other custom bindings; never replace Spokenly's shortcut."""
    paths = settings.get_strv("custom-keybindings")
    for path in paths:
        existing = binding_factory(path)
        if path != SHORTCUT_PATH and existing.get_string("binding").lower() == binding.lower():
            raise RuntimeError(f"Shortcut {binding} is already assigned to {existing.get_string('name')}")
    own = binding_factory(SHORTCUT_PATH)
    if own.get_string("name") not in ("", "Dogspeak"):
        raise RuntimeError("Dogspeak shortcut settings path is already used by another application")
    command = shlex.quote(str(Path(root).resolve() / "app.sh")) + " --toggle-recording"
    for key, value in (("name", "Dogspeak"), ("command", command), ("binding", binding)):
        if not own.set_string(key, value) or own.get_string(key) != value:
            raise RuntimeError(f"Desktop refused shortcut setting: {key}")
    updated = paths if SHORTCUT_PATH in paths else paths + [SHORTCUT_PATH]
    if not settings.set_strv("custom-keybindings", updated) or settings.get_strv("custom-keybindings") != updated:
        raise RuntimeError("Desktop refused the shortcut registration")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shortcut", action="store_true",
                        help="Also register Super+Shift+D in GNOME (does not replace other bindings)")
    args = parser.parse_args(argv)
    try:
        target = install_entry()
        print(f"Installed launcher: {target}")
        if args.shortcut:
            from gi.repository import Gio
            settings = Gio.Settings.new("org.gnome.settings-daemon.plugins.media-keys")
            factory = lambda path: Gio.Settings.new_with_path(
                "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding", path)
            configure_shortcut(ROOT, settings, factory)
            Gio.Settings.sync()
            print("Global record toggle: Super+Shift+D (while Dogspeak is ready)")
        else:
            print("Global shortcuts unchanged. Use --shortcut to opt in to Super+Shift+D.")
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f"dogspeak: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
