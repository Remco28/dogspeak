#!/usr/bin/env python3
"""Install a user-scoped launcher; global GNOME shortcut is explicit opt-in."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

APP_ID = "io.github.Remco28.Dogspeak"
SHORTCUT_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/dogspeak/"
ROOT = Path(__file__).resolve().parent
OVERLAY_UUID = "dogspeak-overlay@remco28.github.io"


def desktop_entry(root):
    path = str(Path(root).resolve() / "app.sh")
    if any(ord(char) < 32 for char in path):
        raise ValueError("Launcher path must not contain control characters")
    quoted = path.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
    return ("[Desktop Entry]\nType=Application\nName=Dogspeak\n"
            "Comment=Local dictation scratchpad and prompt design lab\n"
            f'Exec="{quoted}"\nIcon={APP_ID}\n'
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


def install_icons(root=ROOT, data_home=None, run=subprocess.run):
    """Copy the app icon at every size into the user's hicolor theme.

    If that folder already has an icon-theme.cache (another app made one), GTK
    and GNOME Shell trust it and ignore icons it doesn't list, so rebuild it.
    """
    source = Path(root).resolve() / "data" / "icons" / "hicolor"
    data_home = Path(data_home or os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    installed = []
    for icon in sorted(source.glob(f"*/apps/{APP_ID}.png")):
        target = data_home / "icons" / "hicolor" / icon.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(icon, target)
        installed.append(target)
    if not installed:
        raise ValueError(f"Missing app icons under {source}")
    theme = data_home / "icons" / "hicolor"
    if (theme / "icon-theme.cache").exists():
        try:
            result = run(["gtk-update-icon-cache", "--force", "--ignore-theme-index", "--quiet", str(theme)],
                         capture_output=True, text=True)
        except OSError as error:
            raise RuntimeError(f"Could not refresh the icon cache in {theme}: {error}") from error
        if result.returncode != 0:
            raise RuntimeError(f"Could not refresh the icon cache in {theme}: {result.stderr.strip()}")
    return installed


def install_overlay(root=ROOT, data_home=None):
    """Copy the Shell overlay extension into the user's extensions folder."""
    source = Path(root).resolve() / "gnome-extension" / OVERLAY_UUID
    if not (source / "metadata.json").is_file():
        raise ValueError(f"Missing overlay extension: {source}")
    data_home = Path(data_home or os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    target = data_home / "gnome-shell" / "extensions" / OVERLAY_UUID
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(OVERLAY_UUID + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.copytree(source, staging)
    shutil.rmtree(target, ignore_errors=True)
    staging.replace(target)
    return target


def enable_overlay(settings):
    """Enable at the next Shell start; Wayland Shell only scans for new extensions at login."""
    enabled = settings.get_strv("enabled-extensions")
    disabled = settings.get_strv("disabled-extensions")
    if OVERLAY_UUID in disabled:
        settings.set_strv("disabled-extensions", [uuid for uuid in disabled if uuid != OVERLAY_UUID])
    if OVERLAY_UUID not in enabled:
        if not settings.set_strv("enabled-extensions", enabled + [OVERLAY_UUID]):
            raise RuntimeError("Desktop refused to enable the overlay extension")
    if settings.get_boolean("disable-user-extensions"):
        return "User extensions are switched off in GNOME; turn them on in the Extensions app to see the overlay."
    return None


def _accelerator_key(binding):
    """Compare accelerators as GNOME does: modifier order and aliases don't matter."""
    aliases = {"ctrl": "control", "primary": "control"}
    modifiers, rest = set(), binding.strip().lower()
    while rest.startswith("<") and ">" in rest:
        name, rest = rest[1:].split(">", 1)
        modifiers.add(aliases.get(name, name))
    return frozenset(modifiers), rest


def configure_shortcut(root, settings, binding_factory, binding="<Super>r"):
    """Preserve other custom bindings; never replace another app's shortcut."""
    paths = settings.get_strv("custom-keybindings")
    for path in paths:
        existing = binding_factory(path)
        if path != SHORTCUT_PATH and _accelerator_key(existing.get_string("binding")) == _accelerator_key(binding):
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
                        help="Also register Super+R in GNOME (does not replace other bindings) "
                             "and install the always-on-top recording overlay")
    args = parser.parse_args(argv)
    try:
        icons = install_icons()
        target = install_entry()
        print(f"Installed launcher: {target} ({len(icons)} icon sizes)")
        if args.shortcut:
            from gi.repository import Gio
            settings = Gio.Settings.new("org.gnome.settings-daemon.plugins.media-keys")
            factory = lambda path: Gio.Settings.new_with_path(
                "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding", path)
            configure_shortcut(ROOT, settings, factory)
            print("Global record toggle: Super+R (while Dogspeak is ready)")
            print(f"Installed overlay: {install_overlay()}")
            warning = enable_overlay(Gio.Settings.new("org.gnome.shell"))
            Gio.Settings.sync()
            if warning:
                print(warning)
            print("Log out and back in once so GNOME Shell loads the overlay. "
                  "Until then status appears as notifications.")
        else:
            print("Global shortcuts unchanged. Use --shortcut to opt in to Super+R.")
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f"dogspeak: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
