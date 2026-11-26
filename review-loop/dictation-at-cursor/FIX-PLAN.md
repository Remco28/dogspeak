# Fix plan for the light review's findings

Frank, 2026-10-30: "please make the fixes. use your best judgement." So the author's
recommendations stand, and are listed here as decisions.

1. Typing while a transcript is pending. A result that would go to the cursor waits until
   the draft has had no edits for one second. No cap, because a cap would split the word
   anyway. The job stays pending while it waits, so Esc, Record, Clean up and draft deletion
   all behave as they already do for a pending job. Results go through the controller again
   on a timer. Quitting delivers a waiting result first, so it is not lost.
2. Moving paragraphs. Replace GtkSource's move-lines with our own, on the same signal so the
   built-in Alt+Up/Down binding cannot do the old thing. The block is the current line, or
   the lines of the selection. If the next line is not blank, swap with that one line, so
   tight lists work as before. If blank lines come next, swap with the whole run of non-blank
   lines after them, and the blank lines stay where they were:
   aaa, blank, bbb becomes bbb, blank, aaa. No move at either end of the draft, on a blank
   line, or while the editor is locked. One undo step; the cursor and selection move with
   the text.
3. Returning to a draft puts the cursor at its end, like opening one for the first time.
4. A transcript added at the end leaves an existing selection as it was (the old behaviour);
   with no selection, a cursor at the end still follows the text.
5. Where text goes: to the cursor only for a recording started while Dogspeak had focus.
   Recordings started from another app, and audio imports, go to the end. The window closing
   before the result arrives still sends it to the end.
