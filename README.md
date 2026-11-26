<p align="center"><img src="data/icons/hicolor/256x256/apps/io.github.Remco28.Dogspeak.png" width="160" alt="Dogspeak logo: a puppy behind a microphone"></p>

# Dogspeak

**Talk freely, get a clear prompt.** Dogspeak is a dictation scratchpad for Linux (GNOME) and Windows. Speak the way you think, rambling, repeating yourself, changing your mind, and Dogspeak turns it into text you can edit and paste into a coding agent, an email or anything else.

- **Local speech recognition.** Dictation runs on your own computer with the [Underdog Bark 3.0 Clean](https://huggingface.co/ConwayResearch/Underdog-Bark-3.0-Clean) model, which also drops "um"s and false starts. No audio leaves the machine.
- **Record from anywhere.** Press **Super+R** on Linux or **Ctrl+Alt+R** on Windows, in any app; a small pill at the bottom of the screen shows the recording, **Enter** stops it, **Esc** cancels. The text lands in your current draft and on the clipboard.
- **Clean up (optional).** One button sends the draft to a language model you choose (Claude, OpenRouter, OpenAI, Gemini, a local Ollama model, …) and turns it into a coherent, well-structured prompt, with suggestions on what's still missing. Every clean-up can be undone.
- **Drafts, not chats.** Several drafts, autosaved, with titles; nothing is sent or pasted anywhere unless you do it.

![Dogspeak after a clean-up: the structured prompt on the left, suggestions on the right](docs/cleanup.png)

## Requirements

- **Linux with GNOME**, or **Windows 10 or 11**. On Linux it is developed and tested on Ubuntu 26.04 (GNOME 50, Wayland); GNOME on X11 should work but hasn't been tested. On other Linux desktops (KDE, Xfce, …) the editor, recording and Clean up work, but the Super+R shortcut, the on-screen pill and the top-bar menu do not, because those are built on GNOME Shell.
- **About 4 GB of free RAM** while Dogspeak is open (the speech model stays loaded) and about 2 GB of disk.
- A microphone. On Linux, PipeWire (the default on current Ubuntu, Fedora and Debian).
- No GPU is needed; recognition runs on the CPU. A paragraph typically takes one to two seconds.

macOS is not supported.

## Install on Linux

1. Install the system packages (Ubuntu or Debian; other distributions have equivalents):

   ```sh
   sudo apt install git python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-gtksource-5 gir1.2-secret-1 pipewire-bin
   ```

2. Install [uv](https://docs.astral.sh/uv/getting-started/installation/), the Python package manager Dogspeak uses for its speech-recognition environment. It fetches the right Python version by itself.

3. Clone and set up:

   ```sh
   git clone https://github.com/Remco28/dogspeak.git
   cd dogspeak
   ./setup.sh
   ```

   `setup.sh` builds the speech-recognition environment and downloads the model (about 180 MB), checking every file against the checksums in `upstream.json`. It is safe to run again.

4. Start it:

   ```sh
   ./app.sh
   ```

   The first start takes about half a minute while the model loads; you can type in the meantime.

5. **Recommended:** add Dogspeak to the app launcher and turn on Super+R, the on-screen pill and the top-bar menu:

   ```sh
   /usr/bin/python3 desktop_install.py --shortcut
   ```

   Then **log out and back in once**, so GNOME Shell loads the Dogspeak extension. Without `--shortcut`, only the launcher entry is added. The installer never replaces a shortcut another app already uses; it stops and tells you instead.

## Install on Windows

1. Install [Git for Windows](https://git-scm.com/download/win) and [uv](https://docs.astral.sh/uv/getting-started/installation/). uv fetches the right Python by itself.

2. Clone and set up, in a terminal (cmd or PowerShell):

   ```bat
   git clone https://github.com/Remco28/dogspeak.git
   cd dogspeak
   setup.cmd
   ```

   `setup.cmd` builds the environment (`.venv`, about 1 GB) and downloads the model (about 180 MB), checking every file against `upstream.json`. It is safe to run again.

3. Start it with `dogspeak.cmd`. The first start takes about half a minute while the model loads; you can type in the meantime.

4. **Optional:** add Dogspeak to the Start menu:

   ```bat
   .venv\Scripts\python.exe windows_install.py
   ```

On Windows, **Ctrl+Alt+R** records from any app (change it in Settings; Windows keeps Win+R for itself). The pill appears above the taskbar, and **Enter** and **Esc** stop or cancel while it records. Closing the window keeps Dogspeak in the tray (it may be in the taskbar's hidden-icons area; drag it out to keep it in view). **Ctrl+Q** or *Quit* in the tray menu exits and frees the memory. API keys go in Windows Credential Manager.

If recordings come back as "only silence", Windows is blocking the microphone: turn on *Settings → Privacy & security → Microphone → Let desktop apps access your microphone*.

### What the Windows installer changes

`setup.cmd` changes nothing outside the Dogspeak folder except uv's download cache. `windows_install.py` adds the items below and records them in a ledger; `windows_install.py --uninstall` removes exactly those, and only if the shortcut still points at this folder.

| Change | Where | Undone by |
|---|---|---|
| Start menu shortcut | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Dogspeak.lnk` | `--uninstall` |
| Its icon | `%LOCALAPPDATA%\Dogspeak\dogspeak.ico` | `--uninstall` |
| The ledger | `%LOCALAPPDATA%\Dogspeak\install.json` | `--uninstall` |

Running Dogspeak keeps your drafts in `%LOCALAPPDATA%\Dogspeak` and its log in `%LOCALAPPDATA%\Dogspeak\logs`, and API keys in Credential Manager under "Dogspeak". To remove Dogspeak completely: run `--uninstall`, delete the Dogspeak folder you cloned, delete `%LOCALAPPDATA%\Dogspeak` (your drafts), and remove the "Dogspeak" entries in Credential Manager. Nothing is added to the registry, to startup or to PATH.

## Using Dogspeak

### Dictation

- **Record** (or **Super+R** on Linux, **Ctrl+Alt+R** on Windows, from any app) starts recording; press it again, or **Enter**, to stop. **Esc** cancels and throws the audio away.
- The transcript goes where your cursor is, and the cursor moves to the end of it, so the next recording carries on from there. Click anywhere in the draft to dictate into that spot: between lines it becomes its own line or paragraph, in the middle of a line it joins the sentence. A draft you open or go back to starts with the cursor at the end (**Ctrl+End** gets back there).
- You can keep typing while it's being transcribed. If you're mid-word when it finishes, the transcript waits until you pause for a second.
- A recording started while another app has focus, or with the window closed, goes at the end of the draft. So does an imported audio file.
- A recording started while another app has focus is also put on the clipboard, so you can paste it straight away. Dogspeak never pastes for you.
- Recordings stop automatically at 115 seconds. Audio files are deleted once transcribed.
- Choose a microphone at the bottom right, or leave it on **System default**.

### Drafts

- Lines are numbered; a long paragraph that wraps counts as one line. **Alt+Up** and **Alt+Down** move the current line or selected lines, also in the ☰ and right-click menus. A paragraph swaps with the next one and the blank line between them stays put; a line within a paragraph moves past its neighbour.
- **Ctrl+N** new draft, **Ctrl+O** open a draft (the trash button deletes one, after asking), **Ctrl+Shift+C** copy the whole draft. Everything autosaves.
- Closing the window only hides it; the model stays loaded so the next recording is instant. Bring it back from the microphone icon in the top bar or the app launcher. **Ctrl+Q** (or *Quit* in either menu) exits and frees the memory.

### Clean up

Press **Clean up** (**Ctrl+Shift+L**) and the draft is rewritten: repeated points merged, corrections kept, grammar fixed, names and code left exactly as you said them, nothing invented.

- **Purpose** (optional): leave it blank for a general tidy-up, or say what the text is for. The menu offers *Prompt for an AI agent*, *Essay*, *Email*, *Notes* and your last five custom purposes. A prompt comes back structured as goal, context, requirements and what counts as done.
- The result replaces the draft. **Ctrl+Z** undoes it immediately; **Revert** and the version menu go back through the last 20 versions. Edits you made by hand are kept as a version too, so nothing is lost.
- **Suggestions** about what's missing or ambiguous appear in the side panel. They're for you to read; dismiss each with ×.
- While a clean-up runs the draft is locked; **Esc** cancels it.

**Setting up a provider:** open ☰ → **Settings** (**Ctrl+,**), pick a provider, paste an API key and a model name, and press **Test**. The test only checks the key and model; it costs nothing.

| Provider | Where to get a key | Model name examples |
|---|---|---|
| OpenRouter (default) | [openrouter.ai](https://openrouter.ai/keys), one key for many models | see [openrouter.ai/models](https://openrouter.ai/models) |
| Claude (Anthropic) | [platform.claude.com](https://platform.claude.com) | `claude-opus-5-5`, or `claude-haiku-5-5` for much lower cost |
| OpenAI, Google Gemini, Groq, DeepSeek | the provider's console | the provider's model list |
| Ollama on this computer | no key needed | any model you've pulled |
| Anything OpenAI-compatible | choose *Custom* and enter its address | |

API keys are stored in GNOME's password keyring (Windows Credential Manager on Windows), never in Dogspeak's files. Only the Clean up button sends anything off your computer: the draft and its purpose, to the provider you chose.

## Privacy

- Speech recognition is entirely local. Audio is deleted after transcription or cancellation.
- Drafts live in `~/.local/share/dogspeak` (a SQLite database readable only by you). Nothing is uploaded or synced.
- The model is downloaded once during setup; after that recognition works offline.
- Clean up is the only feature that uses the network, and only when you press it.

## Troubleshooting

- **No pill, no top-bar icon, Super+R does nothing:** run `desktop_install.py --shortcut` and log out and back in. Check that user extensions are switched on in GNOME's *Extensions* app. Until the extension loads, Dogspeak shows its status as notifications.
- **"Dogspeak is loading…" when pressing Super+R:** the model is still starting; press again in a moment. Dogspeak never starts a recording late on its own.
- **Super+R already used:** the installer refuses to take a shortcut another app owns. You can bind `/path/to/dogspeak/app.sh --toggle-recording` to any key in GNOME *Settings → Keyboard → Custom Shortcuts*.
- **Generic icon in the launcher:** run the installer again; it refreshes GNOME's icon cache.
- **Clean up fails:** the banner gives the reason (key rejected, out of credit, model not found, offline). **Test** in Settings checks the setup without spending anything.
- **Logs:** the speech worker writes to `~/.local/state/dogspeak/worker.log`.

## How the two platforms differ

The drafts, version history, Clean up, the speech worker and all of their rules are shared code (`dogspeak_app/controller.py`, `cleanup.py`, `storage.py`, `engine.py`). Each platform has its own window and its own small modules for the operating system:

| | Linux (GNOME) | Windows |
|---|---|---|
| Window | GTK 4 / libadwaita, `gui.py`, system Python | Qt (PySide6), `qt_app.py`, the project environment |
| Microphone | PipeWire's `pw-record`, `audio.py` | PortAudio (WASAPI), `audio_portaudio.py` |
| Shortcut | GNOME custom keybinding, `desktop_install.py` | `RegisterHotKey`, `hotkeys.py` |
| Pill, Enter/Esc, menu | GNOME Shell extension | an always-on-top window, held hotkeys, a tray icon |
| API keys | Secret Service, `credentials.py` | Credential Manager, `credentials_keyring.py` |

[ARCHITECTURE.md](ARCHITECTURE.md) describes the module boundaries.

## Development

```sh
./test.sh
```

runs the core tests in the project environment and the GTK tests headless (needs `xvfb`: `sudo apt install xvfb`). The Qt window's tests run headless too when the environment has the dev group (`uv sync`). On Windows, run `.venv\Scripts\python.exe -m unittest discover -s tests -p "test_[!g]*.py"`. GitHub Actions runs the tests on Ubuntu and Windows for every push. Tests use temporary data and stand-ins for the microphone, the speech model and the language-model providers; they never record or spend credit.

- [ARCHITECTURE.md](ARCHITECTURE.md): how the pieces fit together and why.
- [PRODUCT.md](PRODUCT.md): what Dogspeak is meant to be, and the decisions behind it.
- [SETUP.md](SETUP.md): what `setup.sh` does, step by step, and the pinned model versions.
- [BENCHMARK.md](BENCHMARK.md): speed and memory measurements of the speech model on a laptop CPU.
- [docs/SCRATCHPAD-VERIFICATION.md](docs/SCRATCHPAD-VERIFICATION.md): how the app was checked end to end, and what hasn't been.

A command-line tool is also included for testing the model directly: `./run.sh listen` records in the terminal and `./run.sh transcribe FILE…` transcribes audio files (`./run.sh --help` lists the options).

## License and credits

Dogspeak's code is released under the [MIT License](LICENSE).

The speech model is **Underdog Bark 3.0 Clean** by Conway Research, derived from NVIDIA's Parakeet TDT 0.6B v3 and Moondream's Parakeet Ultra, all under [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/). The model is downloaded during setup and is not part of this repository; its loader and notices are in `vendor/`, unchanged. Keep `vendor/NOTICE` with any redistribution.
