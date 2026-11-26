"""The Windows-side capture, credentials and shortcut parsing, with fake devices."""
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

import numpy as np
import soundfile

from dogspeak_app import audio_portaudio, credentials_keyring, hotkeys, paths


class FakeStream:
    def __init__(self, sd, device, samplerate, channels, dtype, callback, extra_settings=None):
        if sd.refuse_convert and extra_settings is not None:
            raise RuntimeError("Invalid sample rate")
        self.sd, self.device, self.samplerate, self.channels = sd, device, samplerate, channels
        self.callback, self.active, self.closed = callback, False, False
        self.thread = None
        sd.streams.append(self)

    def start(self):
        self.active = True

        def feed():
            for _ in range(self.sd.blocks):
                if not self.active:
                    return
                block = np.full((160, self.channels), self.sd.level, dtype=np.float32)
                self.callback(block, 160, None, None)
                time.sleep(0.001)
        self.thread = threading.Thread(target=feed, daemon=True)
        self.thread.start()

    def stop(self):
        self.active = False
        if self.thread:
            self.thread.join()

    def close(self):
        self.closed = True


class FakeSoundDevice:
    def __init__(self, level=0.25, blocks=20, refuse_convert=False, wasapi=True):
        self.level, self.blocks, self.refuse_convert = level, blocks, refuse_convert
        self.streams = []
        self.devices = [
            {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000},
            {"name": "Microphone (HyperX SoloCast 2)", "max_input_channels": 2,
             "default_samplerate": 48000},
            {"name": "Microphone Array (Intel)", "max_input_channels": 4,
             "default_samplerate": 44100},
            {"name": "Microphone (HyperX", "max_input_channels": 2, "default_samplerate": 48000},
        ]
        apis = [{"name": "MME", "devices": [3], "default_input_device": 3}]
        if wasapi:
            apis.append({"name": "Windows WASAPI", "devices": [0, 1, 2], "default_input_device": 2})
        self.apis = apis
        self.default = types.SimpleNamespace(hostapi=0)
        sd = self

        class InputStream(FakeStream):
            def __init__(self, **kwargs):
                super().__init__(sd, **kwargs)
        self.InputStream = InputStream
        self.WasapiSettings = lambda auto_convert: ("wasapi", auto_convert)

    def query_hostapis(self, index=None):
        return self.apis if index is None else self.apis[index]

    def query_devices(self, index):
        return self.devices[index]


class PortAudioRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "recordings" / "take.wav"

    def record(self, sd, target=None, seconds=0.1):
        recorder = audio_portaudio.Recorder(sd=sd, soundfile=soundfile)
        recorder.start(self.path, target=target)
        self.assertTrue(recorder.running)
        time.sleep(seconds)
        return recorder

    def test_lists_only_wasapi_inputs_with_full_names(self):
        self.assertEqual([s["name"] for s in audio_portaudio.list_sources(FakeSoundDevice())],
                         ["Microphone (HyperX SoloCast 2)", "Microphone Array (Intel)"])

    def test_falls_back_to_default_host_api_without_wasapi(self):
        self.assertEqual([s["name"] for s in audio_portaudio.list_sources(FakeSoundDevice(wasapi=False))],
                         ["Microphone (HyperX"])

    def test_records_16k_mono_wav_from_the_chosen_microphone(self):
        sd = FakeSoundDevice()
        path = self.record(sd, target="Microphone (HyperX SoloCast 2)").stop()
        self.assertEqual(path, self.path)
        self.assertEqual(sd.streams[0].device, 1)
        info = soundfile.info(str(path))
        self.assertEqual((info.samplerate, info.channels, info.subtype), (16000, 1, "FLOAT"))
        audio, _rate = soundfile.read(str(path), dtype="float32")
        self.assertEqual(len(audio), 160 * 20)
        self.assertAlmostEqual(float(audio.max()), 0.25, places=5)
        self.assertTrue(sd.streams[0].closed)

    def test_unknown_microphone_uses_the_default_one(self):
        sd = FakeSoundDevice()
        self.record(sd, target="Unplugged headset").stop()
        self.assertEqual(sd.streams[0].device, 2)

    def test_device_format_is_used_when_windows_cannot_convert(self):
        sd = FakeSoundDevice(refuse_convert=True)
        path = self.record(sd, target="Microphone Array (Intel)").stop()
        info = soundfile.info(str(path))
        self.assertEqual((info.samplerate, info.channels), (44100, 2))

    def test_all_zero_audio_explains_the_privacy_setting_and_cancel_deletes_it(self):
        recorder = self.record(FakeSoundDevice(level=0.0))
        with self.assertRaisesRegex(RuntimeError, "Privacy & security"):
            recorder.stop()
        self.assertTrue(self.path.exists())
        recorder.cancel()
        self.assertFalse(self.path.exists())

    def test_cancel_stops_the_stream_and_removes_the_file(self):
        sd = FakeSoundDevice(blocks=10_000)
        recorder = self.record(sd)
        recorder.cancel()
        self.assertFalse(self.path.exists())
        self.assertFalse(recorder.running)
        self.assertTrue(sd.streams[0].closed)
        with self.assertRaises(RuntimeError):
            recorder.stop()

    def test_a_second_start_is_refused(self):
        recorder = self.record(FakeSoundDevice(blocks=10_000))
        with self.assertRaises(RuntimeError):
            recorder.start(self.path.with_name("other.wav"))
        recorder.cancel()

    def test_a_vanished_device_stops_running(self):
        sd = FakeSoundDevice(blocks=10_000)
        recorder = self.record(sd)
        sd.streams[0].active = False
        self.assertFalse(recorder.running)
        recorder.cancel()


