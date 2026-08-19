# Setting up Jarvis

Written for someone who hasn't done this before. Do the steps in order. Each
one takes a few minutes. You can stop after Step 8 and Jarvis will talk to you —
everything after that adds abilities.

If something breaks, run `python run.py --check` first. It usually tells you
exactly what's wrong.

---

## Step 1 — Install Python 3.13 (64-bit)

Download from <https://www.python.org/downloads/windows/>. Pick **Windows
installer (64-bit)**.

**On the first screen, tick "Add python.exe to PATH".** If you miss it, nothing
below works.

Check it worked — open PowerShell and run:

```powershell
python --version
```

You want `Python 3.13.x`. If it says 3.11 or lower, install the newer one.
32-bit will not work: onnxruntime has no 32-bit wheels.

---

## Step 2 — Put the project somewhere sensible

The repo should already be at `C:\Users\user\Desktop\Jarvis`. Open PowerShell
there:

```powershell
cd C:\Users\user\Desktop\Jarvis
```

---

## Step 3 — Create a virtual environment

This keeps Jarvis's packages separate from everything else on your machine, so
nothing you install later can break it.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

If you get *"running scripts is disabled on this system"*:

```powershell
Set-ExecutionPolicy -Scope Process RemoteSigned
.venv\Scripts\Activate.ps1
```

You'll know it worked when your prompt starts with `(.venv)`. **You need to run
that `Activate` line every time you open a new terminal.**

---

## Step 4 — Install the packages

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

Takes 3–8 minutes. It's downloading around 600 MB. Fine to ignore warnings that
say "WARNING"; only "ERROR" matters.

---

## Step 5 — Download the local models

```powershell
python scripts\download_models.py
```

About 8 MB — the "hey jarvis" wake word and the voice detector.

---

## Step 6 — Connect your Claude subscription

This is what gives Jarvis its brain, using the Pro plan you already pay for
rather than buying API credits.

1. Install Claude Code from <https://claude.com/download> if you don't have it.
2. In PowerShell:

```powershell
claude setup-token
```

3. A browser opens. Log in with the Google account on your Claude Pro plan.
4. Done. The SDK picks the token up automatically.

**Leave `ANTHROPIC_API_KEY` empty in `.env`.** If you set it, you'll be billed
per token instead of using your subscription.

---

## Step 7 — Get a Groq key (speech recognition, free)

1. Go to <https://console.groq.com>
2. Sign in with Google.
3. **API Keys** → **Create API Key** → copy it. You only see it once.

Now make your `.env` file:

```powershell
copy .env.example .env
notepad .env
```

Paste the key after `GROQ_API_KEY=`, with no spaces and no quotes:

```
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxx
```

Save and close.

---

## Step 8 — Check everything, then talk to it

```powershell
python run.py --check
```

Fix anything marked `[XX]`. Warnings marked `[--]` are optional features you
haven't set up yet — ignore them for now.

Then:

```powershell
python run.py --unmuted
```

Say **"Hey Jarvis"**, wait for the orb to turn blue, then say **"what time is
it?"**

That's it. It's alive.

> Prefer to test without a microphone? `python run.py --text` gives you a typed
> conversation with the same brain and the same safety rules.

---

## Step 9 — The Telegram bot (control Jarvis from your phone)

1. Open Telegram, search **@BotFather**.
2. Send `/newbot`. Name it `Jarvis`, username something like `oktam_jarvis_bot`.
3. BotFather replies with a token — copy it into `.env`:

```
TELEGRAM_BOT_TOKEN=8123456789:AAF...
```

4. Now get your own numeric ID: message **@userinfobot** and it replies with a
   number. Put it in `.env`:

```
TELEGRAM_ALLOWED_USER_IDS=123456789
```

**This is a security control, not a convenience setting.** The bot can drive
your desktop. Anyone whose ID is not on that list gets refused. Leave it empty
and the bot refuses everyone, which is the safe default.

---

## Step 10 — Gmail and Calendar

The fiddliest step. Read it carefully; there's one trap that wastes people's
weekends.

1. Go to <https://console.cloud.google.com> and create a project called `Jarvis`.
2. **APIs & Services → Library** → enable **Gmail API** and **Google Calendar API**.
3. **APIs & Services → OAuth consent screen** → choose **External** → fill in
   app name and your email.
