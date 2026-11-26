## Findings

I found no concrete path in the reviewed change where dictated text lands in the wrong place, user edits are lost, or the saved draft diverges from the text on screen.

The insertion flow flushes pending edits before saving a transcript. For the visible draft, the same offset and insertion are then applied to the editor buffer; for hidden drafts or drafts locked by Clean up, insertion goes to the end. Buffer changes from typing or undo mark the draft dirty and are saved through the existing autosave path. I read these paths in `gui.py`, `storage.py`, and `cleanup.py`.

## Author choices

I would keep the listed choices. Sending hidden-window text to the end is predictable when there is no visible cursor. Sending text to the end during Clean up preserves the prefix Clean up is processing. Reading the cursor when the transcript arrives reflects where the user has moved it. Inserting after a selection while preserving its text is a reasonable default, though it should be treated as a product choice. The current-line highlight is harmless.

## Verification

I read the changed insertion, save, undo, and Clean up paths and the added tests. I also ran `./test.sh`, but this read-only environment could not create temporary files, so the suite failed broadly before it could provide a useful verification result. No files in the copy were edited.
