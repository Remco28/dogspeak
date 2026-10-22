# Dogspeak — product specification

This records what Dogspeak is meant to be and the decisions behind it, for
anyone changing it. For using it, see the README.

## Direction: an editor-first prompt design lab
Dogspeak is a local dictation scratchpad for composing prompts for coding
agents (and other text). The editable document is the main surface; a
suggestions panel may sit beside it; chat history is not the primary artifact.
Keep the normal interface single-pane, at most two panes.

You dictate a paragraph, review and edit it, and copy it elsewhere. Bark Clean
removes hesitations, and roughly two seconds of post-recording latency is
acceptable for this. Prioritize useful text, reliable capture, editing and
preservation over inference optimization. Occasional word substitutions
happen; don't assume technical terms or self-corrections are always right.
Personal recordings and transcripts are never part of the public repository.

## First working scratchpad
- GTK4/libadwaita Python desktop application for GNOME (developed on Wayland).
- One editable main document with normal selection, undo/redo and copy.
- Draft title, new draft, reopen prior drafts, durable local autosave.
- Explicit record/stop, keyboard toggle, readable recording elapsed time.
- Obvious Loading / Ready / Recording / Transcribing / Error state.
- Microphone selection including system default; never record before an
  explicit user action or queue surprise capture during model loading.
- Whole-recording transcription; append a new paragraph without destroying
  edits made while inference runs or sending text into a different draft.
- Separate inference process, keeping the model resident while the app runs.
- Copy all for transfer to a coding agent; nothing auto-pastes or auto-sends.
- Import existing audio for retrying recordings and testing the complete app.
- Preserve source ASR text locally. Delete capture audio after a successful
  transcript or a cancel. Native editor undo is enough; do not keep content
  version history.
- Keep recordings/drafts/database/private transcripts out of the public repo.
- Stop/finalize capture and reap child processes on close/cancel/error.
- Bound capture below the model harness's 120-second cap; no silent truncation.

Status while dictating from another app is an always-on-top pill drawn by a
small GNOME Shell extension (opt-in, installed with the global shortcut);
Enter/Esc work from any app only while it shows Recording/Transcribing.
Recordings made from another app are also copied to the clipboard; nothing is
pasted automatically.

App-local shortcuts work without desktop configuration. The global Super+R
record toggle is opt-in and must never take over another app's shortcut.
Closing the window hides the editor and keeps the model loaded; Quit unloads
it. Prefer a clear opt-in desktop shortcut to permission workarounds or
global input injection. No automatic start-at-login or permanent model
service in this version.

## Architecture
- System Python + distro GTK4/libadwaita/PyGObject for the frontend.
- Existing project-local Python 3.12 CPU/PyTorch environment for inference.
- A long-lived private JSON-lines worker over pipes; no HTTP server needed.
- SQLite for drafts, untouched ASR segments and local settings.
- Use XDG user data directories, not source-tree files, for GUI user data.
- Small module interfaces, not a plugin framework, vector database or agent
  orchestration platform. Keep the existing CLI/benchmark usable.

See ARCHITECTURE.md for contracts and behavior invariants.

## Clean up
(Agreed 2026-10-21; supersedes an earlier plan for separate clean up /
combine / critique operations, which was dropped.)

Goal: you speak freely, rambling and repeating themselves, without
worrying about wording. One button turns that into coherent text: removes
duplicated points, fixes grammar and flow, keeps names, identifiers and
negations, invents nothing. The real aim is prompts clear enough that coding
agents stop doing things the wrong way because the instructions were unclear.

**Clean up button.** Sends the current draft (and the purpose, if any) to the
configured model in one request. The result replaces the draft text. Only
this button sends text off the machine; dictation stays local.

**Purpose (optional).** A field above the editor saying what the text is for.
Blank means general purpose: clean it up, no reformatting. Its dropdown offers
presets (Prompt for an AI agent, Essay, Email, Notes) plus the last few
purposes you typed. It keeps the last purpose used across drafts and can
be cleared. A prompt purpose gets instruction-like structure (goal, context,
constraints, what counts as done); an essay gets essay structure, and so on.

**Revert and history.** Before each clean up the current text is saved as a
version: the last 20 per draft, newest first. Revert steps back one version;
a version menu jumps to any of them. Ctrl+Z also undoes a clean up
immediately. This replaces the earlier no-version-history rule for model
rewrites only; typing is still covered by editor undo.

**Suggestions panel.** Returned by the same request as the cleaned text. A
read-only list in a side panel (the second pane), for example parameters to
add to a prompt or topics an essay should cover. Each item can be dismissed;
clicking does not send another request. The panel can be hidden.

**Model and provider.** The OpenAI-style Chat Completions API covers most
providers: Settings take a base URL, an API key and a model name, with presets
that fill in the URL; OpenRouter is the default (one key, many cheap models).
Claude is supported through Anthropic's official SDK. A small, inexpensive
model is usually enough. The key is stored in the GNOME keyring
(Secret Service), never in SQLite or the source tree. A Test connection button
checks the key and the model name without spending tokens (OpenRouter
/api/v1/key and the models list; elsewhere the models list).

## Verification gate
Exercise the actual native app, not just a mockup: editing/copy, autosave/reopen,
real ASR through its worker, origin-draft routing and edits during inference,
error handling, recording lifecycle and child cleanup. Use public speech or
an explicitly selected test source for automation; never capture someone's
ambient speech without consent. Unit mocks prove logic, not microphone intelligibility.

## Development machine
ThinkPad X1 Yoga Gen 6; i7-1185G7 (4 cores / 8 threads); ~31 GiB RAM;
Intel Iris Xe; Ubuntu GNOME Wayland. The model runs on the CPU only; no GPU
is assumed.
