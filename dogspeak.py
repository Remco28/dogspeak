#!/usr/bin/env python3
"""Small offline CPU feasibility harness for Underdog Bark."""
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent


SAMPLE_RATE = 16000
MAX_SECONDS = 120


def normalize_audio(audio, sample_rate):
    """Return unpadded mono float32 audio at 16 kHz."""
    import numpy as np

    audio = np.asarray(audio, dtype=np.float32)
    if sample_rate <= 0 or not np.isfinite(sample_rate):
        raise ValueError("Audio sample rate must be positive and finite")
    if audio.ndim not in (1, 2):
        raise ValueError("Expected mono or multichannel audio (frames, channels)")
    if audio.size == 0:
        raise ValueError("Audio is empty")
    if len(audio) / sample_rate > MAX_SECONDS:
        raise ValueError(f"Audio exceeds {MAX_SECONDS}s limit; split the file first")
    if not np.isfinite(audio).all():
        raise ValueError("Audio must contain only finite samples")
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    if sample_rate != SAMPLE_RATE:
        import soxr
        audio = soxr.resample(audio, sample_rate, SAMPLE_RATE)
    if audio.size == 0:
        raise ValueError("Audio is empty after resampling")
    return np.ascontiguousarray(audio, dtype=np.float32)


def read_audio(path):
    """Check duration before allocating decoded samples."""
    import soundfile as sf

    with sf.SoundFile(path) as source:
        if source.frames / source.samplerate > MAX_SECONDS:
            raise ValueError(f"{path}: exceeds {MAX_SECONDS}s limit; split the file first")
        audio = source.read(dtype="float32", always_2d=True)
        return normalize_audio(audio, source.samplerate)


class Pipeline:
    """One CPU model and cached, strictly local processor per process."""

    def __init__(self, threads=4):
        import torch
        from transformers import (AutoProcessor, GenerationConfig, ParakeetForTDT,
                                  ParakeetTDTConfig)
        from transformers.initialization import no_init_weights
        from vendor.bark_loader import load_state_dict

        torch.set_num_threads(threads)
        torch.set_num_interop_threads(1)
        state, config = load_state_dict(ROOT / "models" / "model.bark")
        # Avoid touching an unused initialized FP32 copy, then replace parameters
        # with the decoded weights rather than copying them into another allocation.
        with no_init_weights():
            self.model = ParakeetForTDT(ParakeetTDTConfig.from_dict(config))
        missing, unexpected = self.model.load_state_dict(
            {key: value.float() for key, value in state.items()}, strict=False, assign=True)
        del state
        if unexpected or any(key.split(".")[-1] != "num_batches_tracked" for key in missing):
            raise RuntimeError(f"Model weight mismatch: missing={missing}, unexpected={unexpected}")
        self.model.eval().to(device="cpu", dtype=torch.float32)
        processor_dir = ROOT / "models" / "processor"
        self.model.generation_config = GenerationConfig.from_pretrained(
            processor_dir, local_files_only=True)
        self.processor = AutoProcessor.from_pretrained(processor_dir, local_files_only=True)

    def transcribe(self, audio):
        import torch

        audio = normalize_audio(audio, SAMPLE_RATE)
        inputs = self.processor([audio], sampling_rate=SAMPLE_RATE,
                                return_tensors="pt", padding=True)
        with torch.inference_mode():
            output = self.model.generate(**inputs)
        return self.processor.batch_decode(output.sequences, skip_special_tokens=True)[0].strip()


def memory_usage():
    """Current RSS and lifetime process high-water mark, both in bytes."""
    import psutil
    import resource
    import sys

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"current_rss_bytes": psutil.Process().memory_info().rss,
            "process_peak_rss_bytes": int(peak if sys.platform == "darwin" else peak * 1024)}


def benchmark(files, threads=4, repeats=1):
    """Measure load separately from inference; repeats are additional runs per clip."""
    import importlib.metadata
    import platform
    import time
    from datetime import datetime, timezone

    result = {"started_at": datetime.now(timezone.utc).isoformat(),
              "versions": {"python": platform.python_version()},
              "config": {"threads": threads, "interop_threads": 1,
                         "device": "cpu", "dtype": "float32", "sample_rate": SAMPLE_RATE,
                         "padding_seconds": 0, "max_audio_seconds": MAX_SECONDS,
                         "repeats_per_clip": repeats}, "clips": []}
    for package in ("torch", "transformers", "numpy", "soundfile", "soxr", "constriction", "psutil", "librosa"):
        result["versions"][package] = importlib.metadata.version(package)
    start = time.perf_counter()
    pipeline = Pipeline(threads=threads)
    result["load_seconds"] = time.perf_counter() - start
    result["memory_after_load"] = memory_usage()
    for path in files:
        audio = read_audio(path)
        duration = len(audio) / SAMPLE_RATE
        clip = {"file": str(path), "duration_seconds": duration, "inferences": []}
        for index in range(repeats + 1):
            start = time.perf_counter()
            text = pipeline.transcribe(audio)
            latency = time.perf_counter() - start
            if not result["clips"] and index == 0:
                result["first_inference_seconds"] = latency
            clip["inferences"].append({"phase": "first" if index == 0 else "repeat",
                                       "repeat_index": index, "latency_seconds": latency,
                                       "real_time_factor": latency / duration,
                                       "text": text, "memory": memory_usage()})
        result["clips"].append(clip)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result


