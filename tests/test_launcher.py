"""Launcher/desktop integration tests; never open a microphone."""
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def test_test_runner_exists_and_is_valid_executable_shell(self):
        runner = ROOT / "test.sh"
        self.assertTrue(runner.is_file(), "test runner is missing")
        self.assertTrue(os.access(runner, os.X_OK))
        result = subprocess.run(["sh", "-n", str(runner)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_native_launcher_exists_and_is_valid_executable_shell(self):
        launcher = ROOT / "app.sh"
        self.assertTrue(launcher.is_file(), "native app launcher is missing")
        self.assertTrue(os.access(launcher, os.X_OK))
        result = subprocess.run(["sh", "-n", str(launcher)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        source = launcher.read_text()
        self.assertIn("/usr/bin/python3", source)
        self.assertIn("-m dogspeak_app.gui", source)
        self.assertIn("HF_HUB_OFFLINE=1", source)
        self.assertIn("TRANSFORMERS_OFFLINE=1", source)
        self.assertNotIn("uv run", source)


class DesktopTests(unittest.TestCase):
    def test_desktop_entry_quotes_paths_and_has_record_action(self):
        import desktop_install
        entry = desktop_install.desktop_entry(Path('/home/example/a project'))
        self.assertIn('Exec="/home/example/a project/app.sh"', entry)
        self.assertIn('[Desktop Action ToggleRecording]', entry)
        self.assertIn('--toggle-recording', entry)
        self.assertIn('Terminal=false', entry)
        with self.assertRaises(ValueError):
            desktop_install.desktop_entry(Path('/home/example/bad\npath'))

    def test_install_is_user_scoped_and_does_not_enable_shortcut(self):
        import desktop_install
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            root.mkdir()
            launcher = root / 'app.sh'
            launcher.write_text('#!/bin/sh\n')
            launcher.chmod(0o755)
            destination = desktop_install.install_entry(root, Path(tmp) / 'data')
            self.assertEqual(destination, Path(tmp) / 'data/applications/io.github.Remco28.Dogspeak.desktop')
            self.assertIn(str(launcher), destination.read_text())
            self.assertIn('Icon=io.github.Remco28.Dogspeak\n', destination.read_text())

    def test_icons_install_at_every_size_in_the_user_theme(self):
        import desktop_install
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            icons = desktop_install.install_icons(ROOT, tmp)
            sizes = sorted(int(p.parts[-3].split('x')[0]) for p in icons)
            self.assertEqual(sizes, [16, 24, 32, 48, 64, 128, 256, 512])
            for icon in icons:
                self.assertEqual(icon.name, 'io.github.Remco28.Dogspeak.png')
                self.assertTrue(str(icon).startswith(str(Path(tmp) / 'icons/hicolor')))
                self.assertEqual(icon.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')

    def test_icon_install_refreshes_an_existing_icon_cache_only(self):
        import desktop_install
        import tempfile
        calls = []

        class Done:
            returncode, stderr = 0, ''
        run = lambda command, **_: calls.append(command) or Done()
        with tempfile.TemporaryDirectory() as tmp:
            desktop_install.install_icons(ROOT, tmp, run=run)
            self.assertEqual(calls, [], 'no cache: leave the folder without one')
            theme = Path(tmp) / 'icons/hicolor'
            (theme / 'icon-theme.cache').write_bytes(b'stale')
            desktop_install.install_icons(ROOT, tmp, run=run)
            self.assertEqual(calls[-1][0], 'gtk-update-icon-cache')
            self.assertEqual(calls[-1][-1], str(theme))
        with tempfile.TemporaryDirectory() as tmp:
            theme = Path(tmp) / 'icons/hicolor'
            theme.mkdir(parents=True)
            (theme / 'icon-theme.cache').write_bytes(b'stale')
            desktop_install.install_icons(ROOT, tmp)
            self.assertGreater((theme / 'icon-theme.cache').stat().st_size, 5, 'real cache rebuilt')

    def test_overlay_install_is_user_scoped_and_replaces_old_copies(self):
        import tempfile
        import desktop_install
        with tempfile.TemporaryDirectory() as tmp:
            target = desktop_install.install_overlay(ROOT, tmp)
            self.assertEqual(target, Path(tmp) / "gnome-shell/extensions" / desktop_install.OVERLAY_UUID)
            (target / "stale.js").write_text("old")
            target = desktop_install.install_overlay(ROOT, tmp)
            self.assertFalse((target / "stale.js").exists())
            for name in ("metadata.json", "extension.js", "stylesheet.css"):
                self.assertTrue((target / name).is_file(), name)
            import json
            self.assertEqual(json.loads((target / "metadata.json").read_text())["uuid"],
                             desktop_install.OVERLAY_UUID)

    def test_overlay_enable_keeps_other_extensions(self):
        import desktop_install

        class Shell:
            def __init__(self):
                self.values = {"enabled-extensions": ["dock@x"],
                               "disabled-extensions": [desktop_install.OVERLAY_UUID, "y@z"]}

            def get_strv(self, key):
                return list(self.values[key])

            def set_strv(self, key, value):
                self.values[key] = list(value)
                return True

            def get_boolean(self, key):
                return False

        shell = Shell()
        self.assertIsNone(desktop_install.enable_overlay(shell))
        self.assertIsNone(desktop_install.enable_overlay(shell))
        self.assertEqual(shell.values["enabled-extensions"], ["dock@x", desktop_install.OVERLAY_UUID])
        self.assertEqual(shell.values["disabled-extensions"], ["y@z"])

    def test_shortcut_preserves_existing_bindings_and_is_idempotent(self):
        import desktop_install
        class Settings:
            def __init__(self, **values): self.values = values
            def get_strv(self, key): return list(self.values.get(key, []))
            def set_strv(self, key, value): self.values[key] = list(value); return True
            def get_string(self, key): return self.values.get(key, '')
            def set_string(self, key, value): self.values[key] = value; return True
        settings = Settings(**{'custom-keybindings': ['/existing/']})
        bindings = {'/existing/': Settings(name='Other dictation app', binding='<Control>space', command='other-app')}
        def binding_factory(path): return bindings.setdefault(path, Settings())
        for _ in range(2):
            desktop_install.configure_shortcut(ROOT, settings, binding_factory)
        self.assertEqual(settings.get_strv('custom-keybindings'), ['/existing/', desktop_install.SHORTCUT_PATH])
        self.assertEqual(bindings['/existing/'].get_string('binding'), '<Control>space')
        self.assertEqual(bindings[desktop_install.SHORTCUT_PATH].get_string('binding'), '<Super>r')
        self.assertIn('--toggle-recording', bindings[desktop_install.SHORTCUT_PATH].get_string('command'))
        settings.values['custom-keybindings'] = ['/existing/']
        bindings['/existing/'].values['binding'] = '<Super>R'
        with self.assertRaisesRegex(RuntimeError, 'already'):
            desktop_install.configure_shortcut(ROOT, settings, binding_factory)
        self.assertEqual(settings.get_strv('custom-keybindings'), ['/existing/'])
        # GNOME Settings stores gtk_accelerator_name() order, e.g. <Shift><Control>r.
        for spelling in ('<Shift><Control>r', '<Primary><Shift>R', '<Ctrl><Shift>r'):
            bindings['/existing/'].values['binding'] = spelling
            with self.subTest(spelling=spelling), self.assertRaisesRegex(RuntimeError, 'already'):
                desktop_install.configure_shortcut(ROOT, settings, binding_factory, binding='<Control><Shift>r')
            self.assertEqual(settings.get_strv('custom-keybindings'), ['/existing/'])
        bindings['/existing/'].values['binding'] = '<Super><Shift>r'
        desktop_install.configure_shortcut(ROOT, settings, binding_factory)

    def test_reinstall_moves_an_old_dogspeak_binding_to_super_r(self):
        import desktop_install
        class Settings:
            def __init__(self, **values): self.values = values
            def get_strv(self, key): return list(self.values.get(key, []))
            def set_strv(self, key, value): self.values[key] = list(value); return True
            def get_string(self, key): return self.values.get(key, '')
            def set_string(self, key, value): self.values[key] = value; return True
        settings = Settings(**{'custom-keybindings': [desktop_install.SHORTCUT_PATH]})
        own = Settings(name='Dogspeak', binding='<Control><Shift>r', command='old')
        desktop_install.configure_shortcut(ROOT, settings, lambda path: own)
        self.assertEqual(own.get_string('binding'), '<Super>r')
        self.assertEqual(settings.get_strv('custom-keybindings'), [desktop_install.SHORTCUT_PATH])


if __name__ == "__main__":
    unittest.main()
