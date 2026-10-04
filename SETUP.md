# Setting up Jalen

The assistant and Python package are called Jalen. Shared settings live in `config/jalen.yaml`; your personal settings belong in `config/user.yaml`.

Written for someone who hasn't done this before. Do the steps in order. After Step 9 Jalen will talk to you. Everything after that adds accounts and abilities.

If something breaks, stop Jalen (`.\jalen.ps1 stop`) and run `.\jalen.ps1 check` from the project folder. It says what is missing in plain words. `.\jalen.ps1 todo` lists what still needs you.

## Three rules for one laptop

Read these once. Breaking them causes problems that are hard to trace.

- **One Jalen at a time, in any mode.** Voice, text and the Telegram bot are modes of the same Jalen. A second launch is refused while one is running.
- **One program at a time on your Telegram login.** The personal Telegram session file (`data\telegram_user.session`) may be used by only one program at once. A second one can get the session killed or corrupted. So stop Jalen before you run `connect_telegram.py` (even with `--status`) or `.\jalen.ps1 check`.
- **Always run Jalen from the same folder.** The "only one Jalen" lock, the autostart entries and the Chrome extension link all belong to the folder they were set up from. Do not run Jalen from a second copy, or from a git worktree under `.claude\worktrees\`. Those copies have no `.venv`, `.env`, `data\` or `models\`.

---

## Step 1 — Install Python 3.13 (64-bit), Chrome, and microphone access

Go to <https://www.python.org/downloads/windows/> and pick a **3.13.x** release, then **Windows installer (64-bit)**. Do not just take the newest Python at the top of python.org. `requirements.txt` is written for 3.12 or 3.13, the working machine runs 3.13.15, and newer versions are untested.

On the first screen tick **"Add python.exe to PATH"**, and leave the **py launcher** ticked.

Check it in a new PowerShell window:

```powershell
py -3.13 --version
```

You want `Python 3.13.x`. 32-bit Python will not work, because onnxruntime has no 32-bit wheels.

Also install **Google Chrome**, because the Jalen extension and the ChatGPT/Gemini hand-off both use it.

Then allow microphone access. In **Settings → Privacy → Microphone** (Windows 11: **Settings → Privacy & security → Microphone**), turn on **Allow desktop apps to access your microphone** (Windows 11: **Let desktop apps access your microphone**).

---

## Step 2 — Put the project in place

There is no online copy to clone. Bring the **code**, never the original owner's private files.

**Do not copy the whole folder.** The working folder also holds files that belong to its owner alone: `.env` (his keys and his Claude sign-in), `data\` (his password vault, his Telegram and Google logins, his audit log), `client_secret.json`, `config\user.yaml` (his name and accounts), plus `.venv`, `native_host_manifest.json` and `jalen_bridge_host.bat`, which only work on his machine. A copy of `data\telegram_user.session` *is* his Telegram account.

The safe way is to let git pack only the code. On the machine that has the project, in its folder:

```powershell
git archive --format=zip -o $HOME\Desktop\jalen-code.zip HEAD
```

That zip holds exactly the files git tracks, and none of the private ones above. Unzip it on the new laptop, then copy one more file across by hand: the wake-word model **`models\hey_jalen.onnx`** (about 0.9 MB). Git leaves it out and it cannot be downloaded; Step 5 has the alternatives.

Any folder works, as long as you always run Jalen from the same one. The other docs assume this one:

```powershell
cd C:\path\to\jalen
```

If your Desktop is synced by OneDrive (the path shows `OneDrive\Desktop`), put the project in a folder OneDrive does not sync instead, for example `C:\Projects\jalen`, and run everything from there. OneDrive would otherwise upload the 1.2 GB `.venv` and lock files while Jalen uses them.

Run Jalen only from this folder (see "Three rules for one laptop" above).

---

## Step 3 — Let PowerShell run the launcher, and create the virtual environment

Jalen starts from a PowerShell script, `jalen.ps1`. A fresh Windows refuses to run scripts, so allow your own once. No admin is needed:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
Unblock-File .\jalen.ps1      # only if the folder came from a download or a zip
```

