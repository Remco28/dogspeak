# Known bugs

## A failed save loses the transcript
If saving a finished transcript fails (for example, the disk is full), the status shows an error, the text never reaches the draft, and Record stays disabled until Esc drops the job. Esc also deletes the audio, so the words are gone. Nothing retries the save. Seen on 623b808 and later; `Controller._event` in `dogspeak_app/gui.py`.
