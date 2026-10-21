"""Real worker subprocess protocol tests with explicitly fake model/audio only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE_BOOTSTRAP = r'''
import os, runpy, sys, types
fake = types.ModuleType("dogspeak")
class Pipeline:
    def __init__(self):
        assert os.environ["HF_HUB_OFFLINE"] == "1"
        assert os.environ["TRANSFORMERS_OFFLINE"] == "1"
        print("fake model constructed", flush=True)
    def transcribe(self, audio):
        print("fake inference chatter", flush=True)
        return "  fake transcript: " + audio + "\n"
def read_audio(path):
    if str(path) == "bad.wav":
        raise ValueError("fake bad audio")
    return str(path)
fake.Pipeline = Pipeline
fake.read_audio = read_audio
sys.modules["dogspeak"] = fake
runpy.run_module("dogspeak_app.worker", run_name="__main__")
'''


class WorkerTests(unittest.TestCase):
    def run_worker(self, requests, bootstrap=FAKE_BOOTSTRAP):
        completed = subprocess.run(
            [sys.executable, "-u", "-c", bootstrap], cwd=ROOT,
            input="".join(json.dumps(item) + "\n" for item in requests),
            text=True, capture_output=True, timeout=5,
        )
        return completed, [json.loads(line) for line in completed.stdout.splitlines()]

    def test_subprocess_loads_once_preserves_text_and_recovers_per_file(self):
        completed, events = self.run_worker([
            {"action": "transcribe", "id": "first", "path": "one.wav"},
            {"action": "transcribe", "id": "bad", "path": "bad.wav"},
            {"action": "transcribe", "id": "second", "path": "two.wav"},
            {"action": "shutdown"},
        ])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual([e["type"] for e in events],
                         ["loading", "ready", "result", "error", "result"])
        self.assertEqual(completed.stderr.count("fake model constructed"), 1)
        self.assertIn("fake inference chatter", completed.stderr)
        self.assertGreater(events[1]["pid"], 0)
        self.assertGreaterEqual(events[1]["load_seconds"], 0)
        self.assertEqual(events[2]["text"], "  fake transcript: one.wav\n")
        self.assertEqual(events[2]["path"], "one.wav")
        self.assertEqual(events[2]["id"], "first")
        self.assertGreaterEqual(events[2]["seconds"], 0)
        self.assertEqual(events[3], {"type": "error", "id": "bad",
                                    "message": "fake bad audio", "fatal": False})
        self.assertEqual(events[4]["id"], "second")

    def test_load_failure_is_terminal_and_observable(self):
        bootstrap = FAKE_BOOTSTRAP.replace(
            'print("fake model constructed", flush=True)',
            'raise RuntimeError("explicit fake load failure")')
        completed, events = self.run_worker([], bootstrap)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(events, [{"type": "loading"},
            {"type": "error", "id": None, "message": "explicit fake load failure", "fatal": True}])
        self.assertNotIn("Traceback", completed.stderr)

    def test_bad_requests_do_not_lose_loaded_pipeline(self):
        completed, events = self.run_worker([
            [], {"action": "unknown", "id": "wrong"},
            {"action": "transcribe", "id": "missing-path"},
            {"action": "transcribe", "id": None, "path": "clip.wav"},
            {"action": "transcribe", "id": "wrong-path", "path": 12},
            {"action": "transcribe", "id": "good", "path": "clip.wav"},
        ])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual([e["type"] for e in events],
                         ["loading", "ready"] + ["error"] * 5 + ["result"])
        self.assertTrue(all(not event["fatal"] for event in events[2:-1]))
        self.assertEqual(events[-1]["id"], "good")
        self.assertEqual(completed.stderr.count("fake model constructed"), 1)

    def test_native_library_chatter_does_not_corrupt_json_stdout(self):
        bootstrap = FAKE_BOOTSTRAP.replace(
            'print("fake model constructed", flush=True)',
            'os.write(1, b"explicit fake native chatter\\n")')
        completed, events = self.run_worker([], bootstrap)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual([e["type"] for e in events], ["loading", "ready"])
        self.assertIn("explicit fake native chatter", completed.stderr)


if __name__ == "__main__":
    unittest.main()
