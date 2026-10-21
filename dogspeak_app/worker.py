"""Offline inference subprocess. Stdout is exclusively JSON lines."""
import json
import os
import sys
import time


def serve(output):
    def emit(kind, **fields):
        output.write(json.dumps({"type": kind, **fields}, ensure_ascii=False,
                                allow_nan=False) + "\n")
        output.flush()

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    emit("loading")
    started = time.perf_counter()
    try:
        from dogspeak import Pipeline, read_audio
        pipeline = Pipeline()
    except Exception as error:
        emit("error", id=None, message=str(error), fatal=True)
        return 1
    emit("ready", load_seconds=time.perf_counter() - started, pid=os.getpid())
    for line in sys.stdin:
        request_id = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Request must be a JSON object")
            request_id = request.get("id")
            if request.get("action") == "shutdown":
                return 0
            if request.get("action") != "transcribe":
                raise ValueError("Unknown action; expected transcribe or shutdown")
            path = request["path"]
            if not isinstance(request_id, str) or not request_id:
                raise ValueError("Transcribe requires a nonempty string id")
            if not isinstance(path, str) or not path:
                raise ValueError("Transcribe requires a nonempty string path")
            started = time.perf_counter()
            text = pipeline.transcribe(read_audio(path))
            emit("result", id=request_id, path=path, text=text,
                 seconds=time.perf_counter() - started)
        except Exception as error:
            emit("error", id=request_id, message=str(error), fatal=False)
    return 0


def main():
    # Keep a private copy of the protocol pipe, redirect even native library
    # writes to fd 1 into stderr, and never expose transcripts in the log.
    sys.stdout.flush()
    protocol_fd = os.dup(sys.stdout.fileno())
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    with os.fdopen(protocol_fd, "w", encoding="utf-8", buffering=1) as output:
        return serve(output)


if __name__ == "__main__":
    raise SystemExit(main())
