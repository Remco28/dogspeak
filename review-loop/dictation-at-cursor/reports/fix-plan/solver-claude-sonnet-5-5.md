I read `_event` and `_insertion_point` in `gui.py`, `render_draft`, and `transcript_insertion` and `append_transcript` in `storage.py`. I wrote no code and ran nothing.

## 1. Problem A: transcript landing in the middle of a word

**Cause.** The cursor offset is read at the moment the result arrives (`gui.py:214`). If the person is mid-word, `transcript_insertion` sees non-space text on at least one side and produces `hel` + ` text ` + `lo`. Then `render_draft` puts the cursor after the dictated words (`gui.py:798`). The rest of their typing therefore lands after the transcript. Nothing is lost, but the word is split.

**Design: hold the result until the person pauses, then insert at the cursor as it is then.**
- The window tracks the time of the last buffer change or cursor move in the shown draft.
- When a result arrives for the shown draft, it checks two things before the existing `flush()` and `append_transcript` path. The first is whether the window is visible and the editor focused. The second is whether the last activity was more than about 1 s ago. If both hold, it inserts immediately.
- Otherwise the job stays in `pending` with its text attached, and a GLib timeout re-checks every ~300 ms. Insertion then follows the existing synchronous path. It reads the offset, saves, and renders in one step, so no stale offset or mark can drift.
- At insertion, if the cursor is strictly inside a word (word characters on both sides), snap forward to the end of that word. A pause with the cursor parked inside a word is rare, and splitting it is never what anyone wants. A cursor touching a word on one side keeps today's behaviour, with a separator space added.
- Two or more held results are inserted in arrival order. Each one leaves the cursor after itself, so the next lands after it.
- I would not add a hard time cap. A forced insert while the person is still typing recreates the bug.

**What can go wrong while it waits:**
- **No text loss on crash.** `append_transcript` is the only durable step, and `_forget_audio` deletes the audio right after it (`gui.py:219`). While a result is held, the transcript text lives only in memory, so keep the audio file until the insert succeeds. Nothing else changes there.
- **Draft switch, a locked draft (Clean up running), a hidden window, or focus in the title entry.** None of these have a typing cursor to protect. Resolve the held job at once with the existing `at=None` (end of draft) rule.
- **Close or quit.** Flush held results (at the end of the draft) in the close path before teardown. Don't discard them.
- **Esc and delete-draft.** The job stays in `pending`. Esc drop, `_drop_jobs`, and the "waiting for a transcript" guard on delete (`gui.py:505`) keep working unchanged.
- **Status.** Show something like "Waiting for you to pause" instead of a bare "Transcribing…". Otherwise the person can't tell why nothing appeared.
- **Selection.** Keep inserting after the selection end, as now. Typing would replace the selection, but the insert never does.
- **Timer lifetime.** Cancel the timeout on window destroy and on draft close.
- **IME composition.** The buffer doesn't change during preedit, so a long composition could look like a pause. That is an edge case, and I'd leave it unless it shows up in use.

## 2. Problem B: Alt+Up/Down reordering

A line here means a buffer line, so a wrapped paragraph is one line. A blank line is empty or whitespace-only. A block is a maximal run of consecutive non-blank lines. A gap is the run of blank lines between two blocks. Leading blank lines and a trailing newline belong to the surrounding text, not to any block.

**Unit to move (U).**
- With no selection, U is the cursor's line.
- With a selection, U is the selected lines. If the selection ends at column 0 of a later line, that line is excluded.
- If U contains a blank line, extend it to the whole blocks at both ends and trim blank edges.

**Mode.**
- **Block move** applies if U is exactly one or more whole blocks. That includes a lone one-line paragraph with no selection, and a tight list fully selected.
- **Line move** applies otherwise. That is a line or partial run inside a multi-line block.

**Block move.** Let N be the adjacent block in the direction of travel. The text is P + U + G + N + S, where G is the gap between them. It becomes P + N + G + U + S. The contents swap and every gap stays exactly where it was, so no blank lines are added, removed, or shifted.

**Line move.**
- If the neighbouring line in that direction is non-blank, swap U with it, as a tight list expects.
- If U sits at the edge of its block, U hops over the whole gap and becomes the first line (down) or last line (up) of the next block. The gap is unchanged.

**Ends.** If there is no neighbour block, or no block to hop into, do nothing. Consume the key and don't fall through to GTK's swap. Leading blank lines and the final newline state are never touched.

**Cursor, selection, undo.**
- Rewrite only the affected line span, from the first line of the earliest piece to the last line of the latest piece. The edit goes in one `begin_user_action`/`end_user_action`, so one Ctrl+Z reverses it.
- Re-place the cursor or selection at the same line and column offsets inside the moved unit.
- Alt+Down then Alt+Up always restores the original.

**Examples** (`·` is a blank line):

1. `aaa · bbb · ccc`, cursor on aaa, moving down.
   - aaa is a one-line block, so this is a block move with bbb.
   - Result: `bbb · aaa · ccc`.
   - Cursor stays on aaa, now line 3. Both gaps are still exactly one blank line.

2. `a b · c`, cursor on b, moving down.
   - b is one line in a two-line block, so this is a line move. b is at the block's edge, so it hops the gap into c's block.
   - Result: `a · b c`.
   - Cursor stays on b. The gap is still one blank line, and nothing is stray.
   - Pressing Alt+Down again swaps b with c, giving `a · c b`.
   - Alt+Up from b returns to `a b · c`.

**Wiring.** `move_line` currently emits GtkSourceView's `move-lines` (`gui.py:724`). Replace its body with the rule above. The Alt+Up/Down accelerators already route there, so the only thing to check is that GtkSourceView's own binding doesn't fire first.
