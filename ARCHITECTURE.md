# Dogspeak architecture and decisions

## Product direction
Dogspeak is an editor-first **prompt design lab**, beginning with a useful local
voice scratchpad. The editable draft is the main surface, not a chat history.
A future optional suggestions/conversation panel may help develop the draft;
keep the application focused, normally single-pane, never more than two panes.

The accepted future operations are distinct:
1. Clean up grammar and flow without changing meaning.
2. Combine selected notes/drafts, deduplicate and structure; flag conflicts.
3. Critique a prompt, ask clarifying questions, and iterate toward explicit
   intent, context, constraints, deliverables, and success criteria.

An LLM proposal must not silently overwrite the draft, resolve contradictory
requirements, invent facts, or change identifiers/negations. Preserve original
ASR output (already disfluency-cleaned, not verbatim), edits, provenance, and
revisions. Hosted LLM text transfer is explicit; local dictation stays local.
No LLM calls, keys, cloud endpoints, plugin framework, or coaching UI in v1.

## First scratchpad
Python GTK4/libadwaita UI; existing Python3.12 CPU model in a separate worker
process; SQLite draft/segment/revision storage. The GTK frontend runs under
system Python with distro PyGObject, avoiding a new native binding build in
the inference environment. Communicate over private stdin/stdout JSON lines,
not HTTP. The UI never imports torch or runs inference in its event loop.

Single editable draft, new/reopen drafts, title, autosave, record/stop,
clear status/timer/error, copy all, native undo/redo, microphone selection.
Transcriptions append a paragraph to the originating draft without replacing
newer typing or leaking into a different selected draft. Preserve audio and
ASR text locally. Model load once per application session. No automatic
sending, system-wide text insertion, or auto-start at login.

Use XDG_DATA_HOME/dogspeak (normally ~/.local/share/dogspeak), overridable with
DOGSPEAK_DATA_DIR for tests. Restrict directory/database/recording permissions.
No private data under source or in Git. Existing CLI remains supported.

## Implementation contracts
These small interfaces keep UI, persistence, capture and engine replaceable.

### storage.py (stdlib only)
- `Draft` dataclass: id, title, content, updated_at (strings).
- `Store(path)` SQLite connection, called on UI thread only.
- create_draft(title='Untitled') -> Draft; list_drafts() -> list[Draft];
  get_draft(draft_id) -> Draft; save_draft(draft_id, content, title=None) -> Draft.
- append_transcript(draft_id, request_id, audio_path, text) -> Draft atomically
  stores untouched ASR segment and appends text once (request_id idempotent).
  Preserve existing content exactly as prefix. Add blank-line separation;
  empty transcript is stored but does not add blank paragraphs.
- list_segments(draft_id) -> list[dict]; list_revisions(draft_id) -> list[dict].
- get_setting(key, default=None), set_setting(key,value), close().
- Record revisions for changed content, not identical autosaves. Parameterized
  SQL, transactions, foreign keys, private permissions, no cloud.

### audio.py / engine.py / worker.py (stdlib UI side)
- `Recorder.start(path:Path, target:str|None=None)`; stop()->Path; cancel();
  property running. Finalize/reap pw-record using SIGINT with bounded kill
  fallback. Capture only when explicitly started. Target is stable node.name.
- `list_sources()` -> list[dict] with name and description from pw-dump.
- UI automatically stops recording at 115 seconds, below the engine's 120s
  cap; record/stop/errors/cancel must not leave a microphone process behind.
- `EngineClient(callback, root=ROOT, log_path=None)`: start();
  transcribe(path,request_id); close(). Callbacks run on reader thread; UI
  dispatches to GLib.idle_add. Keep heavyweight imports inside venv worker.
- Worker invoked `.venv/bin/python -u -m dogspeak_app.worker`. Enable offline
  mode, redirect library chatter to stderr, reserve stdout for JSON lines.
- Messages: {type:'loading'}, {type:'ready',load_seconds,pid},
  {type:'result',id,path,text,seconds},
  {type:'error',id,message,fatal}. Unexpected child death emits fatal error.
- Requests: {action:'transcribe',id,path}; {action:'shutdown'}.
- Worker loads Pipeline once, handles per-file errors without losing it;
  stop worker on GUI shutdown, close pipes and no orphan model process.

### gui.py
Gio/Adw single-instance application id io.github.Remco28.Dogspeak. Gtk.TextView
with undo, word wrap, generous margins, readable text; no webview. Toolbar
new/open/copy/record and title, subtle status bar/microphone selector.
Keyboard Ctrl+Shift+R toggles recording within app, Ctrl+N new draft,
Ctrl+Shift+C copy all. Expose application actions toggle-recording and
import-audio(s) for desktop shortcut and integration tests. `--toggle-recording`
uses single-instance activation; never defer an initial toggle until model
ready (no surprise delayed capture). Parent owns optional shortcut integration.
Backend events for pending requests map to origin draft IDs; persist latest
edits before applying result. Render append as Gtk buffer insertion, not a
full replacement that destroys editing/undo state. Keep editing available
while engine loads/transcribes; disable duplicate recording/jobs.

### launch
`app.sh` launches /usr/bin/python3 -m dogspeak_app.gui from project root,
without modifying PYTHONPATH, system packages, or inference environment.
Tests run system Python for UI/storage and venv Python for real ASR. System
GTK4/libadwaita/PyGObject are runtime prerequisites. Preserve run.sh CLI.
