"""Small stdlib client for the long-lived offline inference subprocess."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading

from .paths import ROOT, no_window_flags, state_dir, venv_python

# Linux can make one small pipe write atomic and non-blocking. Windows has no
# PIPE_BUF, so there a writer thread does the blocking writes instead.
ATOMIC_PIPE_WRITES = hasattr(os, "fpathconf")


class EngineClient:
    """Callbacks run on the reader thread; GUI callers must marshal them."""
    def __init__(self, callback, root=ROOT, log_path=None, python=None):
        self.callback = callback
        self.root = Path(root)
        self.python = Path(python) if python is not None else venv_python(self.root)
        self.log_path = Path(log_path) if log_path is not None else state_dir() / "worker.log"
        self._process = None
        self._outbox = None
        self._reader = None
        self._log = None
        self._closing = False
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()

    def start(self):
        with self._state_lock:
            if self._reader is not None or self._closing:
                return
            self._reader = threading.Thread(target=self._launch_and_read,
                                            name="dogspeak-engine-reader", daemon=True)
            self._reader.start()

    def _launch_and_read(self):
        try:
            with self._state_lock:
                if self._closing:
                    return
                self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                flags = (os.O_WRONLY | os.O_CREAT | os.O_APPEND
                         | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
                fd = os.open(self.log_path, flags, 0o600)
                if hasattr(os, "fchmod"):
                    os.fchmod(fd, 0o600)
                self._log = os.fdopen(fd, "wb", buffering=0)
                env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                           HF_HUB_DISABLE_TELEMETRY="1")
                self._process = subprocess.Popen(
                    [str(self.python), "-u", "-m", "dogspeak_app.worker"],
                    cwd=self.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=self._log, text=True, encoding="utf-8", bufsize=1, start_new_session=True,
                    creationflags=no_window_flags(),
                )
                assert self._process.stdin is not None
                if ATOMIC_PIPE_WRITES:
                    os.set_blocking(self._process.stdin.fileno(), False)
                else:
                    self._outbox = queue.Queue(maxsize=4)
                    threading.Thread(target=self._write_outbox, args=(self._process, self._outbox),
                                     name="dogspeak-engine-writer", daemon=True).start()
        except OSError as error:
            if self._log is not None:
                self._log.close()
            if not self._closing:
                self.callback({"type": "error", "id": None, "fatal": True,
                               "message": f"Cannot start offline inference worker; check {self.python}: {error}"})
            return
        self._read_events()

    def _read_events(self):
        child = self._process
        assert child is not None and child.stdout is not None
        fatal_received = False
        failure = None
        try:
            for line in child.stdout:
                event = json.loads(line)
                fatal_received |= event.get("type") == "error" and bool(event.get("fatal"))
                self.callback(event)
        except Exception as error:
            failure = f"Invalid worker protocol: {error}"
        finally:
            try:
                status = child.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                child.kill()
                status = child.wait(timeout=2)
            if not self._closing and not fatal_received:
                message = failure or f"Inference worker exited unexpectedly (status {status})"
                self.callback({"type": "error", "id": None, "fatal": True,
                               "message": f"{message}; press Record to restart it. See log: {self.log_path}"})

    def _send(self, message):
        with self._write_lock:
            if self._process is None or self._process.poll() is not None:
                raise RuntimeError("Inference worker is not running; restart Dogspeak")
            stream = self._process.stdin
            assert stream is not None
            fd = stream.fileno()
            payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
            if self._outbox is not None:
                try:
                    self._outbox.put_nowait(payload)
                except queue.Full as error:
                    raise RuntimeError("Inference worker is busy; wait before retrying") from error
                return
            # Atomic nonblocking writes: a stalled/dead worker must never hang
            # the GUI, including during shutdown. The UI submits one job at a time.
            if len(payload) > os.fpathconf(fd, "PC_PIPE_BUF"):
                raise RuntimeError("Worker request is too long; use a shorter audio path/id")
            try:
                os.write(fd, payload)
            except BlockingIOError as error:
                raise RuntimeError("Inference worker is busy; wait before retrying") from error
            except OSError as error:
                raise RuntimeError("Cannot contact inference worker; restart Dogspeak") from error

    @staticmethod
    def _write_outbox(child, outbox):
        """Blocking writes off the UI thread; a stalled worker only stalls this thread."""
        fd = child.stdin.fileno()
        while (payload := outbox.get()) is not None:
            try:
                while payload:
                    payload = payload[os.write(fd, payload):]
            except OSError:
                return

    def transcribe(self, path, request_id):
        if self._closing:
            raise RuntimeError("Inference worker is closing")
        self._send({"action": "transcribe", "id": request_id, "path": str(path)})

    def close(self):
        with self._state_lock:
            if self._closing:
                return
            self._closing = True
            child = self._process
        if child is not None:
            try:
                if child.poll() is None:
                    self._send({"action": "shutdown"})
            except (OSError, RuntimeError):
                pass
            if self._outbox is not None:
                try:
                    self._outbox.put_nowait(None)
                except queue.Full:
                    pass
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.terminate()
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=2)
            for stream in (child.stdin, child.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=1)
        if self._log is not None:
            self._log.close()
