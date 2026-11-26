# Plan: dictation at the cursor

Written after the build, not before it: the review was requested once the change
existed. So the peers are reviewing a built change, and the plan describes it.
Frozen as change.patch against 623b808; not committed, because Frank has not asked
for a commit.

## Stakes
Dogspeak is a single-user GNOME/Wayland dictation scratchpad, launched from the
desktop entry with the model kept loaded. Frank dictates, edits and copies prompts
for coding agents, often in many chunks. A failure costs misplaced or lost dictated
text, which is slow to re-speak and may be noticed only after pasting it elsewhere.
Nothing is shared and no money is involved.

## What Frank asked for (2026-10-30)
- "move the cursor to the bottom each time text is inserted"
- "if a user manually moves the cursor in our application, that's where the text should be inserted"
- things "can get very messy if I insert my own line breaks"
- line numbers, and "possibly the ability to reorder lines", skipped if no clean way

## What was built
1. A transcript for the shown draft goes in at the cursor, and the cursor moves to the
   end of the spoken words. A newly loaded draft starts with the cursor at the end.
2. Spacing comes from the cursor's surroundings (storage.transcript_insertion): spaces
   inside a line, otherwise the gap the cursor is in; a blank line by default.
3. The editor becomes GtkSource.View: line numbers, Alt+Up/Down and menu items to move lines.
4. gir1.2-gtksource-5 becomes a required system package.

## Choices the author made, not Frank (told to him afterwards, not explicitly confirmed)
- A hidden window sends text to the end of the draft, leaving the cursor alone.
- Text goes to the end while Clean up runs on that draft, because Clean up needs the
  current text to start with what it sent.
- The position is read when the transcript arrives, not when recording starts.
- With text selected, the transcript goes after the selection and does not replace it.
- A current-line highlight (not requested).

## Checks already run
./test.sh passes (79 + 71). The window was rendered under Xvfb with a stand-in engine:
long draft opens scrolled to the cursor, dictation at the end and mid-draft, dark theme.
Not run: a real Alt+Up key press, a real Wayland session, the real microphone and model.