4. **⚠️ The trap: go to Audience and click "PUBLISH APP".**

   While the app is in *Testing*, Google expires your login **every 7 days** and
   Jarvis stops working. Adding yourself as a test user does **not** fix it —
   that *is* Testing mode. Publishing does.

   **Do not** submit for verification. You don't need it for personal use.
   You'll see a "Google hasn't verified this app" screen once — click
   **Advanced → Go to Jarvis (unsafe)**. That's expected for your own app.

5. **Credentials → Create Credentials → OAuth client ID → Desktop app.**
6. Download the JSON, rename it `client_secret.json`, put it in the Jarvis folder.
7. In `config/jarvis.yaml` set `integrations.gmail.enabled: true` and
   `integrations.calendar.enabled: true`.

First time Jarvis touches Gmail it opens a browser to authorise. Once only.

> One thing to expect: **changing your Google password invalidates the token.**
> If Gmail suddenly stops working, that's usually why — just re-authorise.

---

## Step 11 — Your personal Telegram account (optional)

This lets Jarvis read your real chats and send messages as you.

1. Go to <https://my.telegram.org> → **API development tools**.
2. Create an app. You get an **api_id** and **api_hash**.
3. Put both in `.env`, then set `telegram.personal.enabled: true` in the config.

**Read this before you enable it.** This logs in as *you*, not as a bot.
Telegram's terms allow it, but heavy automation gets accounts banned, and
there's no appeal process worth relying on. Reading your own chats and sending
the occasional message is low risk. Bulk messaging is not. Also: the `.session`
file this creates is a complete login to your account — anyone who copies it is
you. Never commit it, never share it.

---

## Step 12 — File search

Install **Everything** from <https://www.voidtools.com> (free, tiny). Let it
index in the background. Jarvis uses it for instant "where is that file"
answers. Without it, file search falls back to a slower method.

---

## Step 13 — Start automatically with Windows

```powershell
python scripts\install_autostart.py
```

Starts muted, as you asked. Say "unmute" or press the tray icon to wake it.

---

## Starting and stopping

Always use the project's `.venv` (see the troubleshooting table below if you
get a `ModuleNotFoundError`):

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

Or just say so: **"Jarvis, quit"**, **"Jarvis, pause"** (stops listening but
stays running — say "Hey Jarvis" to come back), **"Jarvis, resume"**.

Only one Jarvis can run at a time. A second launch tells you one is already
running and exits, rather than quietly starting a second copy that fights the
first one for your microphone.

## Everyday use

| You say | What happens |
|---|---|
| "Hey Jarvis" | Wakes up, orb turns blue |
| "that's all" | Ends the conversation |
| "stop" | Kill switch — cuts him off mid-sentence |
| `Ctrl+Alt+Space` | Same kill switch, from the keyboard |
| "mute" | He stops talking but keeps listening |
| "private mode" | Nothing logged, nothing remembered |
| "paranoid mode off" | Stops asking permission for reversible things |
| "what did you do today?" | Shows the audit log |

---

## When something goes wrong

| Symptom | Cause |
|---|---|
| `ModuleNotFoundError: No module named 'numpy'` (or any package) even though setup succeeded | This terminal's `python` isn't the project's `.venv` one. Every *new* terminal starts without it — you have to run `.venv\Scripts\Activate.ps1` again each time (Step 3), or skip activation entirely and call `.venv\Scripts\python.exe run.py ...` directly. Check which one you're running with: `Get-Command python \| Select-Object Source` should print a path inside `...\jarvis\.venv\Scripts\`. If it prints anything else (e.g. `AppData\Local\Programs\Python\...`), that's the bug — activate, or use the full `.venv` path. |
| Doesn't hear "Hey Jarvis" | Lower `wake.threshold` to 0.45 in the config |
| Triggers when the TV is on | Raise it to 0.65 |
| `tflite-runtime` error | You have openwakeword 0.4.0 — `pip install openwakeword==0.6.0` |
| edge-tts gives 403 | `pip install --upgrade edge-tts` — Microsoft rotated a token |
| Gmail dies after a week | You're in Testing mode — publish the app (Step 10.4) |
| Gmail dies suddenly | Did you change your Google password? Re-authorise |
| Chrome control does nothing | Launch Chrome with `--force-renderer-accessibility` |
| Brain errors | Run `claude setup-token` again |
| Everything is slow | Check RAM in Task Manager — you don't have much spare |
