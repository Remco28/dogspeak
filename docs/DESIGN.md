# Design decisions

Choices of taste for Dogspeak's windows, so they are made once. Shared palette,
timing and layout values live in `dogspeak_app/ui_values.py`; Qt-specific sizes
and stylesheet rules live in `dogspeak_app/qt_design.py`.

## Both platforms

- **Theme: Field Recorder + ON AIR.** Inspired by tactile pocket voice hardware
  (Braun / Dieter Rams / field recorders) paired with broadcast **ON AIR**
  transparency. Warm matte paper (`#F3F1EB`) in light mode, matte anodized
  graphite (`#191917`) in dark mode, crisp 1px hairlines, minimal radii (4–6 px),
  and signal orange (`#E8551E` / `#F0622A`) for the ON AIR lamp and active
  recording state. Follows the OS light/dark appearance automatically.
- **Layout: Focused Instrument (single-prompt workbench).**
  1. **Slim header:** puppy-behind-the-microphone mark, Dogspeak wordmark,
     inline draft title, compact New/Open draft controls (drafts are secondary),
     microphone selector, Suggestions toggle and `☰` menu.
  2. **Eye-level Hero Instrument Strip** directly above the editor (replacing
     the buried bottom status bar): monospace state lamp (`WARMING UP`,
     `STANDBY`, `● ON AIR`, `LOCAL CPU`, `↗ EXTERNAL · <provider>`, `ERROR`),
     plain-language status, tabular timer, and a ruled progress bar during
     loading, recording, transcription and Clean up.
  3. **Draft sheet:** generous centered/breathing numbered-line editor; locks
     clearly when an external LLM is rewriting.
  4. **Bottom Instrument Dock:** the three core actions in workflow order —
     **Record / Stop** · **Purpose + Clean up (+ Revert / Versions)** ·
     **Copy & Clear (+ Copy)** — wrapping gracefully so the window stays fully
     usable down to `440 px` wide (snapped beside a terminal or IDE).
- **Puppy-behind-the-microphone mark:** anchors the top-left header and appears
  quietly in the Hero Strip during wait states (model startup and external LLM
  rewrites) without paw-print decoration or gimmickry.
- **Recording cap transparency:** live capture auto-stops at 115 s (5 s below
  the engine's 120 s limit). Recording displays `M:SS / 1:55` with a ruled fill
  bar that shifts to warning amber in the final 20 s (`>= 95 s`). The floating
  pill also displays `M:SS / 1:55` and its progress bar.
- **Copy & Clear** is a primary dock action (`Ctrl+Shift+X`). Copy All remains
  available independently (`Ctrl+Shift+C`). Clear is a secondary menu action.
  "Clear" empties the editor and retains the draft title, saved transcripts and
  versions; "Delete draft" removes the saved draft and its transcripts.
- Copy & Clear reads the clipboard back before clearing. Any intervening edit,
  draft switch or active recording/transcription/Clean up prevents the clear.
  Clearing is one native undo step, with a brief message and Undo button. That
  button disappears after another edit or draft switch; `Ctrl+Z` still follows
  normal editor history. No confirmation dialog is needed for an undoable clear.
- **The pill** looks the same on both systems: dark graphite, rounded, a
  coloured dot per state (signal orange recording, amber transcribing, green
  saved, orange error, grey for information), bold title with `M:SS / 1:55`
  while recording, dimmer hint, and a bottom progress bar during recording.
- **Motion:** instant. No animations.
- **Words:** "draft" (never document or note), "Clean up" (two words, as a verb
  and on the button), "suggestions", "version", "purpose", "microphone".

## GNOME (GTK4 / libadwaita)

Apply the Field Recorder + ON AIR CSS theme via `Gtk.CssProvider`, switching
automatically with `Adw.StyleManager`'s `notify::dark`. Keep native GTK
accessibility, `GtkSource.View` line numbers and native `Adw.ToastOverlay`
feedback. Global shortcut: `Super+R`.

## Windows (Qt / PySide6)

Apply the Field Recorder + ON AIR palette and stylesheet via
`qt_design.stylesheet(dark=...)`, following the Windows light/dark system
palette automatically. Feedback such as "Copied and cleared" appears in a
notice bar above the bottom Instrument Dock, with its Undo button, for
`ui_values.FEEDBACK_SECONDS`. Global shortcut: `Ctrl+Alt+R`, changeable in
Settings (`Win+R` is reserved by Windows).
