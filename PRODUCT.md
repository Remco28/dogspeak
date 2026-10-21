# Dogspeak

## Purpose
A small Linux dictation scratchpad for composing prompts for coding agents.
The user normally dictates into an editor, reviews/edits, and copies text elsewhere.
Dogspeak is not a recreation of Spokenly.

## Current scope: engine feasibility only
- Run ConwayResearch/Underdog-Bark-3.0-Clean locally on this ThinkPad.
- Measure load time, first and warm transcription latency, and memory.
- Test real public speech, longer clips, silence, stereo/resampling, and errors.
- Provide an explicit, opt-in microphone test using the user's own speech.
- Keep Spokenly, desktop hotkeys, and system services unchanged.
- Keep recordings and raw result/transcript files out of Git.
- Stop before building a GUI; assess findings with the user first.

## Potential next phase, only after approval
- One editable scratchpad with append-at-end transcription.
- A global hotkey toggles recording; no need for live streaming.
- Obvious recording/transcribing/ready/error state.
- Ordinary editing and copying to another application.
- No system-wide typing/pasting requirement, cloud ASR, extra LLM, account,
  automatic sending/execution, or feature-rich dictation platform.
- Preserve existing text on failure. Default to a working microphone, not an
  assumption that Bluetooth microphone problems are fixed by a new model.

## Acceptance gate
Actual intelligible speech + useful output, not merely successful model load
or microphone level readings. Public samples establish feasibility only;
the user's natural speech, technical names, negations and self-corrections
must be judged before calling this a satisfactory Spokenly replacement.

## Target machine (observed during setup)
ThinkPad X1 Yoga Gen 6; i7-1185G7 (4 cores / 8 threads); ~31 GiB RAM;
Intel Iris Xe; Ubuntu GNOME Wayland. Use the CPU runtime initially.