Then create the project's own Python:

```powershell
py -3.13 -m venv .venv
```

You do not need to activate it. Every command below calls `.venv\Scripts\python.exe` directly, and `jalen.ps1` finds it on its own.

---

## Step 4 — Install the packages

```powershell
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Allow about 1.5 GB of disk. The finished `.venv` is about 1.2 GB on the working machine (measured 2026-10-01), and the Claude package alone is a ~105 MB download because the Claude Code program the brain runs is inside it. Lines that say WARNING are fine; only ERROR matters.

If pip says *"No matching distribution found for claude-agent-sdk"*, your Python is most likely 32-bit (Step 1). `requirements.txt` refuses on purpose any build of that package that lacks the program inside it.

---

## Step 5 — Download the local models

```powershell
.venv\Scripts\python.exe scripts\download_models.py
```

About 8 MB, and it needs the internet. It fetches the voice detector (Silero VAD) and the pretrained "hey jarvis" wake model with its two feature models.

It does **not** fetch Jalen's own wake model, `models\hey_jalen.onnx`. That model was trained on the working machine and cannot be downloaded. Voice mode loads it first and stops at start-up without it ("Could not find pretrained model for model name 'hey_jalen'"). `.\jalen.ps1 check` does not look for this file. Pick one:

- **Copy it** from the working machine into `models\` (Step 2). This is the best option.
- **Use "Hey Jarvis" for now.** Add these lines to `config\user.yaml` (Step 8 explains that file) as a block of their own, starting at the left margin, and say "Hey Jarvis" instead:

  ```yaml
  wake:
    model: ["hey_jarvis_v0.1"]
  ```

  (`.\jalen.ps1 setup` rewrites `config\user.yaml`, so add these lines back after running it.)
- **Train it again**, which takes about 40 minutes. The training script needs one package that is not in `requirements.txt`:

  ```powershell
  .venv\Scripts\python.exe -m pip install onnx
  .venv\Scripts\python.exe scripts\train_wake_word.py all
  ```

Text mode (Step 9) needs no wake model.

Two more models download on their own the first time they are used: the offline speech fallback and the memory model. Keep the internet on for the first run.

---

## Step 6 — Make your `.env` and get a Groq key

`.env` holds your keys. It stays in this folder and is never committed.

```powershell
copy .env.example .env
notepad .env
```

The one key everybody needs is for speech recognition, and it is free:

1. Go to <https://console.groq.com> and sign in.
2. **API Keys → Create API Key**, then copy it. You only see it once.
3. Paste it after `GROQ_API_KEY=` with no spaces and no quotes. Save.

What each line in `.env` is for:

| Key | What it is for | Needed? |
|---|---|---|
| `GROQ_API_KEY` | Speech to text (Groq Whisper) | Yes. `check` marks it blocking; without it Jalen falls back to a slower offline model |
| `CLAUDE_CODE_OAUTH_TOKEN` | The brain's sign-in to your Claude subscription | Yes, Step 7 |
| `ANTHROPIC_API_KEY` | Nothing. Leave it **empty** | Must stay empty |
| `CLAUDE_CLI_PATH` | Run a different `claude.exe` from the one inside the SDK | No, leave empty |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS` | The Telegram bot (Step 11) | Optional; the two go together |
| `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` | Your own Telegram account (Step 12) | Optional |
| `GEMINI_API_KEY` | Handing a task to Gemini when you ask | Optional |
| `HERMES_API_KEY` or `OPENROUTER_API_KEY` | "ask Hermes to ..." through OpenRouter (set one, not both) | Optional |
| `OPENAI_API_KEY` | "ask ChatGPT to ..." through the OpenAI API | Optional |
| `GITHUB_TOKEN`, `NOTION_TOKEN` | Nothing yet: no tool reads them, and both integrations are off | No |

[CREDENTIALS.md](CREDENTIALS.md) says where to get each one.

Some comments inside `.env.example` are older than this guide (for example, it says to run plain `claude setup-token`). Where they disagree, follow this guide.

