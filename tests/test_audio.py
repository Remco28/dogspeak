"""Capture tests stub PipeWire only; these tests never open a microphone."""
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest import mock


class RecorderTests(unittest.TestCase):
    def test_explicit_start_targets_stable_name_and_stop_finalizes_private_file(self):
        audio = importlib.import_module("dogspeak_app.audio")
        child = mock.Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(audio.subprocess, "Popen", return_value=child) as spawn:
            recorder = audio.Recorder()
            self.assertFalse(recorder.running)
            spawn.assert_not_called()
            path = Path(tmp) / "private" / "capture.wav"
            recorder.start(path, target="alsa_input.usb-HyperX_SoloCast")
            argv = spawn.call_args.args[0]
            self.assertEqual(argv[argv.index("--target") + 1],
                             "alsa_input.usb-HyperX_SoloCast")
            self.assertEqual(argv[argv.index("--rate") + 1], "16000")
            self.assertEqual(argv[argv.index("--channels") + 1], "1")
            self.assertEqual(argv[-1], str(path))
            self.assertTrue(recorder.running)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(recorder.stop(), path)
            child.send_signal.assert_called_once_with(signal.SIGINT)
            child.wait.assert_called_once()
            self.assertGreater(child.wait.call_args.kwargs["timeout"], 0)
            self.assertFalse(recorder.running)
            recorder.cancel()
            self.assertTrue(path.exists(), "cancel after stop must preserve completed audio")

    def test_stalled_capture_is_killed_and_reaped_with_bounded_wait(self):
        audio = importlib.import_module("dogspeak_app.audio")
        child = mock.Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("pw-record", 5), -9]
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(audio.subprocess, "Popen", return_value=child):
            recorder = audio.Recorder()
            recorder.start(Path(tmp) / "capture.wav")
            with self.assertRaisesRegex(RuntimeError, "(finaliz|timed out)"):
                recorder.stop()
            child.kill.assert_called_once()
            self.assertEqual(child.wait.call_count, 2)
            self.assertTrue(all(c.kwargs.get("timeout", 0) > 0
                                for c in child.wait.call_args_list))
            self.assertFalse(recorder.running)

    def test_cancel_deletes_partial_capture_even_when_capture_failed(self):
        audio = importlib.import_module("dogspeak_app.audio")
        child = mock.Mock()
        child.poll.return_value = 1
        child.wait.return_value = 1
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(audio.subprocess, "Popen", return_value=child):
            recorder = audio.Recorder()
            path = Path(tmp) / "capture.wav"
            recorder.start(path)
            recorder.cancel()
            self.assertFalse(path.exists())
            self.assertFalse(recorder.running)
            child.wait.assert_called_once()

    def test_missing_pipewire_is_actionable_and_leaves_no_file(self):
        audio = importlib.import_module("dogspeak_app.audio")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(audio.subprocess, "Popen", side_effect=FileNotFoundError()):
            recorder = audio.Recorder()
            path = Path(tmp) / "capture.wav"
            with self.assertRaisesRegex(RuntimeError, "pw-record.*(install|PipeWire)"):
                recorder.start(path)
            self.assertFalse(path.exists())
            self.assertFalse(recorder.running)

    def test_capture_failure_is_observable_on_stop(self):
        audio = importlib.import_module("dogspeak_app.audio")
        child = mock.Mock()
        child.poll.return_value = 7
        child.wait.return_value = 7
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(audio.subprocess, "Popen", return_value=child):
            recorder = audio.Recorder()
            recorder.start(Path(tmp) / "capture.wav")
            with self.assertRaisesRegex(RuntimeError, "pw-record.*7"):
                recorder.stop()
            child.send_signal.assert_not_called()
            self.assertFalse(recorder.running)

    def test_cancel_successful_capture_removes_partial_file(self):
        audio = importlib.import_module("dogspeak_app.audio")
        child = mock.Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(audio.subprocess, "Popen", return_value=child):
            recorder = audio.Recorder()
            path = Path(tmp) / "capture.wav"
            recorder.start(path)
            recorder.cancel()
            self.assertFalse(path.exists())
            self.assertFalse(recorder.running)

    def test_sigint_exit_one_is_only_accepted_for_finalized_nonempty_wav(self):
        import struct
        audio = importlib.import_module("dogspeak_app.audio")
        fmt = struct.pack("<HHIIHH", 3, 1, 16000, 64000, 4, 32)
        chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", 8) + b"\0" * 8
        valid = b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks
        for was_running, contents, accepted in [
                (True, valid, True), (False, valid, False),
                (True, b"", False), (True, valid[:-1], False),
                (True, b"not audio but nonempty", False)]:
            with self.subTest(was_running=was_running, size=len(contents)), \
                    tempfile.TemporaryDirectory() as tmp:
                child = mock.Mock()
                child.poll.return_value = None if was_running else 1
                child.wait.return_value = 1
                with mock.patch.object(audio.subprocess, "Popen", return_value=child):
                    recorder = audio.Recorder()
                    path = Path(tmp) / "capture.wav"
                    recorder.start(path)
                    path.write_bytes(contents)
                    if accepted:
                        self.assertEqual(recorder.stop(), path)
                    else:
                        with self.assertRaisesRegex(RuntimeError, "pw-record"):
                            recorder.stop()

    def test_actual_child_is_finalized_and_reaped_without_any_audio_device(self):
        import sys
        import time
        audio = importlib.import_module("dogspeak_app.audio")
        real_popen = subprocess.Popen
        script = '''
import pathlib, signal, sys, time, wave
path = pathlib.Path(sys.argv[1])
def finish(*_):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\\0\\0" * 160)
    raise SystemExit(1)
signal.signal(signal.SIGINT, finish)
path.with_suffix(".ready").touch()
while True:
    time.sleep(1)
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture.wav"
            children = []
            def fake_device(command, **options):
                child = real_popen([sys.executable, "-u", "-c", script, command[-1]], **options)
                children.append(child)
                return child
            with mock.patch.object(audio.subprocess, "Popen", side_effect=fake_device):
                recorder = audio.Recorder()
                recorder.start(path, target="explicitly.fake.device")
                try:
                    deadline = time.monotonic() + 3
                    while not path.with_suffix(".ready").exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(path.with_suffix(".ready").exists())
                    self.assertEqual(recorder.stop(), path)
                    self.assertEqual(children[0].returncode, 1)
                    self.assertTrue(audio._finalized_wav(path))
                    with self.assertRaises(ProcessLookupError):
                        os.kill(children[0].pid, 0)
                finally:
                    recorder.cancel()


class SourcesTests(unittest.TestCase):
    def test_only_physical_sources_use_stable_names_not_transient_ids(self):
        audio = importlib.import_module("dogspeak_app.audio")
        nodes = [
            {"id": 45, "info": {"props": {"media.class": "Audio/Source",
             "node.name": "alsa_input.usb-HyperX", "node.description": "HyperX SoloCast"}}},
            {"id": 46, "info": {"props": {"media.class": "Audio/Sink",
             "node.name": "speaker"}}},
            {"id": 47, "info": {"props": {"media.class": "Audio/Source",
             "node.name": "speaker.monitor"}}},
            {"id": 48, "info": {"props": {"media.class": "Audio/Source",
             "node.name": "another", "stream.monitor": True}}},
            {"info": {"props": {"media.class": "Audio/Source", "node.name": "fallback"}}},
            {},
        ]
        with mock.patch.object(audio.subprocess, "run", return_value=
                               subprocess.CompletedProcess("pw-dump", 0, json.dumps(nodes), "")) as run:
            self.assertEqual(audio.list_sources(), [
                {"name": "alsa_input.usb-HyperX", "description": "HyperX SoloCast"},
                {"name": "fallback", "description": "fallback"},
            ])
            self.assertEqual(run.call_args.args[0], ["pw-dump"])
            self.assertGreater(run.call_args.kwargs["timeout"], 0)


if __name__ == "__main__":
    unittest.main()
