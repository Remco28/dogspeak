# Dogspeak

An **editor-first local dictation scratchpad**, growing toward a prompt design lab. Dictate a paragraph, edit it, and copy it to a coding agent. The first desktop version uses GTK4/libadwaita, local Bark Clean speech recognition, and SQLite autosave. No cloud requests or language-model processing are made.

## Open the scratchpad

On the original ThinkPad checkout, dependencies and model assets are already present:

```sh
cd ~/Dev/dogspeak
./app.sh
```

For a fresh clone, follow [SETUP.md](SETUP.md) first. The native frontend runs with distro `/usr/bin/python3` and PyGObject. A separate project-venv Python worker loads the model once per session; editing stays responsive during startup and transcription. Startup can take roughly half a minute and the resident model process uses about 3.1 GiB of RAM.

### First-version workflow

- Edit the main draft and its title; changes save locally.
- **Record / Stop** or **Ctrl+Shift+R** toggles capture while the app has focus.
- Select **System default** or a specific microphone.
- After stopping, the transcript appends as a new paragraph. You can keep editing while it processes.
- **Copy all** or **Ctrl+Shift+C** copies the draft; ordinary selection/copy also works.
- **New draft / Ctrl+N** starts another draft without discarding the previous one; reopen saved drafts when needed.
- Import an existing WAV/FLAC to retry a recording. The original CLI is still available below.

Recording starts only on explicit action, never at app launch or as a deferred surprise after loading. Capture automatically stops at 115 seconds, below the engine's 120-second input limit. Closing the app finalizes capture and stops its model worker; there is no permanent background service or automatic login startup. No text is pasted or sent to another program automatically.

User data is separate from the checkout, under `$XDG_DATA_HOME/dogspeak` (normally `~/.local/share/dogspeak`). This includes the local database, recordings, original ASR text and content revisions. `DOGSPEAK_DATA_DIR` overrides this for isolated testing. Recording files are retained; review disk use and remove unwanted audio deliberately. No user data is uploaded or committed.

### Application launcher and optional global shortcut

Install the launcher in your user account (no sudo):

```sh
/usr/bin/python3 desktop_install.py
```

Dogspeak will appear in the desktop application launcher. To additionally opt in to a GNOME **Super+Shift+D** global record toggle:

```sh
/usr/bin/python3 desktop_install.py --shortcut
```

This preserves other custom shortcuts and refuses a conflicting custom binding. It does not take over Spokenly's shortcut. If Dogspeak is not running, the first invocation opens/loads it; recording is not queued while the model loads. The app must remain open for warm, ready-to-record use. You can also assign `~/Dev/dogspeak/app.sh --toggle-recording` in your desktop's shortcut settings.

## Product direction

The editor remains the primary artifact. Future, separately approved operations are **clean up**, **combine/structure**, and **develop/critique a prompt**. They will propose revisions rather than silently replacing your draft, preserve source notes, and ask about missing or contradictory requirements. Those LLM features are not in this release. See [PRODUCT.md](PRODUCT.md) and [ARCHITECTURE.md](ARCHITECTURE.md) for the recorded decisions.

## Existing CLI and measurements

```sh
# Interactive terminal recording (Enter starts/stops; q quits)
./run.sh listen

# Transcribe files; two ADDITIONAL repeats after the first inference
./run.sh transcribe samples/jfk.flac --threads 4 --repeats 2 \
  --json results/cli-benchmark.json
```

Multiple files share one loaded model. The CLI reports startup, first/repeat inference, audio duration, real-time factor, current RSS and lifetime peak RSS separately. It accepts nonempty finite audio up to 120 seconds, downmixes stereo and resamples to 16 kHz. For paths outside the checkout, pass absolute filenames. CLI recordings/results remain under the Git-ignored source directories; GUI data lives in the XDG data directory instead.

Public speech and the user's own speech have run on this ThinkPad. The user confirmed disfluency cleanup; this does not imply perfect names, technical vocabulary, or self-correction handling. [BENCHMARK.md](BENCHMARK.md) retains the initial CPU measurements and their limitations. Dictation and inference use local assets; model/processor downloads happen only during setup.

## Tests

```sh
./test.sh
```

This runs core/ASR tests in the inference venv, then native GTK tests under Xvfb using system Python. GUI bindings are intentionally not injected into the inference environment. Automated unit tests use isolated data and simulated hardware/model behavior where appropriate; real recognition and the PipeWire capture path are exercised separately with public speech. Never treat mocked output as proof of transcription quality.

## Verification

See [the scratchpad verification report](docs/SCRATCHPAD-VERIFICATION.md) for
real native-app/worker/capture/persistence checks and an actual screenshot.

## Files and attribution

- [SETUP.md](SETUP.md): prerequisites, pinned downloads, offline assets, privacy.
- `upstream.json` / `fixtures.json`: source revisions, checksums, public fixture identities.
- `vendor/`: unchanged upstream loader, model card and attribution notice.
- `docs/`: public-sample benchmark evidence only—no personal recordings/transcripts.

Model derivatives are CC-BY-4.0; retain `vendor/NOTICE`. A license for new Dogspeak application code has not yet been selected. Model weights, `.venv`, credentials, recordings, raw private results and user databases are not published.
