# CPU feasibility: Bark 3.0 Clean

## Verdict: PARTIAL — engine validated, personal dictation not yet validated

Actual inference ran on the ThinkPad; there is no fabricated output in this
report. The model is a viable candidate for a local scratchpad. Whether its
cleanup and technical vocabulary are good for the user remains untested.
No GUI or global hotkey integration has been built.

## Environment

Observed 2026-10-21: ThinkPad X1 Yoga Gen 6, Intel i7-1185G7 (4 cores / 8
threads), about 31 GiB RAM, Ubuntu GNOME Wayland. CPU execution only, FP32
weights, four intra-op threads and one inter-op thread. Python 3.12.14,
PyTorch 2.14.1+cpu, Transformers 5.20.2. Exact dependencies are in `uv.lock`;
model/processor revisions and SHA-256 checksums are in `upstream.json`.
Timings are a small desktop feasibility sample, not controlled lab results.
Other workloads, laptop power settings and temperature can change latency.

## Initial direct-loader run

The model and cached processor were loaded once, then each clip was run twice.
Times below are the second inference of each clip, excluding model load.
Input preprocessing and decoding are included; audio file I/O is not.

| Audio fixture | Audio duration | Warm processing time | Observation |
|---|---:|---:|---|
| LibriSpeech 0000 | 5.855 s | 1.18 s | Correct words; “Mr.” abbreviation |
| LibriSpeech 0001 | 4.815 s | 0.90 s | Expected sentence |
| LibriSpeech 0002 | 12.485 s | 1.98 s | Expected sentence |
| JFK excerpt | 11 s | 1.52 s | Expected sentence |
| JFK excerpt repeated three times | 33 s | 4.03 s | All three repetitions preserved |
| Digital silence | 5 s | 1.24 s | Empty transcript |
| Seeded quiet Gaussian noise | 5 s | 0.94 s | Empty transcript |

There are four distinct public speech clips, not seven speakers/tests of
natural dictation. The repeated speech is a synthetic length test; the noise
fixture uses seed 42 and standard deviation 0.002. No claim is made about
broad word error rate, filler removal, accents, real room noise, or code names.
All public fixture references and identities are in `fixtures.json`.

Internet socket connections were explicitly denied within the benchmark
process using Python's audit hook. Inference completed using only the cached
weights and processor files. This does not modify the desktop network.

Raw output: [`docs/upstream-smoke.json`](docs/upstream-smoke.json).
`startup_seconds` ends after model/processor load; `model_load_seconds` starts
after Python imports. Initial model load was 32.55 s, including 23.28 s to
unpack the compressed weights. First-use preprocessing/JIT may add overhead.

## Loading without a duplicate model allocation

The upstream-style constructor initializes a separate FP32 model and copies
the decoded tensors into it. Even after the decoded state dictionary was
released, that run held about 5.4 GiB resident.

A second real probe used Transformers' `no_init_weights()` context and
PyTorch's `load_state_dict(assign=True)`, then discarded the decoded mapping.
This skips initialization of unused weights and assigns the decoded tensors
rather than making a second copy. It does NOT change precision, quantize
again, or modify the vendored decoder.

- Load: 23.87 s (mostly unpacking).
- Resident memory after load: 3,101.7 MiB.
- Resident memory during these clips: about 3.1 GiB.
- Process peak resident memory: 3,374.5 MiB, about 3.30 GiB.
- The short LibriSpeech, JFK, and repeated-JFK transcripts matched the initial
  run byte-for-byte across two repeats each.
- Warm latency varied: about 1.07 s for 5.855 s speech, 1.21 s for 11 s speech,
  and 4.99 s for the repeated 33 s clip. These are separate desktop runs,
  not evidence that this loading change increases or decreases inference speed.

Raw output: [`docs/assign-probe.json`](docs/assign-probe.json).
The supplied vendor loader remains unchanged.

## Thread sampling

In the initial run, the 11-second JFK clip took approximately 2.84-3.05 s
with one CPU thread and 1.71-1.80 s with two. Four threads gave 1.52 s on its
second run. These were sequential small samples, not a randomized tuning
study. Four threads is a sensible initial default, not a proven universal
optimum. The CLI exposes the thread count for further testing.

## What remains

1. Use `./run.sh listen` with the user's real working microphone. This command
   is opt-in; no microphone was opened during the public-audio benchmarks.
2. Judge cleanup, technical names/paths and self-corrections. Suggested real
   speech: an ordinary short prompt, a rambling longer instruction, and a
   correction/negation such as “change the parser, actually do not change the
   parser; only update the test.” Do not feed synthetic TTS as proof of this.
3. Compare the usefulness and waiting time with normal Spokenly use. No
   apples-to-apples Spokenly active-inference benchmark has been performed.
4. Build the editable scratchpad only if the user judges the result worthwhile.

The one-time load argues for keeping the model resident, not reloading per
utterance. Caching unpacked weights or a CPU-optimized model export could be
investigated later, but neither is required to answer the first feasibility
question and neither has been assumed to work.
