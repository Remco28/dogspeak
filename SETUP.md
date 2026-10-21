# Reproducing the Dogspeak engine experiment

The working environment and model are already installed in this checkout.
Nothing was installed system-wide; no service, login startup entry, global
hotkey, or Spokenly setting was changed. Public source does not include the local environment, model weights, recordings, or personal transcripts.

## Environment

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
  excluded from Git. Do not commit your microphone recordings or transcripts.
- Recording is opt-in. No microphone was opened for the automated benchmark.
- Model and processor assets are fetched during setup. Inference uses local
  assets; `run.sh` enables Hugging Face/Transformers offline mode.
- The original model and derivatives are CC-BY-4.0. See `vendor/NOTICE` and
  `vendor/UPSTREAM_README.md`. The vendored loader is unchanged; no new license
  is asserted over its authors' work. No license for new Dogspeak application
  code has been selected yet.
