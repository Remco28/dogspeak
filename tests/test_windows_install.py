"""windows_install.py: the Start menu entry and its clean removal, with a fake PowerShell."""
import importlib.util
import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_install", ROOT / "windows_install.py")
windows_install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(windows_install)


class FakePowerShell:
    """Creates and reads .lnk files as small JSON stand-ins."""

    def __call__(self, command, env, **_kwargs):
        script = command[-1]
        link = Path(env["DS_LINK"])
        if "Save()" in script:
            link.parent.mkdir(parents=True, exist_ok=True)
            link.write_text(json.dumps({"target": env["DS_TARGET"], "icon": env["DS_ICON"]}))
            out = ""
        else:
            out = json.loads(link.read_text())["target"]
        return subprocess.CompletedProcess(command, 0, out, "")


class WindowsInstallTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.checkout = self.base / "windev" / "dogspeak"
        (self.checkout / ".venv" / "Scripts").mkdir(parents=True)
        (self.checkout / ".venv" / "Scripts" / "pythonw.exe").touch()
        self.environ = {"APPDATA": str(self.base / "Roaming"), "LOCALAPPDATA": str(self.base / "Local")}
        self.run = FakePowerShell()
        self.link = self.base / "Roaming/Microsoft/Windows/Start Menu/Programs/Dogspeak.lnk"
        self.data = self.base / "Local" / "Dogspeak"

    def snapshot(self):
        return sorted(str(p.relative_to(self.base)) for p in self.base.rglob("*")
                      if "windev" not in p.parts)

    def test_install_then_uninstall_leaves_only_what_was_there(self):
        self.data.mkdir(parents=True)
        (self.data / "dogspeak.sqlite3").write_text("drafts")
        before = self.snapshot()
        windows_install.install(self.checkout, self.environ, self.run)
        self.assertTrue(self.link.exists())
        self.assertTrue((self.data / "dogspeak.ico").exists())
        ledger = json.loads((self.data / "install.json").read_text())
        self.assertEqual(Path(ledger["target"]), self.checkout / ".venv/Scripts/pythonw.exe")
        windows_install.uninstall(self.environ, self.run)
        after = [p for p in self.snapshot()]
        # Folders created for the shortcut may stay; every file is back as it was.
        self.assertEqual([p for p in after if Path(self.base / p).is_file()],
                         [p for p in before if Path(self.base / p).is_file()])
        self.assertEqual((self.data / "dogspeak.sqlite3").read_text(), "drafts")

    def test_install_refuses_to_replace_someone_elses_shortcut(self):
        self.link.parent.mkdir(parents=True)
        self.link.write_text(json.dumps({"target": "C:/Other/app.exe"}))
        with self.assertRaises(SystemExit):
            windows_install.install(self.checkout, self.environ, self.run)
        self.assertEqual(json.loads(self.link.read_text())["target"], "C:/Other/app.exe")
        self.assertFalse((self.data / "install.json").exists())

    def test_uninstall_leaves_a_shortcut_that_was_changed_since(self):
        windows_install.install(self.checkout, self.environ, self.run)
        self.link.write_text(json.dumps({"target": "C:/Other/app.exe"}))
        windows_install.uninstall(self.environ, self.run)
        self.assertTrue(self.link.exists())
        self.assertFalse((self.data / "install.json").exists())

    def test_uninstall_without_install_changes_nothing(self):
        before = self.snapshot()
        windows_install.uninstall(self.environ, self.run)
        self.assertEqual(self.snapshot(), before)

    def test_ico_holds_every_size_as_png(self):
        pngs = {16: b"\x89PNG-16", 256: b"\x89PNG-256"}
        ico = windows_install.build_ico(pngs)
        self.assertEqual(struct.unpack("<HHH", ico[:6]), (0, 1, 2))
        width, _h, _c, _r, _p, bits, size, offset = struct.unpack("<BBBBHHII", ico[6:22])
        self.assertEqual((width, bits, ico[offset:offset + size]), (16, 32, b"\x89PNG-16"))
        width, *_rest, size, offset = struct.unpack("<BBBBHHII", ico[22:38])
        self.assertEqual((width, ico[offset:offset + size]), (0, b"\x89PNG-256"))


if __name__ == "__main__":
    unittest.main()