---

## Step 7 — Sign the brain in to Claude

Jalen thinks with your Claude subscription (Pro or Max), not with paid API credits. You do not need to install Claude Code. The brain runs the copy that came inside the `claude-agent-sdk` package in Step 4, and that is the copy to sign in with.

1. In the project folder run:

   ```powershell
   .venv\Lib\site-packages\claude_agent_sdk\_bundled\claude.exe setup-token
   ```

2. A browser opens. Sign in with the account that has your Claude subscription.
3. It prints a long token. Nothing saves it for you, so paste it into `.env`:

   ```
   CLAUDE_CODE_OAUTH_TOKEN=<paste the token here>
   ```

4. Save. Jalen reads it when he starts.

**Leave `ANTHROPIC_API_KEY` empty, both in `.env` and in Windows' own environment variables.** A value in either place moves the brain off your subscription and onto per-token billing. `.env` does not override a variable Windows already has, so an empty line in `.env` does not cancel one set in Windows.

---

## Step 8 — Make it yours

`config\jalen.yaml` holds the defaults, and it ships with the original owner's personal values. Do not edit it. Put your own values in **`config\user.yaml`**, which overlays it key by key and is never committed. Lists in `user.yaml` replace the list in `jalen.yaml`; they are not added to it.

These five keys carry the original owner's values and should be yours:

| Key | What it does |
|---|---|
| `identity.user_name` | Your name |
| `web.google_account` | The Google address that Jalen's own Chrome signs in with |
| `telegram.personal.send_without_asking_to` | Telegram chats Jalen may post to without asking first. Start with an empty list |
| `research.contact` | A link or email that Jalen gives to websites that refuse unnamed readers. Use one you are happy to share, or leave it empty |
| `personal.channel_handle` | The handle your community posts sign off with. **Left empty, posts sign off with the original owner's handle**, so set it if you will post |

A starting `config\user.yaml` (one file; every later step that mentions it adds to this same file):

```yaml
identity:
  user_name: "<your name>"
web:
  google_account: "<your Google address>"
telegram:
  personal:
    send_without_asking_to: []
research:
  contact: ""
personal:
  channel_handle: "<your channel handle, or leave the quotes empty if you won't post>"
```

You can also run the guided setup, which asks your name and writes `config\user.yaml` with an empty send-without-asking list. It can also add keys to `.env` and run the Google and Telegram sign-ins (Steps 10 and 12):

```powershell
.\jalen.ps1 setup
```

Two warnings about it.

- **Its brain step looks for a different `claude` program**, one installed on its own. On a laptop set up with this guide it prints `[XX] the claude CLI is not on PATH`. Ignore that and install nothing: Step 7 already signed in the copy the brain really uses. If it offers to sign in, say **No**, because it would not put the token in `.env`.
- **It rewrites the whole of `config\user.yaml` every time it runs**, so everything you added by hand is lost (`web`, `research`, `personal`, `wake`, `telegram.personal.enabled`, `audio.input_device`). Copy the file aside first (`copy config\user.yaml config\user.yaml.bak`) and put your lines back afterwards.

---

## Step 9 — First words

Start with text. It needs no microphone and no wake model:

```powershell
.\jalen.ps1 text
```

Type `explain in one sentence why the sky is blue`. A real answer means the brain is signed in. Type `quit` to leave.

Then voice:

```powershell
.\jalen.ps1 start
```

Say **"Hey Jalen"** ("Hey Jarvis" works too; if you chose the Hey Jarvis option in Step 5, say **"Hey Jarvis"**), wait for the orb to turn blue, then say **"what time is it?"**. To stop, say **"Jalen, quit"** or press Ctrl+C.

If either one fails, stop it and run `.\jalen.ps1 check`. Fix every `[XX]` line except two that stay until later steps: "Google is not connected" (Step 10) and "Personal Telegram isn't signed in" (Step 12). Lines marked `[--]` are optional extras.

