# Dictation placement design for Dogspeak

I read `ARCHITECTURE.md`, `gui.py`, `storage.py` and `cleanup.py`. I did not run anything or change any files.

## 1. Design

**Where text goes**

1. **Default rule:** a foreground dictation goes at the editor's caret, read live at the moment the result arrives. The caret is the owner's "I moved it, so insert there" signal. There is no separate "pinned" mode.
2. **Caret placement on open:** when a draft is opened, rendered, cleaned up, or reverted to a version, the caret is placed explicitly at the end. A fresh session therefore keeps today's "goes at the bottom" behavior, and the owner's click is the only thing that changes it. The code shouldn't rely on GTK's default caret position after `set_text`.
3. **Selection:** dictation never replaces a selection. It inserts after the selection's end and collapses it.
4. **Active typing:** if the user pressed a key in the last second or so, hold the insertion until they pause, for at most about 2 seconds. This stops a result from splitting a word they are typing. After the cap it inserts anyway.
5. **Unfocused or background recordings:** a recording started while the window was unfocused (global shortcut, or `--import-audio` from the command line) appends at the end. A stale caret from yesterday's click is not an instruction. The `background` flag already exists on the job, but imports don't set it.
6. **A different draft is open when the result arrives:** the text goes into its origin draft, as the existing contract says. That draft's buffer is edited directly, the view does not switch, and the status says "Added to <title>".
7. **Empty transcript:** store the segment and insert nothing, as today.
8. **Esc and engine errors:** nothing is inserted. No per-job state is needed, because the caret is read live.

**Spacing**

The rule is that dictation is its own block, and the app only ever adds whitespace, never removes or normalizes the user's.

- **Caret on an empty line:** the text goes on that line with no added newlines. This respects the Enter the user typed.
- **Caret at the end of a non-empty line, or at the end of the document:** top up to a blank line before the text, adding only the newlines that are missing (0, 1 or 2). This replaces the blind `"\n\n"` in `append_transcript`, which produces 3 newlines when the user has already pressed Enter.
- **Caret at the start of a non-empty line:** the text goes before that line, with a blank line after it.
- **Caret mid-line:** insert inline, adding a single space on a side only if the neighbor there is a non-space character.
- **Caret in an empty document:** nothing is added.

**Caret and view after insert**

- **Caret:** it moves to the end of the inserted text, like a paste, and this holds even for background appends. The next chunk then continues right after the last one. This replaces the existing "restore the old selection" code.
- **Scrolling:** scroll just enough to show the end of the inserted text. If the window is hidden, do the scroll when it is next presented, because a hidden view has no allocation.
- **Highlight:** optionally flash a non-undoable tag on the new text for about 1.5 seconds.
- **Overlay and status:** the message becomes "Added at line N".
- **Undo:** the insertion is a single user action, so Ctrl+Z removes the whole chunk.

**Line numbers**

- GTK4's `TextView` has no gutter, and GtkSourceView would add a distro dependency that the architecture avoids.
- Subclass `Gtk.TextView` and draw numbers in `snapshot_layer` (below-text layer) inside a widened left margin. Number logical lines, so wrapped continuations get none.
- Highlight the caret's line, since that is where dictation will land.
- The numbers are drawn, never inserted into the text, so Copy all and Clean up stay plain.
- Put an on/off toggle in settings.

**Reordering**

- Use keyboard-only Alt+Up and Alt+Down. They move the selected lines, or with no selection the current paragraph (a run of non-blank lines), past the neighboring one, keeping the blank separators.
- The move is one buffer user action (undoable) and the caret follows the moved text.
- Skip drag-and-drop. It is not clean in a `TextView`.

## 2. What has to change in the code

The central change is that the position and spacing decision needs the buffer text, which only the UI has. Today `storage.append_transcript` decides both against the database copy.

**Storage and persistence**