class FakeKeyring:
    class PasswordDeleteError(Exception):
        pass

    def __init__(self):
        self.store = {}

    def get_password(self, service, user):
        return self.store.get((service, user))

    def set_password(self, service, user, password):
        self.store[(service, user)] = password

    def delete_password(self, service, user):
        if (service, user) not in self.store:
            raise self.PasswordDeleteError(user)
        del self.store[(service, user)]


class KeyringTests(unittest.TestCase):
    def test_get_set_clear_by_address(self):
        backend = FakeKeyring()
        keys = credentials_keyring.Keyring(backend)
        self.assertEqual(keys.get("https://openrouter.ai/api/v1"), "")
        keys.set("https://openrouter.ai/api/v1", "sk-or-1")
        keys.set("https://api.anthropic.com", "sk-ant-2")
        self.assertEqual(keys.get("https://openrouter.ai/api/v1"), "sk-or-1")
        self.assertEqual(backend.store[("Dogspeak", "https://api.anthropic.com")], "sk-ant-2")
        keys.set("https://api.anthropic.com", "")
        keys.clear("https://api.anthropic.com")  # already gone: no error
        self.assertEqual(keys.get("https://api.anthropic.com"), "")

    def test_overlong_key_is_refused_with_a_sentence(self):
        with self.assertRaisesRegex(RuntimeError, "too long"):
            credentials_keyring.Keyring(FakeKeyring()).set("x", "k" * 5000)


class ShortcutTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(hotkeys.parse("Ctrl+Alt+R"),
                         (hotkeys.MOD_CONTROL | hotkeys.MOD_ALT, ord("R")))
        self.assertEqual(hotkeys.parse("ctrl+shift+space"),
                         (hotkeys.MOD_CONTROL | hotkeys.MOD_SHIFT, hotkeys.VK_SPACE))
        self.assertEqual(hotkeys.parse("Alt+F9"), (hotkeys.MOD_ALT, 0x78))
        self.assertEqual(hotkeys.parse("Ctrl+Win+5"),
                         (hotkeys.MOD_CONTROL | hotkeys.MOD_WIN, ord("5")))

    def test_refusals_say_why(self):
        for text, reason in [("", "Choose"), ("R", "needs Ctrl"), ("Shift+R", "needs Ctrl"),
                             ("Win+R", "Windows keeps"), ("Ctrl+Alt+Home", "can't be used"),
                             ("Hyper+R", "not a modifier")]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, reason):
                hotkeys.parse(text)

    def test_label(self):
        self.assertEqual(hotkeys.label("ctrl+alt+r"), "Ctrl+Alt+R")
        self.assertEqual(hotkeys.label("Ctrl+F5"), "Ctrl+F5")


class ForgetKeysTests(unittest.TestCase):
    def test_removes_only_dogspeak_entries(self):
        from dogspeak_app import forget_keys
        stored = ["Dogspeak", "https://api.anthropic.com@Dogspeak", "git:https://github.com",
                  "MicrosoftAccount:user=frank", "NotDogspeak", "Dogspeak@other"]
        deleted = []
        removed = forget_keys.forget(lambda: list(stored),
                                     lambda target: deleted.append(target) or True)
        self.assertEqual(removed, ["Dogspeak", "https://api.anthropic.com@Dogspeak"])
        self.assertEqual(deleted, removed)

    def test_a_refused_delete_is_not_reported_as_removed(self):
        from dogspeak_app import forget_keys
        self.assertEqual(forget_keys.forget(lambda: ["Dogspeak"], lambda target: False), [])


class PathTests(unittest.TestCase):
    def test_override_and_venv_layout(self):
        import os
        old = os.environ.get("DOGSPEAK_DATA_DIR")
        os.environ["DOGSPEAK_DATA_DIR"] = "/tmp/elsewhere"
        try:
            self.assertEqual(paths.data_dir(), Path("/tmp/elsewhere"))
            # A trial run's logs must not land in the real profile.
            self.assertEqual(paths.state_dir(), Path("/tmp/elsewhere/logs"))
        finally:
            if old is None:
                del os.environ["DOGSPEAK_DATA_DIR"]
            else:
                os.environ["DOGSPEAK_DATA_DIR"] = old
        python = paths.venv_python(Path("root"))
        if paths.WINDOWS:
            with tempfile.TemporaryDirectory() as installed:
                # The installer's layout: its own Python next to dogspeak_app.
                bundled = Path(installed) / "python"
                bundled.mkdir()
                (bundled / "python.exe").touch()
                (bundled / "pythonw.exe").touch()
                self.assertEqual(paths.venv_python(Path(installed)), bundled / "python.exe")
                self.assertEqual(paths.venv_python(Path(installed), windowed=True),
                                 bundled / "pythonw.exe")
            self.assertEqual(python, Path("root/.venv/Scripts/python.exe"))
            self.assertEqual(paths.venv_python(Path("root"), windowed=True).name, "pythonw.exe")
        else:
            self.assertEqual(python, Path("root/.venv/bin/python"))


if __name__ == "__main__":
    unittest.main()