Gmail and Calendar are optional, but `check` always counts a missing Google login as a problem. If you skip Step 10 for good, that one `[XX]` line stays, and it is safe to ignore.

---

## Step 10 — Gmail and Calendar

The fiddliest step. Read it carefully; there's one trap that wastes people's weekends.

1. Go to <https://console.cloud.google.com> and create a project (any name, for example `Jalen`).
2. **APIs & Services → Library** → enable **Gmail API** and **Google Calendar API**.
3. **APIs & Services → OAuth consent screen** → choose **External** → fill in an app name and your email.
4. **The trap: go to Audience and click "PUBLISH APP".**

   While the app is in *Testing*, Google expires your login **every 7 days** and Gmail and Calendar stop working. Adding yourself as a test user does **not** fix it — that *is* Testing mode. Publishing does.

   **Do not** submit for verification. You don't need it for personal use. You'll see a "Google hasn't verified this app" screen once — click **Advanced → Go to <your app name> (unsafe)**. That is expected for your own app.

5. **Credentials → Create Credentials → OAuth client ID → Desktop app.**
6. Download the JSON and put it in the project folder (next to `run.py`) as `client_secret.json`. Windows hides file extensions, so renaming it in Explorer easily makes `client_secret.json.json`. Use this instead, from the project folder:

   ```powershell
   Move-Item "$HOME\Downloads\client_secret_*.json" .\client_secret.json
   ```
7. If you did not publish the app in item 4, add the Google account you will use under **Audience → Test users**. Otherwise Google refuses the sign-in.
8. Connect, once:

   ```powershell
   .venv\Scripts\python.exe scripts\connect_google.py
   ```

   Your browser opens. Pick the account and press **Allow**. Jalen never sees your password. He asks for four permissions only: read mail, create drafts, send mail (he always asks out loud before sending), and calendar events. The login is stored in `data\google_token.json`.
9. Check which account got connected:

   ```powershell
   .venv\Scripts\python.exe scripts\connect_google.py --status
   ```

   If it is the wrong account, run `connect_google.py --logout`, then connect again.

Gmail and Calendar already ship switched on in `config\jalen.yaml`, so there is nothing to edit. Jalen never opens a sign-in browser by himself. When the login stops working he tells you, and you run `connect_google.py` again.

> **Changing your Google password ends the login.** If Gmail suddenly stops working, run `connect_google.py` again.

---

## Step 11 — The Telegram bot (control Jalen from your phone, optional)

1. Open Telegram, search **@BotFather**.
2. Send `/newbot`. Give it any name (for example `Jalen`) and a username ending in `bot`, such as `<yourname>_jalen_bot`.
3. BotFather replies with a token. Put it in `.env`:

   ```
   TELEGRAM_BOT_TOKEN=<the token from BotFather>
   ```

4. Now get your own numeric ID: message **@userinfobot** and it replies with a number. Put it in `.env` (several IDs: separate them with commas):

   ```
   TELEGRAM_ALLOWED_USER_IDS=<your numeric id>
   ```

**This is a security control, not a convenience setting.** The bot can drive your desktop, and anyone whose ID is not on that list is refused. If you leave the list empty, `.\jalen.ps1 telegram` refuses to start at all.

To use the bot, start Jalen in Telegram mode instead of voice:

```powershell
.\jalen.ps1 telegram
```

It is one or the other. Voice, text and Telegram are modes of the same single Jalen, and a second launch is refused while one is running. Stop the running one first with `.\jalen.ps1 stop`.

---

## Step 12 — Your personal Telegram account (optional)

This lets Jalen read your real chats and send messages as you.

1. Go to <https://my.telegram.org> → **API development tools**.
2. Create an app. You get an **api_id** (a number) and an **api_hash**.
3. Put them in `.env` as `TELEGRAM_API_ID=` and `TELEGRAM_API_HASH=`. They identify the app. They are not a login.
4. With Jalen stopped, sign in once:

   ```powershell
   .venv\Scripts\python.exe scripts\connect_telegram.py
   ```

   It asks for your phone number with the country code (`+...`), the code Telegram sends to your other Telegram devices, and your two-step password if you have one (typed hidden). None of these are stored.
