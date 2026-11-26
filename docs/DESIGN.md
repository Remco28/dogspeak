# Design decisions

Choices of taste for Dogspeak's windows, so they are made once. The values
themselves live in code: `dogspeak_app/qt_design.py` for the Windows (Qt)
window. The GNOME window uses libadwaita's own values.

- **Look on Windows:** Qt's own Windows style and the system palette, so light
  and dark mode follow Windows. Only cards, the banner and the editor's page
  get rules of their own (`qt_design.stylesheet()`).
- **The pill** looks the same on both systems: dark, rounded, a coloured dot
  per state (red recording, yellow transcribing, green saved, orange error,
  grey for information), bold title, dimmer hint.
- **Motion:** instant. No animations.
- **Toolbar buttons** are words, not icons: New, Open, Copy all, Suggestions,
  Record. The main menu is ☰.
- **Words:** "draft" (never document or note), "Clean up" (two words, as a
  verb and on the button), "suggestions", "version", "purpose", "microphone".
- **Shortcut:** Ctrl+Alt+R on Windows, changeable in Settings (Frank,
  2026-11-26); Win+R is reserved by Windows. Super+R on GNOME.