def record(path):
    """Start only on explicit listen action; always stop and reap pw-record."""
    import signal
    import subprocess

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    child = subprocess.Popen(["pw-record", "--rate", str(SAMPLE_RATE), "--channels", "1",
                              "--format", "f32", str(path)],
                             stdin=subprocess.DEVNULL, start_new_session=True)
    stopped_by_us = False
    try:
        input("Recording. Press Enter to stop (Ctrl-C cancels): ")
    finally:
        if child.poll() is None:
            child.send_signal(signal.SIGINT)
            stopped_by_us = True
        try:
            status = child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            status = child.wait()
    if status == 1 and stopped_by_us:
        # PipeWire can return 1 for an intentional SIGINT despite finalizing
        # a valid WAV. Never accept an early device failure on status alone.
        import soundfile as sf
        try:
            completed = sf.info(path)
            if completed.frames > 0 and completed.samplerate == SAMPLE_RATE:
                return
        except (OSError, RuntimeError):
            pass
    if status not in (0, -signal.SIGINT, 128 + signal.SIGINT):
        raise RuntimeError(f"pw-record exited with status {status}")


def listen(threads=4):
    from datetime import datetime, timezone

    print("Loading local CPU model (this may take a while)...", flush=True)
    pipeline = Pipeline(threads=threads)
    print(f"Microphone is OFF until Enter. Recordings are saved in {ROOT / 'recordings'}.")
    print(f"Keep each recording under {MAX_SECONDS}s. No clipboard or global hotkeys.")
    while True:
        action = input("Enter to start recording; q + Enter to quit: ").strip().lower()
        if action in ("q", "quit", "exit"):
            return
        if action:
            continue
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        path = ROOT / "recordings" / f"{stamp}.wav"
        record(path)
        print(f"Saved: {path}")
        text = pipeline.transcribe(read_audio(path))
        print(f"Text: {text}")


def positive_int(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def parser():
    cli = argparse.ArgumentParser(description=__doc__)
    commands = cli.add_subparsers(dest="command", required=True)
    transcribe = commands.add_parser("transcribe", help="Transcribe local audio files")
    transcribe.add_argument("files", nargs="+", type=Path)
    transcribe.add_argument("--repeats", type=positive_int, default=1,
                            help="Additional inferences per clip after its first (default: 1)")
    transcribe.add_argument("--json", type=Path, help="Write benchmark results to this file")
    listen = commands.add_parser("listen", help="Opt-in, Enter-to-start/stop recording")
    for command in (transcribe, listen):
        command.add_argument("--threads", type=positive_int, default=4,
                             help="CPU inference threads (default: 4)")
    return cli


def print_report(result):
    print(f"Load: {result['load_seconds']:.3f}s")
    print(f"First inference: {result['first_inference_seconds']:.3f}s (process first call)")
    memory = result["memory_after_load"]
    print(f"After load: Current RSS {memory['current_rss_bytes'] / 2**20:.1f} MiB; "
          f"Process peak RSS {memory['process_peak_rss_bytes'] / 2**20:.1f} MiB")
    for clip in result["clips"]:
        print(f"\n{clip['file']} — {clip['duration_seconds']:.3f}s audio")
        for run in clip["inferences"]:
            memory = run["memory"]
            print(f"  {run['phase']} {run['repeat_index']}: {run['latency_seconds']:.3f}s, "
                  f"RTF {run['real_time_factor']:.3f}; "
                  f"Current RSS {memory['current_rss_bytes'] / 2**20:.1f} MiB; "
                  f"Process peak RSS {memory['process_peak_rss_bytes'] / 2**20:.1f} MiB")
            print(f"    {run['text']!r}")


def main(argv=None):
    import json
    import sys

    args = parser().parse_args(argv)
    try:
        if args.command == "transcribe":
            for path in args.files:
                if not path.is_file():
                    raise FileNotFoundError(f"audio file not found: {path}")
            print("Loading local CPU model (this may take a while)...", flush=True)
            result = benchmark(args.files, threads=args.threads, repeats=args.repeats)
            print_report(result)
            if args.json:
                args.json.parent.mkdir(parents=True, exist_ok=True)
                args.json.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                                     encoding="utf-8")
                print(f"JSON: {args.json}")
        else:
            listen(threads=args.threads)
    except EOFError:
        print("\nInput closed; stopped.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(f"dogspeak: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
