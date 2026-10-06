# Johnny — AI Computer Butler
**by DopeKitchen Industries** · Windows 10/11 x64 · runs 100% locally with [Ollama](https://ollama.com)

Johnny is a private, offline assistant that talks with you, controls your PC, and **learns tasks by watching you**.

## Install
**Easiest:** run **`dist\JohnnySetup.exe`**. It's a normal Windows installer (no admin needed), and it upgrades older "Jarvis" installs while keeping your settings, memory and skills.

**From source:**
1. Double-click **`Install Johnny.bat`**.
   - Installs Python 3.12 and Ollama with `winget` if they're missing.
   - Copies Johnny to `%LOCALAPPDATA%\Programs\Johnny` with its own Python environment.
   - Registers an uninstaller under *Settings › Apps*.
2. The **Setup Wizard** opens:
   | Step | What you choose |
   |---|---|
   | System check | Detects CPU/RAM/GPU and checks that Ollama is running |
   | AI model | Picks the best model for your hardware (★). You can also choose any installed model or type any Ollama tag |
   | Personality | Your name, the assistant's name (you'll wake it with "Hey <name>"), and a style: Butler / Friendly / Concise / Witty |
   | Voice | Speaking voice, speed, a voice test, offline voice input (faster-whisper) and hands-free "Hey Johnny" |
   | Learning & privacy | Habit learning, automation suggestions, privacy keywords, and permissions (ask / always / never) |
   | Startup | Start with Windows, start minimized to the tray, desktop and Start-menu shortcuts |
   | Install | Downloads the model with a progress bar and installs any optional components |

To change these later, open **Settings › Run full setup again**, or run `Johnny --setup`.

## Using Johnny
- **Chat or talk.** Try "open Spotify", "search YouTube for lo-fi", "how's my PC doing?", "remind me in 20 minutes to stretch", "remember my wife's name is Ana", "what's in my Downloads?", "volume up".
- **"Hey Johnny" (hands-free):** works even while Johnny is minimized to the tray.
  - Say **"Hey Johnny, open Spotify"** in one go, or say **"Hey Johnny"**, wait for the chime, then make your request.
  - A small "Listening…" banner appears while the window is hidden.
  - Switch it on or off with the *Hands-free* checkbox, or in *Settings › Voice input*.
- **Push-to-talk:** `Ctrl+Alt+J` works from anywhere.
- **Interrupt him by talking:** while Johnny is speaking, just start talking. He stops mid-sentence, drops the rest of his reply, and takes your new request, with no wake word needed. Saying "stop" or "never mind" just quiets him.
  - **On speakers**, Johnny compares the mic against what he's playing, so his own voice coming back from the speakers doesn't cut him off.
  - **A headset gives the fastest cut-off**, about 0.3 s.
- **Answer his questions:** when a reply ends with a question, the mic opens for 8 seconds after he finishes, with a soft chime and a "Listening for your answer…" banner. Just answer.
- Turn either of these off in **Settings** (*Interrupt by talking* and *Listen for my answer after a question*).
- **Teach mode (watch and learn):**
  1. Press `Ctrl+Alt+R`, or say "learn how to export my report".
  2. Do the task normally. Johnny records your clicks, typing and window switches.
  3. Press `Ctrl+Alt+S` to stop. The AI names the task, describes it, and suggests **variables**: in `weather in {city}`, `{city}` is filled in each time.
  4. Later, say "check the weather in Dallas" and Johnny replays the task with `city = Dallas`. Press **Esc** to abort a replay at any time.
- **Habit learning:** Johnny notes which apps you use and when. After a few days it suggests routines, such as "You open Outlook around 8:55 most days. Want me to do that automatically?" or a one-phrase command that opens apps you always use together.
- **Tray:** closing the window minimizes Johnny to the system tray.

## Voice and AI model
- **Natural voice:** Johnny speaks with **Kokoro**, an offline neural voice that sounds close to Siri. Choose from 13 voices in **Settings › Voice** (Heart is the most Siri-like, George is a British butler) and adjust the speed. The voice model is a one-time ~350 MB download. You can switch back to the classic Windows voice at any time.
- **Talks back results:** Johnny speaks while it's still writing, a sentence at a time. It reads out what it did and what it found, including the **weather** (wttr.in) and **look-ups** from Wikipedia. These two are the only features that go online; turn them off in Settings with *Allow online lookups*.
- **Recommended model: `qwen3.5:4b`.** It handles all of Johnny's tool tasks using about **4 GB** of graphics memory, versus about 10 GB for `gemma4:26b`, which leaves room for games. Larger models are listed in Settings.
- If Ollama stops (closed or updating), Johnny restarts it and retries automatically.

