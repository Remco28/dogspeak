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

    def test_shortcut_preserves_existing_bindings_and_is_idempotent(self):
        import desktop_install
        class Settings:
            def __init__(self, **values): self.values = values
            def get_strv(self, key): return list(self.values.get(key, []))
            def set_strv(self, key, value): self.values[key] = list(value); return True
            def get_string(self, key): return self.values.get(key, '')
            def set_string(self, key, value): self.values[key] = value; return True
        settings = Settings(**{'custom-keybindings': ['/existing/']})
        bindings = {'/existing/': Settings(name='Spokenly', binding='<Control>space', command='spokenly')}
        def binding_factory(path): return bindings.setdefault(path, Settings())
        for _ in range(2):
            desktop_install.configure_shortcut(ROOT, settings, binding_factory)
        self.assertEqual(settings.get_strv('custom-keybindings'), ['/existing/', desktop_install.SHORTCUT_PATH])
        self.assertEqual(bindings['/existing/'].get_string('binding'), '<Control>space')
        self.assertEqual(bindings[desktop_install.SHORTCUT_PATH].get_string('binding'), '<Super><Shift>d')
        self.assertIn('--toggle-recording', bindings[desktop_install.SHORTCUT_PATH].get_string('command'))
        settings.values['custom-keybindings'] = ['/existing/']
        bindings['/existing/'].values['binding'] = '<Super><Shift>d'
        with self.assertRaisesRegex(RuntimeError, 'already'):
            desktop_install.configure_shortcut(ROOT, settings, binding_factory)
        self.assertEqual(settings.get_strv('custom-keybindings'), ['/existing/'])


if __name__ == "__main__":
    unittest.main()
