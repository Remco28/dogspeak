"""Engine integration tests use real subprocesses and explicitly fake inference."""
import importlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE_WORKER = f'''
import os, runpy, sys, types
sys.path.insert(0, {str(ROOT)!r})
fake = types.ModuleType("dogspeak")
class Pipeline:
    def __init__(self):
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        print("explicit fake pipeline", flush=True)
    def transcribe(self, audio):
        return "  fake: " + audio + "\\n"
fake.Pipeline = Pipeline
fake.read_audio = lambda path: str(path)
sys.modules["dogspeak"] = fake
runpy.run_path({str(ROOT / 'dogspeak_app' / 'worker.py')!r}, run_name="__main__")
'''


class EngineTests(unittest.TestCase):
    def make_root(self, script=FAKE_WORKER):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / ".venv/bin").mkdir(parents=True)
        (root / ".venv/bin/python").symlink_to(sys.executable)
        (root / "dogspeak_app").mkdir()
        (root / "dogspeak_app/__init__.py").touch()
        (root / "dogspeak_app/worker.py").write_text(script)
        return root

    def make_client(self, root):
        engine = importlib.import_module("dogspeak_app.engine")
        self.events = queue.Queue()
        self.thread_ids = []
        def callback(event):
            self.thread_ids.append(threading.get_ident())
            self.events.put(event)
        client = engine.EngineClient(callback, root=root, log_path=root / "private/engine.log")
        self.addCleanup(client.close)
        return client

    def event(self):
        return self.events.get(timeout=5)

    def test_async_real_subprocess_roundtrip_preserves_text_and_closes(self):
        root = self.make_root()
        client = self.make_client(root)
        client.start()
        self.assertEqual(self.event()["type"], "loading")
        ready = self.event()
        self.assertEqual(ready["type"], "ready")
        child_pid = ready["pid"]
        client.transcribe(Path("clip.wav"), "job")
        result = self.event()
        self.assertEqual(result["type"], "result")
        self.assertEqual(result["id"], "job")
        self.assertEqual(result["path"], "clip.wav")
        self.assertEqual(result["text"], "  fake: clip.wav\n")
        self.assertTrue(all(tid != threading.get_ident() for tid in self.thread_ids))
        client.close()
        client.close()
        with self.assertRaises(ProcessLookupError):
            os.kill(child_pid, 0)
        self.assertTrue(self.events.empty(), "normal close must not emit fatal child death")
        self.assertEqual((root / "private/engine.log").stat().st_mode & 0o777, 0o600)
        self.assertIn("explicit fake pipeline", (root / "private/engine.log").read_text())

    def test_unexpected_worker_death_emits_fatal_error_on_reader_thread(self):
        root = self.make_root('import os; print(\'{"type":"loading"}\', flush=True); os._exit(23)')
        client = self.make_client(root)
        client.start()
        self.assertEqual(self.event()["type"], "loading")
        error = self.event()
        self.assertEqual(error["type"], "error")
        self.assertTrue(error["fatal"])
        self.assertIsNone(error["id"])
        self.assertIn("23", error["message"])
        self.assertIn("log", error["message"])
        self.assertTrue(all(tid != threading.get_ident() for tid in self.thread_ids))

    def test_missing_venv_emits_actionable_fatal_event_instead_of_raising(self):
        root = self.make_root()
        (root / ".venv/bin/python").unlink()
        client = self.make_client(root)
        client.start()
        error = self.event()
        self.assertEqual(error["type"], "error")
        self.assertTrue(error["fatal"])
        self.assertIn(".venv/bin/python", error["message"])
        self.assertTrue(all(tid != threading.get_ident() for tid in self.thread_ids))

    def test_unresponsive_worker_cannot_block_request_submission(self):
        script = 'import os,time; print(\'{"type":"ready","pid":%d}\' % os.getpid(),flush=True); time.sleep(60)'
        client = self.make_client(self.make_root(script))
        client.start()
        ready = self.event()
        outcome = []
        def fill_pipe():
            try:
                for index in range(1000):
                    client.transcribe("x" * 1000 + ".wav", str(index))
            except Exception as error:
                outcome.append(error)
        sender = threading.Thread(target=fill_pipe, daemon=True)
        sender.start()
        sender.join(timeout=0.5)
        blocked = sender.is_alive()
        # Always release a blocked writer before asserting, even in RED.
        if blocked:
            os.kill(ready["pid"], 9)
            sender.join(timeout=2)
        self.assertFalse(blocked, "transcribe blocked when the worker pipe filled")
        self.assertTrue(outcome)
        self.assertIsInstance(outcome[0], RuntimeError)
        self.assertIn("busy", str(outcome[0]).lower())

    def test_close_reaps_worker_that_ignores_shutdown_and_sigterm(self):
        script = ('import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                  'print(\'{"type":"ready","pid":%d}\' % os.getpid(),flush=True); time.sleep(60)')
        client = self.make_client(self.make_root(script))
        client.start()
        ready = self.event()
        started = time.monotonic()
        client.close()
        self.assertLess(time.monotonic() - started, 5)
        with self.assertRaises(ProcessLookupError):
            os.kill(ready["pid"], 0)
        self.assertTrue(self.events.empty())

    def test_close_during_start_does_not_leave_reader_or_worker(self):
        client = self.make_client(self.make_root())
        client.start()
        client.close()
        self.assertFalse(client._reader.is_alive())
        if client._process is not None:
            self.assertIsNotNone(client._process.poll())
        with self.assertRaisesRegex(RuntimeError, "closing"):
            client.transcribe("clip.wav", "job")

    def test_invalid_worker_output_emits_fatal_error_and_reaps_child(self):
        script = 'import time; print("not JSON",flush=True); time.sleep(60)'
        client = self.make_client(self.make_root(script))
        client.start()
        error = self.event()
        self.assertEqual(error["type"], "error")
        self.assertTrue(error["fatal"])
        self.assertIn("protocol", error["message"])
        self.assertIsNotNone(client._process.poll())

    def test_worker_fatal_error_is_not_duplicated_by_eof(self):
        script = ('print(\'{"type":"error","id":null,"message":"fake load failure","fatal":true}\',flush=True)')
        client = self.make_client(self.make_root(script))
        client.start()
        error = self.event()
        self.assertEqual(error["message"], "fake load failure")
        client._reader.join(timeout=3)
        self.assertFalse(client._reader.is_alive())
        self.assertTrue(self.events.empty())


if __name__ == "__main__":
    unittest.main()
