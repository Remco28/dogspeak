"""Explicit, local PipeWire capture; no microphone is opened on import."""
from pathlib import Path
import os
import json
import signal
import struct
import subprocess


def _finalized_wav(path):
    """Check RIFF lengths/data, including IEEE float WAV (unsupported by wave)."""
    try:
        with path.open("rb") as source:
            size = path.stat().st_size
            header = source.read(12)
            if (len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE"
                    or struct.unpack("<I", header[4:8])[0] != size - 8):
                return False
            alignment = 0
            data_size = 0
            while source.tell() < size:
                chunk = source.read(8)
                if len(chunk) != 8:
                    return False
                kind, length = struct.unpack("<4sI", chunk)
                end = source.tell() + length + (length % 2)
                if end > size:
                    return False
                if kind == b"fmt ":
                    fmt = source.read(min(length, 16))
                    if len(fmt) != 16 or struct.unpack("<H", fmt[:2])[0] not in (1, 3, 0xFFFE):
                        return False
                    alignment = struct.unpack("<H", fmt[12:14])[0]
                elif kind == b"data":
                    data_size += length
                source.seek(end)
            return alignment > 0 and data_size > 0 and data_size % alignment == 0
    except (OSError, ValueError):
        return False


class Recorder:
    def __init__(self):
        self._process = None
        self._path = None

    @property
    def running(self):
        return self._process is not None and self._process.poll() is None

    def start(self, path: Path, target: str | None = None):
        if self._process is not None:
            raise RuntimeError("A recording already exists; stop or cancel it first")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        command = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "f32"]
        if target is not None:
            command.extend(["--target", target])
        command.append(str(path))
        try:
            self._process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL,
                                             start_new_session=True)
        except OSError as error:
            path.unlink(missing_ok=True)
            raise RuntimeError(f"Cannot start pw-record; install/check PipeWire: {error}") from error
        self._path = path

    def stop(self) -> Path:
        if self._process is None:
            raise RuntimeError("No recording to stop")
        child = self._process
        interrupted = False
        if child.poll() is None:
            try:
                child.send_signal(signal.SIGINT)
                interrupted = True
            except ProcessLookupError:
                pass
        timed_out = False
        try:
            status = child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            timed_out = True
            child.kill()
            status = child.wait(timeout=2)
        self._process = None
        if timed_out:
            raise RuntimeError("pw-record timed out finalizing audio; partial capture is not usable")
        # PipeWire versions can return 1 after a deliberate SIGINT despite a
        # valid finalized float WAV. Never accept an early device failure.
        finalized_interrupt = (status == 1 and interrupted and self._path is not None
                               and _finalized_wav(self._path))
        if status not in (0, -signal.SIGINT, 128 + signal.SIGINT) and not finalized_interrupt:
            raise RuntimeError(f"pw-record exited with status {status}; check microphone/PipeWire")
        assert self._path is not None
        path, self._path = self._path, None
        return path

    def cancel(self):
        path = self._path
        try:
            if self._process is not None:
                self.stop()
        except RuntimeError:
            if self._process is not None:
                raise
        finally:
            if self._process is None and path is not None:
                path.unlink(missing_ok=True)
                self._path = None


def list_sources():
    """List selectable input node names without creating a recording stream."""
    try:
        result = subprocess.run(["pw-dump"], capture_output=True, text=True,
                                check=True, timeout=5)
        nodes = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise RuntimeError(f"Cannot list microphones with pw-dump; check PipeWire: {error}") from error
    sources = []
    for node in nodes:
        props = (node.get("info") or {}).get("props", {})
        name = props.get("node.name", "")
        if (props.get("media.class") == "Audio/Source" and name
                and not name.endswith(".monitor")
                and str(props.get("stream.monitor", "false")).lower() != "true"):
            sources.append({"name": name, "description": props.get("node.description")
                            or props.get("node.nick") or name})
    return sources