5. Check it, still with Jalen stopped:

   ```powershell
   .venv\Scripts\python.exe scripts\connect_telegram.py --status
   ```

Personal Telegram already ships switched on in `config\jalen.yaml`, so there is nothing to edit. If you skip this step, add `enabled: false` under the `telegram:` → `personal:` lines that `config\user.yaml` already has (Step 8), so that `check` stops counting it as a problem. Do not add a second `telegram:` block: YAML keeps only the last one, silently. The merged part looks like this:

```yaml
telegram:
  personal:
    send_without_asking_to: []
    enabled: false
```

**Read this before you sign in.** This logs in as *you*, not as a bot. Telegram's terms allow it, but heavy automation gets accounts banned, and there's no appeal process worth relying on. Reading your own chats and sending the occasional message is low risk; bulk messaging is not. The file this creates, `data\telegram_user.session`, is a complete login to your account, and anyone who copies it is you. Never commit it, never share it.

**One program at a time.** Only one program may use that session file at once, and a second one can get the session killed or corrupted. So stop Jalen (`.\jalen.ps1 stop`) before you run `connect_telegram.py` (including `--status`) or `.\jalen.ps1 check`, and never run Jalen with the same session on two machines.

---

## Step 13 — Your everyday Chrome (the Jalen extension, optional)

This lets Jalen see the tab you are looking at in your own Chrome and fill ordinary forms there. The full guide is [docs\CHROME_EXTENSION_SETUP.md](docs/CHROME_EXTENSION_SETUP.md). In short:

1. From this project folder (the one Jalen runs from), register the bridge. No argument is needed:

   ```powershell
   .venv\Scripts\python.exe scripts\install_extension.py
   ```

   It writes a small launcher and a manifest into this folder and registers them for your Windows user only. The launcher always starts this folder's `.venv`, which is one more reason to run Jalen from here only.
2. In Chrome open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and choose the `browser_extension` folder inside the project.
3. Quit Chrome completely and open it again.
4. Start Jalen (`.\jalen.ps1 start` or `.\jalen.ps1 text`). Click the Jalen orb in the Chrome toolbar. A newly loaded extension hides in the puzzle-piece **Extensions** menu at first: open it and pin Jalen. The panel should say **connected**.

If it says not connected, run `.\jalen.ps1 browser`. It checks each link in the chain and names the broken one.

---

## Step 14 — File search (optional)

Install **Everything** from <https://www.voidtools.com> and let it index. Jalen does not talk to Everything directly. He runs its command-line tool **`es.exe`**, which is a separate download on the same site ("Command-line Interface"). Put `es.exe` in `C:\Program Files\Everything\`, the path set as `index.everything_cli` in `config\jalen.yaml`. Everything itself must be running. Without them, "find that file" falls back to a slower search of Documents, Downloads and Desktop.

---

## Step 15 — Start with Windows, and the hotkeys

Run this from the project folder. The entries it creates point at whichever folder it was run from.

```powershell
.\jalen.ps1 hotkeys
```

(This is the same as `.venv\Scripts\python.exe scripts\install_autostart.py`.) It puts two entries in your Startup folder:

- **Jalen** starts at login, **muted**. He listens but stays silent until you say "Hey Jalen, unmute" or press **Ctrl+Alt+J**.
- **Jalen Hotkeys** is a small separate listener, and it also starts right away:
  - **Ctrl+Alt+J** wakes him. It starts him, unmuted, if he is not running, and un-mutes or un-pauses him if he is.
  - **Ctrl+Alt+K** is the kill switch. It stops Jalen.

  If another program already owns one of those keys, the listener takes the next free one (Ctrl+Shift+J or Ctrl+Alt+F9; Ctrl+Shift+K or Ctrl+Alt+F10) and writes which one it took to `data\hotkeys.log`.

The script's own closing message still says "Ctrl+Alt+Space stops him". That is out of date: the key is Ctrl+Alt+K.

To see what is installed:

