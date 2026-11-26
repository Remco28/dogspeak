1. **REPLACED BY A NEW PROBLEM.** The original loss is fixed for the current draft. The fallback sets `self.dirty = True` and renders the transcript into the editor. Headless checks confirmed that it saves successfully, or survives a refused close and saves exactly once on retry.

2. **MEDIUM — fallback can save the transcript into the wrong draft.** Dictate in draft A while typing, switch to draft B, then quit before the typing-pause timer delivers the held result. If saving the transcript fails, [`_keep_as_edit()`](/home/frank/.cache/dogspeak-review/r5/after/dogspeak_app/gui.py:662) uses the currently selected draft:

   > `self.content += transcript_insertion(self.content, len(self.content), text)`

   It never checks the pending job’s `draft_id`. The app can therefore close successfully with A’s words saved in B. This requires an unusual sequence and is recoverable by moving the text.

   **Checked:** reproduced with the real controller and SQLite store, injecting an `append_transcript` failure. After reopening, A contained `'words'`; B contained `'Unrelated\n\nSpoken'`; close had returned `True`.

All 40 storage and controller tests passed. Window tests were not rerun because the sandbox lacks a display and session bus. No source files changed.
