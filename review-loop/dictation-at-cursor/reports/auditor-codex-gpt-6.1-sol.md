I found **two additional regressions, both MEDIUM**. I reviewed every diff hunk and left source files unchanged.

1. **Reopening a cached draft does not put its cursor at the end.**

   Scenario: enter `First\n\nSecond`, place the cursor after `First`, switch to another draft, then reopen the original and dictate `More`.

   - **After:** `First\n\nMore\n\nSecond`.
   - **Before:** `First\n\nSecond\n\nMore`.

   Both versions retain cursor offset 5 when reopening. Previously that did not affect dictation; now it determines placement. This contradicts the new README and PRODUCT promise that opening a draft puts its cursor at the end.

   The reset in [gui.py:804](/home/frank/.cache/dogspeak-review/r1/after/dogspeak_app/gui.py:804) only runs for a newly created buffer. Cached buffers take the branch at [gui.py:775](/home/frank/.cache/dogspeak-review/r1/after/dogspeak_app/gui.py:775).

   **Checked:** ran the same scenario against both versions using their actual controller and rendering methods with real GTK buffers and stand-in window widgets. Placement is visibly wrong and recoverable.

2. **A transcript appended while the window is hidden expands a selection ending at EOF.**

   Scenario: select the draft’s entire text, hide the window, dictate from another app, then reopen and delete the previously selected text.

   - **After:** the selection has expanded to include the transcript; deletion leaves an empty draft.
   - **Before:** the selection retains its original bounds; deletion leaves `\n\nHidden`.

   The old renderer restored both selection marks after appending. The new [insertion branch](/home/frank/.cache/dogspeak-review/r1/after/dogspeak_app/gui.py:792) neither restores them nor explicitly positions them when `at=None`. GTK’s marks therefore expand the selection across the appended text.

   **Checked:** ran both versions with real GTK buffers. For a 19-character draft, the selection changed from `[0,19]` to `[0,27]` after hidden dictation only in the new version. This requires an unusual sequence and is undoable.

The first finding is also the concrete inconsistency across code, docs and tests: the added opening-cursor test passes without exercising a cached buffer whose cursor is in the middle. I found no further actionable inconsistency in the dependency imports, setup checks, installation instructions, storage changes, menus or action registration. Historical verification notes describe earlier runs and need no correction.

For the proposed rejections:

| Item | Does the reason hold? | Code check |
|---|---|---|
| **a. Custom gutter instead of GtkSourceView** | **Yes.** | ARCHITECTURE restricts native binding builds in the inference environment, not distro GUI dependencies. GtkSourceView runs under system Python, supplies the editor features, and is included consistently in setup and docs. |
| **b. Refuse recording during Clean up** | **Yes.** | `Controller._event()` leaves `at=None` for a locked draft. Storage appends, preserving Clean up’s required prefix. The controller-level overlap test passed; the added GUI test also explicitly describes this sequence. |
| **c. Replace append with compare-and-set** | **Yes, for the supported execution model.** | Editor changes update controller content synchronously; `flush()` saves it immediately before insertion, with no intervening asynchronous step. I found no normal application path violating that invariant. The renderer’s exception is a diagnostic after persistence, however, not a rollback safeguard. |
| **d. Immediately update a non-shown cached buffer** | **Yes, for transcript catch-up.** | Non-shown results append in storage. I ran result delivery while another draft was selected, reopening, then undo: both versions caught up to `First\n\nAway`, and undo restored `First`. Finding 1 concerns cursor positioning separately. |
| **e. Explicit cursor placement after Clean up** | **Yes.** | I ran replacement through `replace_text()` in both versions. Replacing with `Clean text.` left the cursor at offset 11, exactly EOF. Deletion followed by insertion already produces the stated result. |
| **f. Pause, flash, line notification, line-number toggle** | **Yes, as optional product choices.** | I found no separate concrete failure requiring these additions. That does not dismiss the already established findings you excluded from this audit. |

Validation was limited by the environment. `./test.sh` reported two setup errors because the sandbox blocks local HTTP server sockets; its storage tests passed. Separately, all **33 controller and Clean up tests passed**. Full window tests could not initialize a display, and the private D-Bus session could not bind its socket. The reproductions above therefore used real text buffers and application methods without a full GUI window.
