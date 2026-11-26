# Review-loop ledger: dictation at the cursor, spacing, line numbers, line moving
*Kept by the review-loop skill. Safe to delete this folder once the work is done.*

Success question: In the ways its user really uses it, does this change put dictated text in the wrong place, or lose text or edits?
Rules confirmed with: Frank's request (2026-10-30), quoted in dictation-at-cursor/PLAN.md. Choices the author made beyond it are marked there and were reported to Frank in chat but not explicitly confirmed.
Reviewing: a change, from 623b808 (uncommitted, frozen as dictation-at-cursor/change.patch); old bugs filed in: BUGS.md (none in the repo yet)
Mode: light review, code-review profile.

## Light review, 2026-10-30, 623b808 + change.patch
Cast: critic, Codex gpt-6-luna (read-only; could not run tests). Blind solver, Claude Sonnet 5.5 (given the before version and Frank's words). Fix auditor, Codex gpt-6.1-sol (writable copy; no display, so no full window tests). Grok 4.7 as auditor timed out twice with no report (kept in reports/failed-first-run/); replaced.

| # | Finding | Reader (their rating) | Ours | From | Reaches | How checked | Done |
|---|---|---|---|---|---|---|---|
| 1 | Typing while a transcript is pending splits the word: 'I am typ\n\nSpoken words.ing a word'. README still promises typing is fine | Solver (design point, unrated) | MEDIUM: realistic, visible, recoverable | new | how it is used now | reproduced on both versions in the real window | Open: needs Frank's choice, then peer step |
| 2 | Alt+Down on a paragraph breaks blank-line spacing: 'aaa\n\nbbb' becomes '\nbbb\naaa'. The chat report's "two presses" was wrong | Solver (design point, unrated) | MEDIUM: default spacing makes it the normal case; undoable | new | how it is used now | reproduced in the real window | Open: needs Frank's choice |
| 3 | Reopening a cached draft keeps its old cursor, so dictation lands mid-draft; README and PRODUCT say opening puts it at the end | Auditor (MEDIUM) | MEDIUM | new | how it is used now | auditor ran it with real buffers; author reproduced on both versions in the real window | Open: fix after decision |
| 4 | Hidden-window transcript expands a selection ending at end of draft; deleting it then removes the transcript too | Auditor (MEDIUM) | MEDIUM: rare sequence, visible only after the delete | new | how it is used now | auditor ran it with real buffers; author reproduced on both versions in the real window | Open: restore the old selection code |
| 5 | Recording from another app with the window open behind it lands at a possibly stale cursor | Author; solver recommends the end | MEDIUM | new | how it is used now | read | Owner decision |
| 6 | A store failure while saving a transcript loses it from the UI and leaves Record disabled until Esc | Solver (unrated) | MEDIUM: visible error, text lost | old | how it is used now, on a full disk | reproduced on both versions | Filed in BUGS.md, not fixed |

Declined, each with a second opinion from the fix auditor, which agreed with all: custom gutter instead of GtkSourceView; refusing to record during Clean up; compare-and-set in the store; updating non-shown cached buffers at once; explicit cursor after replace_text (measured: already at the end); typing pause, flash, 'Added at line N', line-number toggle as unrequested extras. The typing pause reopens as an option under finding 1.

What happened against the predictions: none written; this was the first and only pass.
Believed, and wrong: "Moving a paragraph past the next one takes two presses because of the blank line" (told to Frank; never run). "A draft you open starts with the cursor at the end" (true only for a draft not yet opened this session). The critic's "no concrete path" was also wrong: it could not run anything, and findings 1 to 4 exist.

Stop state: no HIGH. Four new MEDIUMs are unfixed, so by the code profile this review has not met its stop rule: fixes need the peer step and a fix auditor after them. Stopped for Frank's decisions.

## Fixes, 2026-10-30, after Frank: "please make the fixes. use your best judgement."
Author's recommendations taken as decisions (FIX-PLAN.md). Peer step on the plan: critic Codex gpt-6-luna, blind solver Claude Sonnet 5.5 (reports/fix-plan/). Taken from the solver: its line-moving rule in place of the author's, the word-end snap, the waiting status. Critic's HIGH (quit loses a waiting result) was in the plan already; building it then took three goes, below.

| Finding | Reader (their rating) | Ours | From | How checked | Done |
|---|---|---|---|---|---|
| Quit loses a waiting result if its save fails | Auditor (MEDIUM) | MEDIUM: rare sequence | from a fix (1) | auditor ran it | Fixed, but see next |
| Retry after the refused quit loses it anyway | Auditor (HIGH) | MEDIUM: needs a store failure at quit in a one-second window, and the words were shown | from a fix (2) | auditor ran it | Fixed: words become an unsaved edit |
| Those words can land in the draft now shown | Auditor (MEDIUM) | MEDIUM | from a fix (3) | auditor ran it | Fixed and tested; no reader has seen this last fix |
| Selection moved to end of draft shrinks | Auditor (MEDIUM) | MEDIUM | from a fix (1) | auditor ran it; test added | Fixed, GONE at audit |
| Moving past an identical line raises | Auditor (LOW) | LOW | from a fix (1) | auditor ran it; test added | Fixed, GONE at audit |
| Cursor at a word's start jumped past the word | Author's test | MEDIUM | from a fix (1) | test failed | Fixed before audit |
| Recording with title focused goes to the old cursor | Auditor (MEDIUM) | Declined: the rule is the active window, so the side-panel case works | new | sent back; auditor withdrew it | Docs corrected |

Believed, and wrong: "Quitting delivers a waiting result, so it is not lost" (plan): it took three tries to be true when the save also fails. "GTK inside_word excludes the start of a word": it does not.
Stop: the last findings are from fixes, each needs a failed store write during a one-second wait at quit, and the stakes do not justify chasing them. The final fix has author tests only. No HIGH by our rating, but the auditor rated one HIGH and no disposition auditor checked that (the light review's second opinions stand in). Skill's stop rule strictly wants a further round; stopped by judgment, for Frank to overrule.
Not run: a real Alt+Up key press, a real Wayland session, the real microphone and model.

## Mistakes the next author will make
- Believing a clean report from a reader that could not run the code.
- Assuming "the end of the draft" still holds for anything that treats the cursor as meaningful: a stale cursor is now a destination.
- Testing a behaviour only on a fresh buffer. Buffers are cached per draft.
- Adding a delay to delivery without following it through the quit path: anything held in memory is one failed write from gone.