```powershell
.venv\Scripts\python.exe scripts\install_autostart.py --status
```

To remove both: `.\jalen.ps1 uninstall`.

---

## Step 16 — Full check

1. Stop Jalen: `.\jalen.ps1 stop`.
2. Run the diagnostic:

   ```powershell
   .\jalen.ps1 check
   ```

   Know what it does: it makes one real round trip to Claude, asks Google which account is connected, and opens your Telegram session to see who is signed in. That is why Jalen must be stopped first.

   When everything is ready it ends with **"All good — start it with: .\jalen.ps1"**. Otherwise it counts the blocking problems, each with its fix.
3. See what is still optional:

   ```powershell
   .\jalen.ps1 todo
   ```

   The usual extras are the password vault (`.\jalen.ps1 vault`), teaching the wake word your own voice (`.\jalen.ps1 voice`, about 15 minutes), and walking the end-to-end flows with you present (`.\jalen.ps1 rehearse`).

Ways to check one thing at a time:

| Command | What it tells you |
|---|---|
| `.\jalen.ps1 status` | Is Jalen running, and in which mode? |
| `.venv\Scripts\python.exe scripts\connect_google.py --status` | Which Google account is connected |
| `.venv\Scripts\python.exe scripts\connect_telegram.py --status` | Which Telegram account is signed in (stop Jalen first) |
| `.venv\Scripts\python.exe scripts\install_autostart.py --status` | Whether autostart and the hotkeys are installed |
| `.\jalen.ps1 browser` | Why the Chrome extension says "not connected" |

---

## Starting and stopping

The simplest way is the launcher, which can't be typed wrong. From the project folder:

```powershell
.\jalen.ps1              # start listening (voice, unmuted)
.\jalen.ps1 stop         # stop it
.\jalen.ps1 restart      # stop, then start fresh
.\jalen.ps1 status       # is it running?
.\jalen.ps1 text         # type instead of talk
.\jalen.ps1 telegram     # run as the Telegram bot instead of voice
.\jalen.ps1 check        # diagnostics (stop Jalen first)
.\jalen.ps1 why          # why did it stop last time?
.\jalen.ps1 todo         # what still needs you
.\jalen.ps1 setup        # guided setup: your name, keys, Google, Telegram
```

**Why not just `run.py --unmuted`?** That fails twice over on Windows:

