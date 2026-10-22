# Dogspeak setup, step by step

`./setup.sh` does all of this for you; this page explains what it does, so you
can check it or do it by hand. For the short version, see the
[README](README.md#install).

## 1. System packages

The window runs on your distribution's own Python with PyGObject; it never
imports the speech-recognition packages. It needs GTK 4, libadwaita, the
Secret Service bindings (for storing Clean up API keys) and the PipeWire tools
`pw-record` and `pw-dump` (for recording and listing microphones). On Ubuntu or
Debian:

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-secret-1 pipewire-bin
```

Check with:

```sh
/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); gi.require_version('Secret','1'); from gi.repository import Gtk, Adw, Secret; print('ok')"
command -v pw-record pw-dump
```

For the test suite you also need `xvfb` (headless display) and `dbus-daemon`.
Don't install these bindings into the speech-recognition environment, and
don't run the window with that environment's Python.

## 2. Speech-recognition environment

A separate Python 3.12 environment in `.venv` holds PyTorch (CPU-only),
Transformers and the other recognition packages. [uv](https://docs.astral.sh/uv/)
builds it from the pinned versions in `uv.lock`, downloading Python 3.12 if
your system doesn't have it:

```sh
uv sync --frozen
```

PyTorch comes from the CPU-only wheel index, so no CUDA packages are pulled
in. `librosa` is included because the Parakeet feature extractor in current
Transformers needs it. The `anthropic` SDK is also pinned here; only the
optional Claude provider for Clean up uses it (`dogspeak_app/claude.py`).

## 3. Model files

No Hugging Face account is needed. `setup.sh` downloads these files at the
exact revisions recorded in `upstream.json` and checks each one's SHA-256:

| File | From | Size |
|---|---|---|
| `models/model.bark` | `ConwayResearch/Underdog-Bark-3.0-Clean` @ `6d34ed86` | 179 MB |
| `models/processor/*.json` (5 files) | `nvidia/parakeet-tdt-0.6b-v3` @ `541d1f99` | 1.2 MB |
| `samples/jfk.flac` | OpenAI Whisper's test audio @ `86098128` | 1.2 MB |

Only the processor configuration is taken from NVIDIA's repository, not its
full weights. To download by hand instead:

```sh
.venv/bin/hf download ConwayResearch/Underdog-Bark-3.0-Clean model.bark \
  --revision 6d34ed8639b2b9a33e764acf06748519bbb950cf --local-dir models
.venv/bin/hf download nvidia/parakeet-tdt-0.6b-v3 \
  config.json generation_config.json processor_config.json tokenizer.json tokenizer_config.json \
  --revision 541d1f99c6b0c3cd0b11a95167540bb8edefd82b --local-dir models/processor
sha256sum models/model.bark   # b67ca90d94b79a36af4616f1cb3f241884925de7aa9f3e673a8d5c6c4cfd3436
```

After setup, recognition runs offline: `app.sh` and `run.sh` switch Hugging
Face and Transformers to offline mode.

## 4. Optional: more test audio

`samples/jfk.flac` is enough for the app and the test suite. The benchmark in
[BENCHMARK.md](BENCHMARK.md) also used three LibriSpeech clips from
`hf-internal-testing/librispeech_asr_dummy` (`clean`, `validation`, rows 0-2);
`fixtures.json` records their IDs, dataset revision and reference
transcripts. Their download links are signed and expire, so they aren't
fetched automatically. The repeated-JFK, silence and quiet-noise clips used
there were generated locally as edge cases.

## 5. Desktop integration

```sh
/usr/bin/python3 desktop_install.py            # app launcher entry and icons
/usr/bin/python3 desktop_install.py --shortcut # also Super+R and the GNOME Shell extension
```

Everything is installed in your home directory; nothing needs sudo. The
extension is copied to `~/.local/share/gnome-shell/extensions/` and enabled
for your next login. Run the command again after pulling changes to the
extension or the icons. Nothing starts automatically at login.

## What stays out of Git

`models/`, `samples/`, `recordings/`, `results/`, `.venv/` and `.env*` are
ignored. The app keeps your drafts in `~/.local/share/dogspeak` and its log in
`~/.local/state/dogspeak`, outside the repository. Don't commit recordings,
databases or transcripts.

## Licenses

Dogspeak's own code is MIT-licensed ([LICENSE](LICENSE)). The model and its
derivatives are CC-BY-4.0; `vendor/NOTICE` and `vendor/UPSTREAM_README.md`
carry the attribution, and `vendor/bark_loader.py` is the upstream loader,
unchanged. No new license is asserted over the upstream authors' work.
