"""Offline unit tests; never open the microphone or load model weights."""
import contextlib
import importlib
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CLITests(unittest.TestCase):
    def test_help_without_heavy_imports(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "dogspeak.py"), "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("transcribe", result.stdout)
        self.assertIn("listen", result.stdout)

    def test_cli_options_and_positive_counts(self):
        import dogspeak
        cli = dogspeak.parser()
        args = cli.parse_args(["transcribe", "a.wav", "b.wav", "--threads", "2",
                               "--repeats", "3", "--json", "out.json"])
        self.assertEqual(args.files, [Path("a.wav"), Path("b.wav")])
        self.assertEqual((args.threads, args.repeats, args.json), (2, 3, Path("out.json")))
        self.assertEqual(cli.parse_args(["listen"]).threads, 4)
        self.assertEqual(cli.parse_args(["transcribe", "a.wav"]).repeats, 1)
        for argv in (["transcribe"], ["listen", "--threads", "0"],
                     ["transcribe", "a.wav", "--repeats", "-1"],
                     ["listen", "--threads", "no"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    cli.parse_args(argv)
                self.assertEqual(error.exception.code, 2)

    def test_main_prints_metrics_and_writes_json(self):
        import dogspeak
        result = {"load_seconds": 5, "first_inference_seconds": 2,
                  "memory_after_load": {"current_rss_bytes": 2000, "process_peak_rss_bytes": 3000},
                  "clips": [{"file": "a.wav", "duration_seconds": 1,
                             "inferences": [{"phase": "first", "repeat_index": 0,
                                             "latency_seconds": 2, "real_time_factor": 2,
                                             "text": "hello", "memory": {"current_rss_bytes": 2000,
                                                                         "process_peak_rss_bytes": 3000}}]}]}
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            audio = Path(tmp) / "a.wav"
            audio.touch()
            output = Path(tmp) / "results" / "result.json"
            with mock.patch.object(dogspeak, "benchmark", return_value=result) as bench, \
                    contextlib.redirect_stdout(io.StringIO()) as stdout:
                code = dogspeak.main(["transcribe", str(audio), "--threads", "2",
                                      "--repeats", "3", "--json", str(output)])
            self.assertEqual(code, 0)
            bench.assert_called_once_with([audio], threads=2, repeats=3)
            self.assertEqual(json.loads(output.read_text()), result)
        for label in ("Load:", "First inference:", "Current RSS", "Process peak RSS", "hello", "RTF"):
            self.assertIn(label, stdout.getvalue())

    def test_missing_file_is_rejected_before_loading_model(self):
        import dogspeak
        path = ROOT / "definitely-missing-dogspeak-file.wav"
        with mock.patch.object(dogspeak, "benchmark") as bench, \
                contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(dogspeak.main(["transcribe", str(path)]), 1)
        bench.assert_not_called()
        self.assertIn(str(path), stderr.getvalue())

    def test_runtime_failures_are_short_errors_and_interrupt_is_clean(self):
        import dogspeak
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path = Path(tmp) / "audio.wav"
            path.touch()
            for error in (ValueError("bad audio"), RuntimeError("weight mismatch"),
                          OSError("cannot read file"), ImportError("librosa is required"),
                          KeyboardInterrupt()):
                with self.subTest(error=type(error).__name__), \
                        mock.patch.object(dogspeak, "benchmark", side_effect=error), \
                        contextlib.redirect_stderr(io.StringIO()) as stderr, \
                        contextlib.redirect_stdout(io.StringIO()):
                    try:
                        code = dogspeak.main(["transcribe", str(path)])
                    except BaseException as exc:
                        self.fail(f"CLI leaked {type(exc).__name__}: {exc}")
                    self.assertEqual(code, 130 if isinstance(error, KeyboardInterrupt) else 1)
                    self.assertNotIn("Traceback", stderr.getvalue())
                    if not isinstance(error, KeyboardInterrupt):
                        self.assertIn(str(error), stderr.getvalue())

    def test_run_script_is_offline_and_uses_project_venv_from_other_directory(self):
        script = ROOT / "run.sh"
        self.assertTrue(script.is_file(), "run.sh is missing")
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp) / "project with spaces"
            interpreter = project / ".venv" / "bin" / "python"
            interpreter.parent.mkdir(parents=True)
            interpreter.write_text(
                f"#!{sys.executable}\n"
                "import json, os, sys\n"
                "print(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:], "
                "'hub': os.environ.get('HF_HUB_OFFLINE'), "
                "'transformers': os.environ.get('TRANSFORMERS_OFFLINE')}))\n")
            interpreter.chmod(0o755)
            copied = project / "run.sh"
            copied.write_text(script.read_text())
            copied.chmod(0o755)
            result = subprocess.run([str(copied), "transcribe", "file with spaces.wav"],
                                    cwd=tmp, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["cwd"], str(project))
            self.assertEqual(payload["argv"], [str(project / "dogspeak.py"), "transcribe", "file with spaces.wav"])
            self.assertEqual((payload["hub"], payload["transformers"]), ("1", "1"))


class AudioTests(unittest.TestCase):
    def test_mono_float32_downmix_without_padding(self):
        import dogspeak
        import numpy as np
        normalize = getattr(dogspeak, "normalize_audio", None)
        self.assertTrue(callable(normalize), "normalize_audio is missing")
        audio = normalize(np.array([[0.2, 0.4], [-0.8, 0.4]]), 16000)
        np.testing.assert_allclose(audio, [0.3, -0.2])
        self.assertEqual(audio.dtype, np.float32)
        self.assertEqual(audio.shape, (2,))
        silence = normalize(np.zeros(16), 16000)
        np.testing.assert_array_equal(silence, np.zeros(16))

    def test_reject_invalid_audio_and_duration(self):
        import dogspeak
        import numpy as np
        for audio, rate, message in (
            (np.array([]), 16000, "empty"),
            (np.array([np.nan]), 16000, "finite"),
            (np.array([np.inf]), 16000, "finite"),
            (np.zeros(1), 0, "sample rate"),
            (np.zeros((2, 2, 2)), 16000, "mono or multichannel"),
            (np.zeros((2, 0)), 16000, "empty"),
            (np.zeros(120 * 16000 + 1), 16000, "120"),
        ):
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    dogspeak.normalize_audio(audio, rate)
        self.assertEqual(len(dogspeak.normalize_audio(np.zeros(120 * 16000), 16000)),
                         120 * 16000)

    def test_resample_preserves_duration_and_downmix(self):
        import dogspeak
        import numpy as np
        rate = 48000
        tone = np.sin(2 * np.pi * 440 * np.arange(rate) / rate).astype(np.float32)
        audio = dogspeak.normalize_audio(np.column_stack((tone, tone)), rate)
        self.assertEqual(audio.shape, (16000,))
        self.assertEqual(audio.dtype, np.float32)
        expected = np.sin(2 * np.pi * 440 * np.arange(16000) / 16000)
        np.testing.assert_allclose(audio[100:-100], expected[100:-100], atol=0.001)

    def test_read_audio_validates_before_decoding_long_files(self):
        import dogspeak
        import numpy as np
        import soundfile as sf
        reader = getattr(dogspeak, "read_audio", None)
        self.assertTrue(callable(reader), "read_audio is missing")
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path = Path(tmp) / "audio.wav"
            sf.write(path, np.ones((8000, 2), dtype=np.float32) * 0.25, 8000,
                     subtype="FLOAT")
            audio = reader(path)
            self.assertEqual(audio.shape, (16000,))
            np.testing.assert_allclose(audio[500:-500], 0.25, atol=0.0001)
            sf.write(path, np.zeros(120 * 16000 + 1, dtype=np.float32), 16000)
            with mock.patch.object(sf.SoundFile, "read", side_effect=AssertionError("decoded")):
                with self.assertRaisesRegex(ValueError, "120"):
                    reader(path)
            sf.write(path, np.array([], dtype=np.float32), 16000)
            with self.assertRaisesRegex(ValueError, "empty"):
                reader(path)
            sf.write(path, np.array([np.nan], dtype=np.float32), 16000, subtype="FLOAT")
            with self.assertRaisesRegex(ValueError, "finite"):
                reader(path)

    def test_tiny_clip_that_resamples_to_empty_is_rejected(self):
        import dogspeak
        with self.assertRaisesRegex(ValueError, "empty"):
            dogspeak.normalize_audio([0.1], 192000)


class PipelineTests(unittest.TestCase):
    def test_local_model_and_processor_loaded_once_and_inference_reused(self):
        import dogspeak
        import numpy as np
        import torch
        import weakref
        pipeline_type = getattr(dogspeak, "Pipeline", None)
        self.assertTrue(callable(pipeline_type), "Pipeline is missing")
        states = []
        calls = []

        class State(dict):
            pass

        def decode(path):
            calls.append(("decode", path))
            state = State(weight=torch.tensor([2.0]))
            states.append(weakref.ref(state))
            return state, {"test_config": True}

        class Model(torch.nn.Module):
            def __init__(self, config):
                super().__init__()
                self.weight = torch.nn.Parameter(torch.zeros(1))
                calls.append(("model", config))

            def load_state_dict(self, state, strict=True, assign=False):
                calls.append(("load_state", strict, assign))
                return super().load_state_dict(state, strict=strict, assign=assign)

            def generate(self, **inputs):
                self_test.assertFalse(self.training)
                self_test.assertTrue(torch.is_inference_mode_enabled())
                self_test.assertEqual(self.weight.dtype, torch.float32)
                self_test.assertEqual(self.weight.device.type, "cpu")
                return types.SimpleNamespace(sequences=inputs["input_values"])

        class Processor:
            def __call__(self, waves, **kwargs):
                calls.append(("process", waves, kwargs))
                return {"input_values": torch.from_numpy(waves[0])}

            def batch_decode(self, sequences, **kwargs):
                return ["  actual fake-model text  "]

        self_test = self
        processor = Processor()
        local_processor = mock.Mock(return_value=processor)
        local_generation = mock.Mock(return_value=object())
        transformers = types.SimpleNamespace(
            ParakeetForTDT=Model,
            ParakeetTDTConfig=types.SimpleNamespace(from_dict=lambda cfg: cfg),
            GenerationConfig=types.SimpleNamespace(from_pretrained=local_generation),
            AutoProcessor=types.SimpleNamespace(from_pretrained=local_processor),
        )
        loader = types.SimpleNamespace(load_state_dict=decode)
        no_init = mock.Mock(return_value=contextlib.nullcontext())
        with mock.patch.dict(sys.modules, {"transformers": transformers,
                                           "transformers.initialization": types.SimpleNamespace(no_init_weights=no_init),
                                           "vendor.bark_loader": loader}), \
                mock.patch.object(torch, "set_num_threads") as threads, \
                mock.patch.object(torch, "set_num_interop_threads") as interop:
            pipeline = pipeline_type(threads=3)
            self.assertEqual(pipeline.transcribe(np.zeros(16000, dtype=np.float32)),
                             "actual fake-model text")
            self.assertEqual(pipeline.transcribe(np.ones(8000, dtype=np.float32)),
                             "actual fake-model text")
        threads.assert_called_once_with(3)
        interop.assert_called_once_with(1)
        no_init.assert_called_once_with()
        self.assertIn(("load_state", False, True), calls)
        local_processor.assert_called_once_with(ROOT / "models" / "processor", local_files_only=True)
        local_generation.assert_called_once_with(ROOT / "models" / "processor", local_files_only=True)
        self.assertEqual(len([c for c in calls if c[0] == "decode"]), 1)
        self.assertIsNone(states[0](), "Decoded state dictionary retained after model load")
        processed = [c for c in calls if c[0] == "process"]
        self.assertEqual([len(c[1][0]) for c in processed], [16000, 8000])
        self.assertEqual(processed[0][2]["sampling_rate"], 16000)

    def test_reject_weight_mismatches_except_batch_counters(self):
        import dogspeak
        import torch
        for missing, unexpected, accepted in (
            (["encoder.weight"], [], False),
            ([], ["surprise.weight"], False),
            (["encoder.num_batches_tracked"], [], True),
        ):
            with self.subTest(missing=missing, unexpected=unexpected):
                model = mock.Mock()
                model.load_state_dict.return_value = (missing, unexpected)
                transformers = types.SimpleNamespace(
                    ParakeetForTDT=lambda cfg: model,
                    ParakeetTDTConfig=types.SimpleNamespace(from_dict=lambda cfg: cfg),
                    GenerationConfig=mock.Mock(), AutoProcessor=mock.Mock())
                loader = types.SimpleNamespace(load_state_dict=lambda path: ({}, {}))
                with mock.patch.dict(sys.modules, {"transformers": transformers,
                                                   "transformers.initialization": types.SimpleNamespace(no_init_weights=contextlib.nullcontext),
                                                   "vendor.bark_loader": loader}), \
                        mock.patch.object(torch, "set_num_threads"), \
                        mock.patch.object(torch, "set_num_interop_threads"):
                    if accepted:
                        dogspeak.Pipeline()
                    else:
                        with self.assertRaisesRegex(RuntimeError, "weight.*mismatch"):
                            dogspeak.Pipeline()


class BenchmarkTests(unittest.TestCase):
    def test_current_and_process_peak_memory_are_distinct_bytes(self):
        import dogspeak
        import psutil
        import resource
        memory_usage = getattr(dogspeak, "memory_usage", None)
        self.assertTrue(callable(memory_usage), "memory_usage is missing")
        with mock.patch.object(psutil, "Process") as process, \
                mock.patch.object(resource, "getrusage") as usage:
            process.return_value.memory_info.return_value.rss = 12345
            usage.return_value.ru_maxrss = 99
            memory = memory_usage()
        self.assertEqual(memory["current_rss_bytes"], 12345)
        self.assertEqual(memory["process_peak_rss_bytes"], 99 if sys.platform == "darwin" else 99 * 1024)

    def test_benchmark_load_first_and_repeat_timings_with_one_pipeline(self):
        import dogspeak
        import numpy as np
        import soundfile as sf
        benchmark = getattr(dogspeak, "benchmark", None)
        self.assertTrue(callable(benchmark), "benchmark is missing")

        class FakePipeline:
            def __init__(self, threads):
                self.threads = threads
                self.calls = 0
                pipelines.append(self)

            def transcribe(self, audio):
                self.calls += 1
                return f"text {self.calls}"

        pipelines = []
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            paths = [Path(tmp) / "one.wav", Path(tmp) / "two.wav"]
            for seconds, path in enumerate(paths, 1):
                sf.write(path, np.zeros(seconds * 16000, dtype=np.float32), 16000)
            with mock.patch.object(dogspeak, "Pipeline", FakePipeline), \
                    mock.patch("time.perf_counter", side_effect=[0, 5, 10, 12, 20, 21, 30, 33, 40, 41.5]):
                result = benchmark(paths, threads=2, repeats=1)
        self.assertEqual(len(pipelines), 1)
        self.assertEqual((pipelines[0].threads, pipelines[0].calls), (2, 4))
        self.assertEqual(result["load_seconds"], 5)
        self.assertEqual(result["first_inference_seconds"], 2)
        self.assertEqual(result["config"]["threads"], 2)
        self.assertEqual(result["config"]["padding_seconds"], 0)
        self.assertIn("python", result["versions"])
        self.assertIn("torch", result["versions"])
        self.assertIn("started_at", result)
        self.assertIn("finished_at", result)
        self.assertIn("current_rss_bytes", result["memory_after_load"])
        clips = result["clips"]
        self.assertEqual([c["duration_seconds"] for c in clips], [1, 2])
        self.assertEqual([c["file"] for c in clips], [str(p) for p in paths])
        runs = [run for clip in clips for run in clip["inferences"]]
        self.assertEqual([r["phase"] for r in runs], ["first", "repeat", "first", "repeat"])
        self.assertEqual([r["latency_seconds"] for r in runs], [2, 1, 3, 1.5])
        self.assertEqual([r["real_time_factor"] for r in runs], [2, 1, 1.5, 0.75])
        self.assertEqual([r["text"] for r in runs], ["text 1", "text 2", "text 3", "text 4"])
        for run in runs:
            self.assertGreater(run["memory"]["current_rss_bytes"], 0)
            self.assertGreater(run["memory"]["process_peak_rss_bytes"], 0)
        json.dumps(result, allow_nan=False)


class RecordingTests(unittest.TestCase):
    def test_recording_finalizes_child_on_enter_interrupt_or_timeout(self):
        import dogspeak
        record = getattr(dogspeak, "record", None)
        self.assertTrue(callable(record), "record is missing")
        for interrupt, timeout in ((False, False), (True, False), (False, True)):
            with self.subTest(interrupt=interrupt, timeout=timeout), \
                    tempfile.TemporaryDirectory(dir=ROOT) as tmp:
                path = Path(tmp) / "recordings" / "sample.wav"
                child = mock.Mock()
                child.poll.return_value = None
                child.wait.return_value = 0
                if timeout:
                    child.wait.side_effect = [subprocess.TimeoutExpired("pw-record", 5), 0]
                with mock.patch("subprocess.Popen", return_value=child) as popen, \
                        mock.patch("builtins.input", side_effect=KeyboardInterrupt if interrupt else [""]):
                    if interrupt:
                        with self.assertRaises(KeyboardInterrupt):
                            record(path)
                    else:
                        record(path)
                command = popen.call_args.args[0]
                self.assertEqual(command, ["pw-record", "--rate", "16000", "--channels", "1",
                                           "--format", "f32", str(path)])
                self.assertTrue(popen.call_args.kwargs["start_new_session"])
                child.send_signal.assert_called_once_with(signal.SIGINT)
                if timeout:
                    child.kill.assert_called_once()
                    self.assertEqual(child.wait.call_count, 2)
                else:
                    child.kill.assert_not_called()
                    child.wait.assert_called_once_with(timeout=5)

    def test_listen_reuses_pipeline_for_opt_in_recordings(self):
        import dogspeak
        import numpy as np
        import soundfile as sf
        child = mock.Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        pipeline = mock.Mock()
        pipeline.transcribe.return_value = "heard speech"

        def start_child(command, **kwargs):
            sf.write(command[-1], np.zeros(1600, dtype=np.float32), 16000)
            return child

        with tempfile.TemporaryDirectory(dir=ROOT) as tmp, \
                mock.patch.object(dogspeak, "ROOT", Path(tmp)), \
                mock.patch.object(dogspeak, "Pipeline", return_value=pipeline) as factory, \
                mock.patch("subprocess.Popen", side_effect=start_child) as popen, \
                mock.patch("builtins.input", side_effect=["", "", "", "", "q"]), \
                contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(dogspeak.main(["listen", "--threads", "2"]), 0)
            factory.assert_called_once_with(threads=2)
            self.assertEqual(popen.call_count, 2)
            self.assertEqual(pipeline.transcribe.call_count, 2)
            self.assertEqual(len(list((Path(tmp) / "recordings").glob("*.wav"))), 2)
        self.assertEqual(stdout.getvalue().count("heard speech"), 2)
        self.assertIn("saved", stdout.getvalue().lower())

    def test_listen_eof_stops_recording_without_traceback(self):
        import dogspeak
        child = mock.Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp, \
                mock.patch.object(dogspeak, "ROOT", Path(tmp)), \
                mock.patch.object(dogspeak, "Pipeline"), \
                mock.patch("subprocess.Popen", return_value=child), \
                mock.patch("builtins.input", side_effect=["", EOFError()]), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            try:
                code = dogspeak.main(["listen"])
            except EOFError:
                self.fail("EOF escaped instead of cleanly ending listen")
            self.assertEqual(code, 0)
        child.send_signal.assert_called_once_with(signal.SIGINT)
        child.wait.assert_called_once_with(timeout=5)


if __name__ == "__main__":
    unittest.main()
