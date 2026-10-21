# Dogspeak architecture and decisions

## Product direction
Dogspeak is an editor-first **prompt design lab**, beginning with a useful local
voice scratchpad. The editable draft is the main surface, not a chat history.
An optional suggestions panel may sit beside the draft;
keep the application focused, normally single-pane, never more than two panes.

Clean up is a single explicit operation with an optional purpose,
replace-with-revert history and a read-only suggestions panel; see PRODUCT.md
"Clean up". It must not invent facts or change identifiers/negations. Preserve
original ASR output (already disfluency-cleaned, not verbatim). Text leaves the
machine only when Clean up is pressed; local dictation stays local.

## First scratchpad
Python GTK4/libadwaita UI; existing Python3.12 CPU model in a separate worker
process; SQLite draft/segment storage. The GTK frontend runs under
system Python with distro PyGObject, avoiding a new native binding build in
the inference environment. Communicate over private stdin/stdout JSON lines,
not HTTP. The UI never imports torch or runs inference in its event loop.

Single editable draft, new/reopen drafts, title, autosave, record/stop,
clear status/timer/error, copy all, native undo/redo, microphone selection.
Transcriptions append a paragraph to the originating draft without replacing
newer typing or leaking into a different selected draft. Keep ASR text; delete
finished or cancelled audio. Model load once per application session. Closing
the window hides it and keeps the worker; Quit stops the worker. No automatic
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
- list_segments(draft_id) -> list[dict]; list_revisions(draft_id) -> [] (no
  content version history).
- forget_audio(request_id) clears the stored path after a finished job.
- delete_draft(draft_id) -> list[str] removes a draft, its segments and
  versions, returning audio paths still on disk; only on explicit request.
- add_version(draft_id, content, label, suggestions=()) keeps the newest 20
  per draft; list_versions(draft_id) oldest first with undismissed
  suggestions as (index, text); dismiss_suggestion(version_id, index).
- get_setting(key, default=None), set_setting(key,value), close().
- Parameterized SQL, transactions, foreign keys, private permissions, no cloud.

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
Keyboard Super+R (and Ctrl+Shift+R) toggles recording within app, Ctrl+N new draft,
Ctrl+Shift+C copy all, Enter stops an active recording, Escape cancels
recording or drops an in-flight transcript. Ctrl+Q quits and unloads the
model. Closing the window hides it. Expose application actions
toggle-recording, import-audio(s), quit, and show-window. `--toggle-recording`
uses single-instance activation without presenting the editor, so a global
shortcut can record while another app is focused. Never defer an initial
toggle until model ready (no surprise delayed capture). Parent owns optional
shortcut integration. Backend events for pending requests map to origin draft
IDs; persist latest edits before applying result. Render append as Gtk buffer
insertion, not a full replacement that destroys editing/undo state. Keep
editing available while engine loads/transcribes; disable duplicate
recording/jobs.

### overlay (gnome-extension/dogspeak-overlay@remco28.github.io)
GNOME Wayland will not keep a client window above the focused one, so the
status pill is drawn by a Shell extension (top chrome, bottom-centre of the
focused monitor, no input). It owns `io.github.Remco28.Dogspeak.Overlay` at
`/io/github/Remco28/Dogspeak/Overlay` on the session bus: Show(state, title,
hint), Hide(), SetKeys(as: 'stop' = Return/KP_Enter, 'cancel' = Escape),
SetClipboard(s), property Version, signal KeyPressed(s). It also adds the
top-bar menu (PanelMenu.Button 'dogspeak-tray'), shown while the
`io.github.Remco28.Dogspeak` bus name exists; its items call the app's
GApplication actions through org.gtk.Actions.Activate (show-window,
toggle-recording, new-draft, copy-all, quit). Copy all from the menu goes back
through SetClipboard because the window is unfocused then. The GUI's
`ShellOverlay` watches the name and falls back to Gio notifications when it
is absent. Key grabs exist only while Recording ('stop','cancel') or
Transcribing ('cancel'); the extension drops them and hides the pill when the
calling bus name vanishes, and after 10 minutes regardless. Only recordings
started while the editor is unfocused are copied, via SetClipboard, because an
unfocused Wayland client cannot set the selection. The GUI tests run on a
private bus (`dbus-run-session` in test.sh) so they never drive the real pill.

### llm.py / credentials.py / cleanup.py / cleanup_ui.py
- `llm.Client(base_url, api_key, model)`: stdlib urllib, OpenAI-style
  `/chat/completions`. `clean_up(text, purpose) -> (text, suggestions)`; the
  model is asked for one JSON object `{"text", "suggestions"}` and the draft is
  fenced as data, never instructions. `test()` uses only free endpoints
  (OpenRouter `/key`, `/credits`; everyone `/models`). Failures raise
  `LLMError` with a sentence fit for the banner. `PROVIDERS` holds presets.
- `claude.py` (venv only): Claude via the official `anthropic` SDK, one
  subprocess per request (`.venv/bin/python -m dogspeak_app.claude`), JSON on
  stdin/stdout; the key goes over stdin, never argv. Structured output
  (`output_config.format` json_schema), effort `low`, `fallbacks: "default"`
  on models that accept it; `test` only calls `models.retrieve`.
  `llm.make_client` picks `ClaudeClient` for `https://api.anthropic.com`.
- `credentials.Keyring`: libsecret (Secret Service) keyed by base URL. Called
  off the UI thread for Clean up; a local provider works without a keyring.
- `cleanup.Cleanup(controller, credentials, client_factory)`: GTK-free. One
  request at a time; the provider call runs via `spawn`, returns via
  `dispatch`. Applies a result only if the origin draft still starts with what
  was sent (a transcript appended meanwhile is kept after the result). Versions
  (SQLite `versions`, newest 20 per draft): "Original"/"Before clean up" before,
  "Clean up · <purpose>" with suggestions after, "Your edits" when jumping
  away from unsaved hand edits. Settings: `purpose`, `recent_purposes` (5,
  presets excluded), `llm_provider`, `llm_base_url`, `llm_model`,
  `suggestions_panel`. Registers `controller.locked_drafts` so a draft being
  cleaned can't be deleted.
- `cleanup_ui.CleanupView`: purpose bar, Adw.Banner (setup / failure),
  suggestions Revealer, Settings (Adw.PreferencesDialog). The window replaces
  text with one buffer user action, so Ctrl+Z restores it. App actions
  `clean-up` (Ctrl+Shift+L) and `settings` (Ctrl+,).

### launch
`app.sh` launches /usr/bin/python3 -m dogspeak_app.gui from project root,
without modifying PYTHONPATH, system packages, or inference environment.
Tests run system Python for UI/storage and venv Python for real ASR. System
GTK4/libadwaita/PyGObject are runtime prerequisites. Preserve run.sh CLI.