- PowerShell will not run a script from the current folder without the `.\` prefix, so you get *"The term 'run.py' is not recognized"*.
- Bare `python` is a **different** Python install without this project's packages, so it dies with *"No module named 'numpy'"*.

`.\jalen.ps1` handles both.

### The longer way

Always use the project's `.venv` (see the troubleshooting table below if you get a `ModuleNotFoundError`):

```powershell
.venv\Scripts\python.exe run.py --unmuted     # voice
.venv\Scripts\python.exe run.py --text        # typed, no mic
.venv\Scripts\python.exe run.py --telegram    # control from your phone
.venv\Scripts\python.exe run.py --check       # diagnostics
```

To stop it — you never need Task Manager:

```powershell
.venv\Scripts\python.exe run.py --stop        # stop it
.venv\Scripts\python.exe run.py --status      # is it running?
.venv\Scripts\python.exe run.py --restart     # stop, then start fresh
```

Or just say so: **"Jalen, quit"**, **"Jalen, pause"** (stops listening but stays running; say "Hey Jalen" to bring him back), **"Jalen, resume"**. "Jarvis" works anywhere you would say "Jalen".

Only one Jalen can run at a time, in any mode: voice, text and Telegram share one lock. A second launch says which one is already running and exits. It does not start a second copy that fights the first for your microphone and your Telegram session. The lock lives in this folder's `data\`, which is why Jalen must always be started from the same folder.

## Everyday use

| You say | What happens |
|---|---|
| "Hey Jalen" (or "Hey Jarvis") | Wakes up, orb turns blue |
| "that's all" / "goodbye" / "quit" | Says "See you, Boss." and shuts Jalen down. Ctrl+Alt+J or `.\jalen.ps1` starts him again |
| "stop" | Kill switch: cuts him off mid-sentence |
| `Ctrl+Alt+K` | Stops Jalen completely, from the keyboard (needs Step 15; `data\hotkeys.log` names the key if Ctrl+Alt+K was taken) |
| "mute" / "unmute" | He stops talking but keeps listening / he talks again |
| "pause" | Stops listening but stays running; "Hey Jalen" brings him back |
| "private mode" | Nothing logged, nothing remembered ("private mode off" ends it) |
| "paranoid mode on" / "paranoid mode off" | Asks before anything that changes something / back to the default: asks only before things that can't be undone |
| "what did you do today?" | Reads back a digest of today's audit log |

---

## When something goes wrong

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: No module named 'numpy'` (or any package) even though setup succeeded | The command used a bare `python`, which is your system Python, not the project's one (this guide never activates the `.venv`, so bare `python` is always the wrong one). Use `.\jalen.ps1`, or call `.venv\Scripts\python.exe ...` with the full path. |
| Doesn't hear "Hey Jalen" | Run `.\jalen.ps1 check`: it lists your microphones, and you can pin one with `audio.input_device` in `config\user.yaml`. If the mic is right, the model has only ever heard synthetic voices, so teach it yours with `.\jalen.ps1 voice` (about 15 minutes). `wake.threshold` already ships lowered to 0.5 for this reason. |
| Wakes when nobody said his name | Raise `wake.threshold` back toward 0.7. The measured table is in the comment above it in `config\jalen.yaml`. |
| Voice mode stops at start with "Could not find pretrained model for model name 'hey_jalen'" | `models\hey_jalen.onnx` is missing. See Step 5. |
| `tflite-runtime` error | You have openwakeword 0.4.0: `.venv\Scripts\python.exe -m pip install openwakeword==0.6.0` |
| edge-tts gives 403 | `.venv\Scripts\python.exe -m pip install --upgrade edge-tts`. Microsoft rotated a token |
| Gmail and Calendar die after a week | The consent screen is still in Testing mode. Run `.venv\Scripts\python.exe scripts\connect_google.py` again, and publish the app (Step 10, item 4) so it stops happening. |
| Gmail dies suddenly | Did you change your Google password? Run `.venv\Scripts\python.exe scripts\connect_google.py` again. |
| Jalen says the Chrome extension isn't connected | `.\jalen.ps1 browser` checks every link in the chain and names the broken one. Most often Chrome was not fully restarted after Step 13, or Jalen isn't running. |
| Jalen can't read the text of a Chrome window ("read the screen") | Chrome shows page text to screen-reading tools only when it is started with `--force-renderer-accessibility`. |
| `check` says "the brain is NOT signed in to Claude" | Repeat Step 7: run the bundled `claude.exe setup-token`, put the new token in `CLAUDE_CODE_OAUTH_TOKEN` in `.env`, and restart Jalen. |
| It stopped on its own | `.\jalen.ps1 why` |
| Everything is slow | Check RAM in Task Manager — you don't have much spare |

---

## For a developer machine: the tests

```powershell
.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider --ignore=tests\benchmark_latency.py --ignore=tests\benchmark_open.py --ignore=tests\benchmark_phrasing.py
```

Run it to see the current count; it was about 5,245 at commit `833adbb` and it changes as work merges.

On the working machine one failure is expected: `test_overhaul_fixes.py::test_real_typos_and_abbreviations_still_resolve[capcut-True]`, because CapCut is not installed. It is not a code problem. A **fresh** laptop has a few more that are about the machine, not the code:

- tests that need files only a used install has: the password vault (`data\vault.json`, made by `.\jalen.ps1 vault`) and `data\weaknesses.md`;
- the wake-word tests, if `models\hey_jalen.onnx` is missing (Step 5);
- three voice tests that call the internet (`test_stt_roundtrips_through_groq`, `test_barge_in_latency_is_measured`, `test_failure_paths_keep_jalen_alive`). Run them two or three times on their own before believing a failure.

Any other failure is real.
