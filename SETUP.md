# Dogspeak setup

The working environment and model are already installed in this checkout.
No model service, login startup entry, or Spokenly setting is required. The
user-level application launcher is installed separately; the global GNOME
shortcut is explicit opt-in. Public source does not include the local
environment, model weights, recordings, databases or personal transcripts.

## Native desktop prerequisites

The GUI uses distro Python and PyGObject; it does not import the ASR packages.
On this Ubuntu machine, `python3-gi`, `gir1.2-gtk-4.0` and `gir1.2-adw-1` were
already installed. PipeWire tools `pw-record` and `pw-dump` provide capture and
microphone discovery. No GTK package was installed or upgraded for this app.
Check a fresh machine before installing its distro equivalents:

```sh
/usr/bin/python3 -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1'); from gi.repository import Gtk, Adw; print(Gtk.get_major_version(), Adw.get_major_version())"
command -v pw-record pw-dump
```

`xvfb-run` is needed only for the headless native test suite. Use the existing
system bindings with `app.sh`; do not add system dist-packages to the model
venv or attempt to run the GUI under the inference interpreter.

## Inference environment

Python 3.12 and uv are required. This machine already had both available.
From the repository root:

```sh
uv sync --frozen
```

`uv.lock` pins the resolved packages. PyTorch is explicitly sourced from the
CPU-only wheel index; CUDA packages are not needed. The upstream model card
omits `librosa`, which the current Transformers Parakeet feature extractor
requires; it is included here. The private `.venv` does not need pip.

## Model and offline processor assets

No Hugging Face account or Underdog invitation is needed for these public files.
The vendored loader and attribution are committed; model files are ignored.
Download the exact revisions recorded in `upstream.json`:

```sh
.venv/bin/hf download ConwayResearch/Underdog-Bark-3.0-Clean model.bark \
  --revision 6d34ed8639b2b9a33e764acf06748519bbb950cf --local-dir models

.venv/bin/hf download nvidia/parakeet-tdt-0.6b-v3 \
  config.json generation_config.json processor_config.json tokenizer.json tokenizer_config.json \
  --revision 541d1f99c6b0c3cd0b11a95167540bb8edefd82b --local-dir models/processor

sha256sum models/model.bark
```

Expected model SHA-256:

```
b67ca90d94b79a36af4616f1cb3f241884925de7aa9f3e673a8d5c6c4cfd3436
```

Downloaded model size: 179,458,613 bytes. `upstream.json` also records hashes
for the loader, processor assets, and public speech fixtures. The runtime
loads the processor from disk and does not need the full NVIDIA base weights.

## Public test audio

The initial checkout includes ignored, locally downloaded fixtures. A readily
reproducible speech sample is the JFK excerpt from OpenAI Whisper's tests:

```sh
mkdir -p samples
curl --fail --location --retry 3 \
  https://raw.githubusercontent.com/openai/whisper/86098128c0b4f24f0e2aa2994de830614b474227/tests/jfk.flac \
  --output samples/jfk.flac
```

Three more read-speech clips were obtained through Hugging Face's dataset
viewer API for `hf-internal-testing/librispeech_asr_dummy`, `clean`,
`validation`, rows 0-2. `fixtures.json` preserves their stable IDs, dataset
revision and reference transcripts; viewer audio links are signed and expire.
The repeated-JFK, silence and quiet-noise clips are explicitly synthetic
edge-case fixtures, not additional speakers or evidence of real dictation
accuracy. They live only under ignored `samples/`.

## Privacy and attribution

- `samples/`, `models/`, `recordings/`, `results/`, `.venv/`, and `.env*` are
  excluded from Git. GUI user data additionally lives in the XDG data directory
  outside the repository. Do not commit recordings, databases or transcripts.
- Recording is opt-in. No microphone was opened for the automated benchmark.
- Model and processor assets are fetched during setup. Inference uses local
  assets; `run.sh` enables Hugging Face/Transformers offline mode.
- The original model and derivatives are CC-BY-4.0. See `vendor/NOTICE` and
  `vendor/UPSTREAM_README.md`. The vendored loader is unchanged; no new license
  is asserted over its authors' work. No license for new Dogspeak application
  code has been selected yet.
