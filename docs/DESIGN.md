# Scratchpad interface decisions

Use native GTK/libadwaita controls, symbolic icons and theme colours. New
interface values belong in `dogspeak_app/ui_values.py`; existing editor and
toolbar spacing is the starting point for future consolidation.

Copy & Clear is a labelled toolbar action (Ctrl+Shift+X). Copy All remains
available independently (Ctrl+Shift+C). Clear is a secondary menu action.
“Clear” empties the editor and retains the draft title, saved transcripts and
versions; “Delete draft” removes the saved draft and its transcripts.

Copy & Clear reads the clipboard back before clearing. Any intervening edit,
draft switch or active recording/transcription/Clean up prevents the clear.
Clearing is one native undo step, with a brief message and Undo button. That
button disappears after another edit or draft switch; Ctrl+Z still follows
normal editor history. No confirmation dialog is needed for an undoable clear.
Use native immediate state changes and native toast feedback.
