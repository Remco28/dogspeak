# Dogspeak

A small local dictation-scratchpad project, starting with a **Bark 3.0 Clean CPU feasibility test**. There is no GUI yet. Spokenly and desktop hotkeys are untouched.

**Initial result:** real speech transcribes locally on this ThinkPad. Short clips took about 1–2 seconds with the model loaded; a repeated 33-second clip took about 4–5 seconds. The efficient FP32 loading path used about 3.1 GiB resident. This is promising, but the user's own speech and cleanup quality are not yet validated. See [BENCHMARK.md](BENCHMARK.md) for measurements, limitations, and raw public-sample output.

## Try your own speech

On the original ThinkPad checkout, the environment and model are already installed. For a fresh clone, first follow [SETUP.md](SETUP.md) to install dependencies and download the model. Then:

```sh
cd ~/Dev/dogspeak
./run.sh listen
```

1. Wait for the model to load (roughly half a minute; it stays loaded for the session).
2. Press **Enter** to start recording using your system's default PipeWire microphone.
3. Speak normally. Press **Enter** again to stop and see the transcript.
4. Repeat with another thought without reloading the model.
5. Type **q**, then Enter, to exit. Ctrl-C stops the session and finalizes an active recording.

The microphone is **off until you explicitly start recording**. Recordings are retained under `recordings/` for review and are ignored by Git. Keep each recording under **120 seconds**; longer clips are rejected, not silently truncated. Check the input device in system sound settings first if needed. This does not repair an unreliable Bluetooth microphone.

Try a normal coding-agent prompt, then one with hesitations, technical names, and a correction such as “actually, don't change that function.” Review whether the cleanup preserved your intent. No text is pasted or sent anywhere automatically.

**Current prototype differences from the intended app:** terminal input instead of a global hotkey, printed transcripts instead of an editable scratchpad, and manual terminal selection/copying. Those UI features come only after the model earns its place.

## Transcribe and measure existing audio

```sh
./run.sh transcribe samples/jfk.flac --threads 4 --repeats 2 \
  --json results/cli-benchmark.json
```

`--repeats 2` means **two additional inferences after the first** (three runs per clip). Multiple input files share one loaded model. Output separates load time, process-first inference, per-clip timings, real-time factor, current resident RAM, and lifetime peak resident RAM. Lower real-time factor is faster: below 1 means processing faster than the audio duration.

WAV/FLAC and other formats supported by libsndfile can be read; stereo is downmixed and sample rates are converted to 16 kHz. Inputs must be nonempty, finite, and no longer than 120 seconds. Inference is whole-recording, not live streaming. For paths outside this checkout, pass absolute filenames.

`run.sh` uses the project's installed `.venv` directly and enables offline mode. It never installs or downloads anything during use. The model and processor load from local paths.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
sh -n run.sh
```

Unit tests do not capture audio or load the real model. Real-weight inference is verified separately by the benchmark; successful mocked tests alone are not evidence of recognition quality.

## Project notes

- [PRODUCT.md](PRODUCT.md): small scratchpad scope and acceptance gate.
- [BENCHMARK.md](BENCHMARK.md): measured CPU feasibility and remaining questions.
- [SETUP.md](SETUP.md): reproducible dependencies, pinned downloads, privacy, and attribution.
- `upstream.json`: source revisions, sizes, and hashes.
- `fixtures.json`: public speech references and stable fixture identities.
- `vendor/`: unchanged upstream loader, model card, and attribution notice.

No system service, startup entry, clipboard integration, or desktop shortcut is installed. Personal recordings and transcripts, model weights, and the environment are excluded from Git.
