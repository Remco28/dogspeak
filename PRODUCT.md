# Dogspeak — product specification

## Direction: an editor-first prompt design lab
Dogspeak starts as a small local dictation scratchpad for composing prompts
for coding agents. The editable document is the main surface. A future optional
suggestions/conversation panel may help refine it; chat history is not the
primary artifact. Keep the normal interface single-pane; at most two panes
when a future workshop panel is open. Dogspeak is not a Spokenly clone.

The user dictates a paragraph, reviews/edits, and copies it elsewhere. They
confirmed Bark Clean removes their hesitations and works for their speech;
roughly two seconds of post-recording latency is acceptable. Prioritize useful
text, reliable capture, editing and preservation over inference optimization.
A word substitution was observed; do not assume all technical terms or
self-corrections are reliable from two samples. Personal sample text is not
part of the public documentation.

## Approved first working scratchpad
- GTK4/libadwaita Python desktop application on this GNOME/Wayland ThinkPad.
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
- Preserve source ASR text, audio association and content revisions locally.
- Keep recordings/drafts/database/private transcripts out of the public repo.
- Stop/finalize capture and reap child processes on close/cancel/error.
- Bound capture below the model harness's 120-second cap; no silent truncation.

App-local shortcuts work without desktop configuration. A separate global
record toggle must not take over Spokenly's existing hotkey. Prefer a clear
opt-in desktop shortcut to permission workarounds or global input injection.
No automatic start-at-login or permanent model service in this version.

## Approved architecture
- System Python + distro GTK4/libadwaita/PyGObject for the frontend.
- Existing project-local Python 3.12 CPU/PyTorch environment for inference.
- A long-lived private JSON-lines worker over pipes; no HTTP server needed.
- SQLite for drafts, untouched ASR segments, revisions and local settings.
- Use XDG user data directories, not source-tree files, for GUI user data.
- Small module interfaces, not a plugin framework, vector database or agent
  orchestration platform. Keep the existing CLI/benchmark usable.

See ARCHITECTURE.md for contracts and behavior invariants.

## Future workshop — recorded decisions, not v1 implementation
Three distinct explicit operations:
1. **Clean up:** grammar, punctuation, flow; preserve intent and identifiers.
2. **Combine/structure:** collate selected prompts/notes, remove duplication,
   organize requirements; surface conflicts instead of resolving them silently.
3. **Develop/critique:** identify omissions/ambiguity, ask questions, accept
   dictated answers and iteratively construct a more effective prompt.

“Better” means clear intent, relevant context, explicit constraints, concrete
outputs and testable success criteria—not longer or more ornate prose.
Keep sources and revision provenance. LLM output is a reviewable proposal;
never silently overwrite the working draft, especially after newer edits.
Untouched Bark ASR output is already disfluency-cleaned, not a verbatim record.
A future provider adapter may use hosted APIs or a local endpoint. Network
transfer of selected text must be explicit and visible. No cloud ASR, LLM
requests, provider keys, or coaching UI in the initial scratchpad.

## Verification gate
Exercise the actual native app, not just a mockup: editing/copy, autosave/reopen,
real ASR through its worker, origin-draft routing and edits during inference,
error handling, recording lifecycle and child cleanup. Use public speech or
an explicitly selected test source for automation; don't capture ambient user
speech without consent. Unit mocks prove logic, not microphone intelligibility.

## Target machine (observed)
ThinkPad X1 Yoga Gen 6; i7-1185G7 (4 cores / 8 threads); ~31 GiB RAM;
Intel Iris Xe; Ubuntu GNOME Wayland. Initial model CPU only; no new hardware.