1. **`append_transcript`:** it currently appends in SQL and promises that the old content is a prefix. Replace it with something like `record_transcript(draft_id, request_id, path, text, base, new_content)`.
   - In one transaction it checks that the stored content equals `base` (compare-and-set), writes the segment, and saves `new_content`.
   - It stays idempotent on `request_id`.
   - The invariant "new content with the inserted block removed equals base" is cheap to assert and is the guarantee that no edit is lost.
2. **Insert before persisting:** the controller currently persists first and the UI renders second.
   - Reverse that: insert into the buffer, then persist.
   - Today, if the store write raises, the result stays in `pending` and nothing retries it, so the ASR text exists only in the event and is lost.
   - With the text in the buffer and the draft marked dirty, autosave retries and the user can copy it.

**Rendering**

3. **`render_draft` append path:** it assumes `draft.content.startswith(old buffer)` and inserts the suffix. This is the one most likely to be missed.
   - It also serves as the lazy catch-up for hidden drafts through the cached `self.buffers`.
   - With mid-text insertion that check fails, raising "Draft changed outside the editor; reopen it to reconcile". Reopening goes through the same forced-append path, so it raises again and the draft becomes unopenable.
   - A result for a non-displayed draft must instead be applied to that draft's cached buffer immediately. The fallback is to drop the cached buffer, which loses its undo stack but not any text.
   - The controller currently updates only the displayed draft, and `flush` saves only `self.draft`, so a hidden draft's save needs its own path.
4. **Caret after replacement:** `replace_text` (Clean up, version jump) and `set_text` leave the caret wherever GTK puts it. That decides where the next dictation lands, so set the caret explicitly to the end.

**Clean up**

5. **`Cleanup._apply`:** it requires `current.startswith(sent)` and keeps any transcript appended meanwhile after the result. That only holds for end-appends.
   - Recording is not blocked while Clean up is busy, because `toggle_recording` never checks `cleanup.busy`. A mid-text dictation would make the check fail and discard a paid-for result.
   - Make the two mutually exclusive: refuse to start recording while Clean up runs. `can_start` already blocks the other direction.
   - This reverses a documented behavior, so `ARCHITECTURE.md` needs the update.

**Smaller interactions**

6. **Import path:** `import_audio` jobs have no `background` key. Decide each import's mode explicitly, since the `--import-audio` path is typically unfocused.
7. **Suppressed `_changed`:** the `rendering` flag suppresses `_changed` during the insertion, so `controller.content` and the draft must be updated by hand from the saved result, as the code does now. The autosave timer is not re-armed in that case.
8. **Docs:** `ARCHITECTURE.md` ("append a paragraph", "Render append as insertion", the `append_transcript` contract) and `README.md` need updating.

**Simple parts**

- Line numbers and Alt+Up/Down are self-contained UI work. They interact with nothing else beyond undo grouping.
- The caret and scroll change is small once the position rule exists.

## 3. Decisions for the owner

1. **Mid-line cursor: inline or its own paragraph?** I recommend inline (spaces only), because paragraph-splitting a sentence you clicked into is surprising. The other cases keep paragraph behavior.
2. **Background recordings: end or caret?** I recommend the end. This is the place the "caret = intent" rule is weakest.
3. **Dictating while Clean up runs:** I recommend refusing, with a message. The alternative, queueing the transcript until Clean up finishes, is possible but adds machinery.
4. **When the position is decided:** I recommend the live caret at result time (with the typing pause). The alternative is to freeze the caret when recording stops, which is more predictable but ignores a click made during transcription.
5. **Line numbers:** I recommend numbering true lines, including blanks, on by default with a toggle. With paragraph spacing, half the numbers will be blank lines, so the owner may prefer to number only non-blank lines.
6. **Reordering:** I recommend keyboard-only, shipped after the placement work. The owner said to skip if it isn't clean, and drag isn't clean.
7. **Does the view follow the dictation?** I recommend yes, always scrolling to the inserted text. This directly answers the original "disorienting" complaint.
