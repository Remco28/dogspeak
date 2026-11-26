# Design decisions

Choices of taste for Dogspeak's windows, so they are made once. The values
themselves live in code: `dogspeak_app/ui_values.py` for the GNOME (GTK)
window's new controls, and `dogspeak_app/qt_design.py` for the Windows (Qt)
window.

## Both platforms

- **Copy & Clear** is a labelled toolbar action (Ctrl+Shift+X). Copy All
  remains available independently (Ctrl+Shift+C). Clear is a secondary menu
  action. "Clear" empties the editor and retains the draft title, saved
  transcripts and versions; "Delete draft" removes the saved draft and its
  transcripts.
- Copy & Clear reads the clipboard back before clearing. Any intervening
  edit, draft switch or active recording/transcription/Clean up prevents the
  clear. Clearing is one native undo step, with a brief message and Undo
  button. That button disappears after another edit or draft switch; Ctrl+Z
  still follows normal editor history. No confirmation dialog is needed for an
  undoable clear.
- **The pill** looks the same on both systems: dark, rounded, a coloured dot
  per state (red recording, yellow transcribing, green saved, orange error,
  grey for information), bold title, dimmer hint.
- **Motion:** instant. No animations.
- **Words:** "draft" (never document or note), "Clean up" (two words, as a
  verb and on the button), "suggestions", "version", "purpose", "microphone".

## GNOME

Use native GTK/libadwaita controls, symbolic icons and theme colours. New
interface values belong in `dogspeak_app/ui_values.py`; existing editor and
toolbar spacing is the starting point for future consolidation. Use native
immediate state changes and native toast feedback. Shortcut: Super+R.

## Windows

- **Look:** Qt's own Windows style and the system palette, so light and dark
  mode follow Windows. Only cards, the banner, the notice and the editor's
  page get rules of their own (`qt_design.stylesheet()`).
- **Toolbar buttons** are words, not icons: New, Open, Copy all, Copy & Clear,
  Suggestions, Record. The main menu is ☰.
- **Feedback** such as "Copied and cleared" appears in a notice bar above the
  status line, with its button, for `ui_values.FEEDBACK_SECONDS` (Qt has no
  toast).
- **Shortcut:** Ctrl+Alt+R, changeable in Settings (Frank, 2026-11-26);
  Win+R is reserved by Windows.