## Game mode
When a game is in the foreground, a small **HUD** appears in the top-right corner. Johnny counts something as a game if it's fullscreen or borderless, or installed under Steam, Epic, Riot, GOG, Xbox, EA, Ubisoft, Battle.net or Rockstar.

| HUD control | What it does |
|---|---|
| 🎙 **Ask** | Ask out loud (or say "Hey Johnny, …"). Johnny **sees your screen** while answering |
| 👁 **Look** | "What should I do right now?" based on the current screen |
| ⏺ **Learn** | **Teach by playing:** Johnny takes periodic screenshots and tracks which controls you use. When you stop, it writes notes about the game and your play style, and uses them in future coaching |
| **Mode** | **Off**: answers only when asked. **Coach**: speaks up with a tip when something matters. **Steer**: *you drive, Johnny steers*, calling out short directions every few seconds |
| ⚙ | Real-time settings: mode, tip and steer frequency, spoken tips, hands-free, microphone and level meter, HUD opacity, "Not a game" |

- `Ctrl+Alt+H` shows or hides the HUD. **—** collapses it to a small logo bubble; click the bubble to expand it. Drag the HUD by its title to move it.
- The HUD re-asserts "always on top" every second, so games that push themselves to the front can't bury it. It stays up through loading screens and alt-tabs, and only goes away when the game process exits. Turn on **Always show HUD** in ⚙ to keep it up all the time.
- **⛶ Make borderless** (or say "Hey Johnny, make it borderless") turns a *windowed* game into borderless fullscreen.
- The HUD never takes keyboard focus from your game.
- **Exclusive fullscreen** (common in older DirectX 9 games) doesn't allow any overlay to draw over it without injecting code into the game, which anti-cheat flags. Johnny tells you out loud when a game is in exclusive fullscreen. To fix it, set the game to Windowed and use ⛶ Make borderless, or pick Borderless in the game's settings.
- Game mode needs a **vision** model. `gemma4`, `gemma3`, `qwen2.5vl` and `llava` all work. To leave more GPU for your game, set a smaller vision model in **Games › Vision model**.
- Johnny never presses keys inside your games. Automated input in online games can get accounts banned.

## Microphone troubleshooting ("Hey Johnny" doesn't respond)
1. Open **Settings**, talk, and watch the **mic level bar**. If it doesn't move, pick a different **Microphone** from the list.
2. If a mic is muted, switched off, or blocked under *Windows Settings › Privacy › Microphone*, Johnny says so in the chat.
3. **Last heard** shows exactly what Johnny transcribed. A full log is in `%APPDATA%\Johnny\logs\voice.log`.

## Privacy and safety
- Everything stays in `%APPDATA%\Johnny`: settings, memory database, skills and routines. Nothing is sent online.
- Typing is **never recorded** in windows whose title matches a privacy keyword (password, bank, login…).
- PowerShell commands, closing apps, typing text and replaying skills ask for permission by default.
- *Settings › Forget everything* wipes conversation history, remembered facts and activity history.

## Building a standalone `JohnnySetup.exe`
```bash
powershell -ExecutionPolicy Bypass -File installer\build_exe.ps1
```
This builds `dist\Johnny\Johnny.exe` with PyInstaller. If [Inno Setup 6](https://jrsoftware.org/isinfo.php) is installed, it also builds `dist\JohnnySetup.exe`: a normal Windows installer that offers to install Ollama.
 or ***just run Johnnysetup.exe under the dist folder in root***
## Development
`Run Johnny (dev).bat` runs Johnny straight from this folder. Code layout:

| File | Purpose |
|---|---|
| `johnny/setup_wizard.py` | First-run wizard |
| `johnny/app.py` | Main window, voice, Teach mode, Skills, Habits, Settings, tray, hotkeys |
| `johnny/brain.py` | Persona prompt and Ollama tool-calling agent loop |
| `johnny/tools.py` | Everything Johnny can do on the PC (add new tools with `@tool`) |
| `johnny/watcher.py` | Activity observer, recorder, replayer, skill storage |
| `johnny/patterns.py` | Habit detection and routines |
| `johnny/voice.py` | Text-to-speech (SAPI) and speech-to-text (faster-whisper) |
| `johnny/memory.py` | SQLite memory |
| `johnny/ollama_client.py` | Dependency-free Ollama API client |

**Tip:** models with tool calling (Qwen 3, Llama 3.1+, Mistral, Gemma 4) control the PC most reliably.

## Branding
Every logo asset (app icon, in-app logo, tray icon, installer images) is generated from **`assets/logo_source.jpg`**. To change the logo, replace that file with a square image (512 px or larger looks best) and rebuild. `build_exe.ps1` runs `installer/make_assets.py` automatically.
