1. **Fix 1: STILL THERE — partially fixed. HIGH.**  
   The first quit now refuses and displays the transcript when `append_transcript` fails. But it removes the held result and clears its pending job. The message promises: “retry saving or copy the text before discarding.” ([Code](/home/frank/.cache/dogspeak-review/r4/after/dogspeak_app/gui.py:602))

   **Reproduced:** dictate “Irreplaceable spoken words” while typing; let the result wait; make transcript saving fail with `disk full`; quit. The app refuses, but both `held` and `pending` become empty. Restore saving and quit again: it closes successfully. Reopening the database shows only the original draft—the transcript was silently lost. This makes the offered retry unsafe.

   There is also a gap in displaying the words: if unsaved typing makes `flush()` fail too, the message is only “Could not save draft: disk full…” because `if unsaved and not save_error:` suppresses the transcript message. I reproduced that through the real controller as well.

2. **Fix 2: GONE.**  
   The endpoint now uses `return buffer.get_end_iter()` when it falls beyond the final line. ([Code](/home/frank/.cache/dogspeak-review/r4/after/dogspeak_app/gui.py:858))

   **Checked with a real GTK text buffer:** select `a\nb\n` in `a\nb\n\nc`, move down, then up. At the end, the selection contains `"a\nb"`; moving back restores `"a\nb\n\nc"` exactly.

3. **Fix 3: GONE.**  
   The guard `if new != lines:` skips text replacement for identical neighbours while still updating the cursor. ([Code](/home/frank/.cache/dogspeak-review/r4/after/dogspeak_app/gui.py:843))

   **Checked with a real GTK text buffer:** start on the first line of `a\na\nb`; move down twice. The first move advances the cursor to line 1; the second produces `"a\nb\na"` without an exception.

**Unintended worsening:** I found no separate regression introduced by this patch. Fix 1’s recovery remains incomplete; fixes 2 and 3 showed no regression in the checks performed.

**Declined finding: the author’s reason holds.** The rule is explicitly window activity: `self.get_visible() and self.is_active()`. ([Code](/home/frank/.cache/dogspeak-review/r4/after/dogspeak_app/gui.py:1174)) Focusing the title or side panel preserves the editor’s insertion position. In a scratchpad designed to dictate at that cursor, this is consistent behavior. Title focus alone does not establish that insertion should switch to the end; I would withdraw that finding.

Validation: 24 controller tests passed, along with the focused reproductions and 6,408 GTK buffer movement checks. No window tests ran, and no source files were changed.
