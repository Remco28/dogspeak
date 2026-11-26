Found four problems. No source files changed. All 24 controller tests and 16 storage tests passed; additional headless checks reproduced the failures below using real GTK buffers and controller methods. Full window tests weren’t run.

| Fix | Verdict | Evidence / quote |
|---|---|---|
| 1. Wait for one second without edits; deliver on quit | **REPLACED BY A NEW PROBLEM** | Waiting works: repeated edits kept the result pending for 90 simulated seconds; a one-second pause delivered it. Normal quit also delivered it. However, quit loses it if delivery fails: `“for event in list(self.held.values()): self._event(event)”` doesn’t propagate failure. See finding 1. |
| 2. Move paragraphs/lines; moving back reverses it | **REPLACED BY A NEW PROBLEM** | Basic swaps and paragraph joins work. Selection restoration breaks reversal in a common boundary case, and identical neighbors raise an exception. The promise `“Moving back always undoes a move”` is false. See findings 2–3. |
| 3. Showing a draft puts the cursor at its end | **GONE** | `“if switching: self.buffer.place_cursor(self.buffer.get_end_iter())”`. Ran the renderer against a cached draft with its cursor initially at the start; it finished at the end. |
| 4. Appending preserves an existing selection | **GONE** | `“elif cursor != bound: self.buffer.select_range(...)”`. Ran an append with the whole original draft selected, including its endpoint; the original selection survived. |
| 5. Only editor-focused recordings use the cursor | **STILL THERE**, partially | Imports and recordings started outside the active window now append correctly. But `“self.get_visible() and self.is_active()”` checks window activity, not editor focus. See finding 4. |
| 6. Inside a word, insert after that word | **GONE** | `“if end.inside_word() and not end.starts_word(): end.forward_word_end()”`. Ran with the cursor inside `hello`; insertion resolved to its end. A cursor at the next word’s start stayed there. |

1. **MEDIUM — Quit can silently lose a waiting transcript after a save failure.**  
   Start a cursor recording, receive its result while typing, then quit. If `append_transcript()` fails—for example, because the disk is full—`_event()` catches the error, but `close()` continues, closes the store and deletes the recording. The transcript was never stored.

   **Checked:** ran the controller with an injected append failure. `close()` returned `True`; reopening the database showed only the original text, and the audio file was gone.  
   **Did this fix make it?** Yes, this is a failure in the new waiting-result delivery path, which reuses shutdown’s existing pending-job disposal. See [delivery during close](/home/frank/.cache/dogspeak-review/r3/after/dogspeak_app/gui.py:607).

2. **MEDIUM — Moving a selected paragraph to the document’s end shrinks its selection and breaks reversal.**  
   Start with `a\nb\n\nc`. Select `a\nb\n`, ending at the blank line’s start. Alt+Down produces the correct text, `c\n\na\nb`, but selects only `a\n`. Alt+Up then produces `c\na\n\nb`, rather than restoring the original.

   The translated selection endpoint falls beyond the final line; `“line = max(0, min(line, len(new) - 1))”` clamps it to that line’s **start**.

   **Checked:** ran both moves through the actual window method using a real GtkSource buffer.  
   **Did this fix make it?** Yes; the new selection-position calculation introduces it. See [position restoration](/home/frank/.cache/dogspeak-review/r3/after/dogspeak_app/gui.py:850).

3. **LOW — Moving past identical text fails instead of advancing the cursor or selection.**  
   With `a\na\nb`, put the cursor on the first `a` and press Alt+Down. The text comparison finds no differing line, so `“top = next(... if a != b)”` raises `StopIteration` before cursor restoration. Repeated presses cannot move past the duplicate. Identical adjacent paragraphs behave likewise.

   **Checked:** ran both duplicate-line and duplicate-paragraph cases through the actual method.  
   **Did this fix make it?** Yes; this comparison is new. See [changed-region calculation](/home/frank/.cache/dogspeak-review/r3/after/dogspeak_app/gui.py:838).

4. **MEDIUM — Recording with the title field focused still inserts at the editor’s old cursor.**  
   Leave the editor cursor between two paragraphs, focus the draft title, and start recording with Super+R. The visible, active window makes `focused()` true, so the result goes to the old editor cursor instead of the end.

   **Checked:** read the recording path and ran the actual focus predicate with an active window and an unfocused editor; it returned `True`.  
   **Did this fix make it?** No. The active-window predicate already existed; fix 5 reused it without implementing the stricter editor-focus requirement. See [focus predicate](/home/frank/.cache/dogspeak-review/r3/after/dogspeak_app/gui.py:1166).

There are corresponding coverage gaps:

- The new dictation test helper replaces the real focus predicate with a supplied boolean, hiding finding 4. [Test helper](/home/frank/.cache/dogspeak-review/r3/after/tests/test_gui.py:754)
- Selected-line tests don’t cover moving a newline-ended selection to the document’s end and back. Duplicate text is also untested.
- The quit test covers successful delivery, but not a failed transcript write.
- Architecture documentation promises editor focus and delivery on quit; those claims exceed the behavior above. I found no additional independent documentation mismatch.

Both declined extras remain reasonably declined: cursor movement needn’t restart an **edit** pause, and the word-boundary adjustment protects insertion inside a word. The patch adds neither the status-bar toggle nor the insertion flash.
