"""Microphone capture through PortAudio (the sounddevice package); used on Windows.

Same interface as audio.Recorder: start(path, target), stop() -> Path, cancel(),
running, plus list_sources(). No microphone is opened on import or on listing.
"""
from pathlib import Path
import queue
import threading

SAMPLE_RATE = 16000
SILENCE = ("The microphone recorded only silence. If that's unexpected, check Windows "
           "Settings → Privacy & security → Microphone → Let desktop apps access your microphone")


def _sounddevice():
    import sounddevice
    return sounddevice


def _hostapi(sd):
    """WASAPI where there is one: full device names and Windows' own sample-rate conversion.

    The default host API (MME) cuts names to 31 characters.
    """
    for index, api in enumerate(sd.query_hostapis()):
        if "WASAPI" in api["name"]:
            return index, api
    index = sd.default.hostapi
    return index, sd.query_hostapis(index)


def list_sources(sd=None):
    sd = sd or _sounddevice()
    try:
        _index, api = _hostapi(sd)
        sources, seen = [], set()
        for device in api["devices"]:
            info = sd.query_devices(device)
            if info["max_input_channels"] > 0 and info["name"] not in seen:
                seen.add(info["name"])
                sources.append({"name": info["name"], "description": info["name"]})
        return sources
    except Exception as error:
        raise RuntimeError(f"Cannot list microphones: {error}") from error


class Recorder:
    def __init__(self, sd=None, soundfile=None):
        self._sd, self._sf = sd, soundfile
        self._stream = None
        self._path = None
        self._writer = None
        self._chunks = None
        self._error = None
        self.frames = 0
        self.peak = 0.0

    @property
    def running(self):
        stream = self._stream
        return stream is not None and bool(stream.active) and self._error is None

    def _device(self, sd, target):
        hostapi, api = _hostapi(sd)
        if target:
            for device in api["devices"]:
                if sd.query_devices(device)["name"] == target:
                    return device
        # The saved microphone is unplugged, or none was chosen: the system default.
        device = api.get("default_input_device", -1)
        if device is None or device < 0:
            raise RuntimeError("No microphone found; plug one in or check Windows sound settings")
        return device

    def _open(self, sd, device, callback):
        """16 kHz mono with Windows converting, else the device's own format."""
        try:
            settings = sd.WasapiSettings(auto_convert=True)
            stream = sd.InputStream(device=device, samplerate=SAMPLE_RATE, channels=1,
                                    dtype="float32", callback=callback, extra_settings=settings)
            return stream, SAMPLE_RATE, 1
        except Exception:
            info = sd.query_devices(device)
            rate = int(info["default_samplerate"])
            channels = max(1, min(2, int(info["max_input_channels"])))
            stream = sd.InputStream(device=device, samplerate=rate, channels=channels,
                                    dtype="float32", callback=callback)
            return stream, rate, channels

    def start(self, path: Path, target: str | None = None):
        if self._stream is not None or self._path is not None:
            raise RuntimeError("A recording already exists; stop or cancel it first")
        sd = self._sd or _sounddevice()
        if self._sf is None:
            import soundfile
            self._sf = soundfile
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        chunks = queue.Queue()
        self._error, self.frames, self.peak = None, 0, 0.0

        def callback(indata, _frames, _time, _status):
            chunks.put(indata.copy())

        try:
            stream, rate, channels = self._open(sd, self._device(sd, target), callback)
        except Exception as error:
            raise RuntimeError(f"Cannot open the microphone: {error}") from error
        try:
            output = self._sf.SoundFile(str(path), "x", samplerate=rate, channels=channels,
                                        format="WAV", subtype="FLOAT")
        except Exception:
            stream.close()
            raise
        self._path, self._chunks, self._stream = path, chunks, stream
        self._writer = threading.Thread(target=self._write, args=(output, chunks),
                                        name="dogspeak-capture-writer", daemon=True)
        self._writer.start()
        try:
            stream.start()
        except Exception as error:
            self._shutdown()
            path.unlink(missing_ok=True)
            self._path = None
            raise RuntimeError(f"Cannot start recording: {error}") from error

    def _write(self, output, chunks):
        try:
            with output:
                while (chunk := chunks.get()) is not None:
                    output.write(chunk)
                    self.frames += len(chunk)
                    if chunk.size:
                        self.peak = max(self.peak, float(abs(chunk).max()))
        except Exception as error:
            self._error = error

    def _shutdown(self):
        """Close the stream and finish the file; safe to call twice."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        writer, self._writer = self._writer, None
        if writer is not None:
            self._chunks.put(None)
            writer.join(timeout=10)
            if writer.is_alive():
                raise RuntimeError("Timed out finishing the recording")

    def stop(self) -> Path:
        if self._path is None:
            raise RuntimeError("No recording to stop")
        self._shutdown()
        if self._error is not None:
            raise RuntimeError(f"Could not write the recording: {self._error}")
        if self.frames == 0:
            raise RuntimeError("No audio was captured; check the microphone")
        if self.peak == 0.0:
            # Exact zeros mean Windows blocked the microphone, not a quiet room.
            raise RuntimeError(SILENCE)
        path, self._path = self._path, None
        return path

    def cancel(self):
        path, self._path = self._path, None
        try:
            self._shutdown()
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
