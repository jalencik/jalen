# Jalen

A voice assistant for Windows that listens, answers, and helps you get things done on your computer. Say **"Hey Jalen"** to start, or use text mode when you prefer typing.

Common commands run through a local intent router. More involved requests go to Claude through the Claude Agent SDK. Actions pass through a safety gate before they run.

## What Jalen can do

- **Control your computer:** open apps and files, manage windows, type, click, and read the screen.
- **Work in your browser:** find information, manage tabs, fill forms, and use a Chrome extension to interact with pages.
- **Handle communication:** read Gmail, manage Calendar events, draft emails, and work with your personal Telegram account or a Telegram bot. On Telegram it reads, searches, drafts and sends, and acts on messages already sent: edit, delete, forward, pin and react.
- **Help with writing and coding:** prepare drafts, start coding jobs, and hand work to other assistants.
- **Diagnose Windows problems:** check disk space, startup apps, network settings, and system health.
- **Remember useful information:** store notes and keep credentials in an encrypted vault with site-specific permissions. The conversation itself carries on after a restart (up to 12 hours later); say "new conversation" to let it go.

Jalen is under active development. Run `.\jalen.ps1 can` for the current tool list and safety tiers.

## Requirements

- Windows 10 or 11.
- 64-bit Python 3.12 or 3.13.
- A microphone for voice mode.
- Claude authentication for the assistant's reasoning.
- A Groq API key for cloud speech recognition; a local speech model provides a fallback.

Google, Telegram, and the browser extension are optional integrations you can configure for your workflow.

## Quick start

```powershell
git clone https://github.com/jalencik/jalen.git
cd jalen
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.venv\Scripts\python.exe scripts\download_models.py
.venv\Scripts\python.exe scripts\onboard.py
```

The setup guide helps configure your name, keys, and integrations. Authenticate Claude using the bundled CLI, then save the token in `.env` as `CLAUDE_CODE_OAUTH_TOKEN`:

```powershell
.venv\Lib\site-packages\claude_agent_sdk\_bundled\claude.exe setup-token
```

For **"Hey Jalen"** voice activation, build the custom wake model separately:

```powershell
.venv\Scripts\python.exe -m pip install onnx
.venv\Scripts\python.exe scripts\train_wake_word.py all
```

The generated `models\hey_jalen.onnx` is local and is not included in the repository. Training uses synthetic speech and requires a network connection. You can start text mode before completing voice setup.

```powershell
.\jalen.ps1 text    # type your requests
.\jalen.ps1         # voice mode
```

See [SETUP.md](SETUP.md) for the full walkthrough, including PowerShell script permissions and optional integrations.

## Everyday commands

| Command | Purpose |
|---|---|
| `.\jalen.ps1` | Start voice mode |
| `.\jalen.ps1 text` | Start text mode |
| `.\jalen.ps1 status` / `stop` / `restart` | Manage the running instance |
| `.\jalen.ps1 setup` | Run guided setup |
| `.\jalen.ps1 todo` | Show remaining setup tasks |
| `.\jalen.ps1 check` | Check dependencies, models, connected accounts, limits, and lockdown |
| `.\jalen.ps1 why` | Explain the last shutdown or crash |
| `.\jalen.ps1 can` | List tools and safety tiers |
| `.\jalen.ps1 hotkeys` | Install autostart and global shortcuts |
| `.\jalen.ps1 browser` | Diagnose the Chrome connection |
| `.\jalen.ps1 vault` | Set up the encrypted vault |

After installing hotkeys, **Ctrl+Alt+J** wakes Jalen and **Ctrl+Alt+K** stops it. Say **"Jalen, quit"** to exit voice mode.

**Ctrl+Alt+K** silences Jalen the moment you press it and ends everything it was doing. A fresh Jalen then loads in the background in standby: microphone closed, nothing on screen. The next **Ctrl+Alt+J** only has to open the microphone and you can talk straight away, with no "Hey Jalen" needed. Before this, K ended the process, so J had to start everything from cold, which took about a minute. Press **Ctrl+Alt+K** again while Jalen is in standby to end it completely. `startup.standby_after_kill: false` in `config/jalen.yaml` brings back the old behaviour. The first start after logging in, or after K twice, still loads from cold. Each start writes a "microphone open" row to the audit log with how long it took.

**While Jalen is talking, only "Hey Jalen" interrupts it.** The TV, someone else talking or a cough no longer cuts it off mid-sentence; Ctrl+Alt+J and Ctrl+Alt+K still do. This is `conversation.barge_in: wake_word`. Set it to `true` for the old behaviour, where any voice interrupts, or `false` for nothing.

**Ask "what's wrong with you?"** (or "why can't you hear me?", "why aren't you working?") and Jalen says in plain words what is broken right now and what to do about it: an expired Claude sign-in, a microphone that went quiet, speech recognition stuck on the slow offline model, a lockdown, a limit you've hit, mute or pause, or a run that crashed. It answers even when the brain is down. "Diagnose yourself" still runs the full install check.

**The microphone reopens itself.** If it stops delivering sound (unplugged headset, driver reset, another app taking it), Jalen reopens it after ten seconds, trying the default device if yours is gone, and tells you whether it's back or still dead.

**It doesn't go silent on a long task.** After "Give me a second.", a turn that is still working without saying anything gets "Still working on it." at 20 seconds, then at 45 and 90. It never talks over a question it is waiting on you to answer.

**Memory.** Jalen stores lasting facts you tell it (people, preferences, how you like things done) in its long-term memory, which survives restarts, and looks them up before work they matter to. Nothing it only *read* is stored, and nothing that looks like a password.

Run one Jalen instance at a time. Stop it before checking or reconnecting a personal Telegram session, because only one client may use that session at once.

## Configuration and privacy

- `config/jalen.yaml`: shared defaults for voice, behaviour, integrations, and memory.
- `config/user.yaml`: your personal overrides; ignored by Git.
- `config/safety.yaml`: action tiers and protected paths.
- `.env`: local API keys and authentication tokens; ignored by Git.

Restart Jalen after editing configuration. Account sessions, conversations, local models, and credentials stay outside version control.

| Tier | Behaviour |
|---|---|
| **GREEN** | Runs directly |
| **AMBER** | Announces the action and gives you time to stop it |
| **RED** | Requires explicit confirmation |
| **BLACK** | Refuses protected actions |

Content read from emails, messages, or websites does not authorize actions. Vault secrets are passed to the code that uses them rather than returned as model-visible tool results.

## Limits and "hands off"

You are the final commander. The tiers say what *kind* of action is allowed; the limits say how *many*.

- **Limits.** The `limits:` block of `config/safety.yaml` caps how many emails, messages, deletions, closed windows, delegations and similar Jalen may do in an hour or a day, how many actions it takes on a single request of yours, and how many times it thinks per hour. Past a cap Jalen asks you out loud (for example: "That's your limit on emails: 10 in the last hour. Confirm?"). Your yes lets that one action through and does not raise the cap, and a no stops it asking again until you say something new. A limit set to `on_limit: refuse` never asks. Numbers change in that file only; nothing Jalen can do, by voice or by tool, raises one.
- **Hands off.** Say **"Jalen, hands off"** (or "lock down", "freeze") and Jalen only reads and answers until you say **"Jalen, end the lockdown"**, with its name. It survives a restart, and `.\jalen.ps1 check` says when it is on. Coding jobs already running are not stopped by it; say "cancel the job" for those.
- **What has been used.** Ask "what are my limits?"
- **On your phone.** In Telegram mode a question arrives with Yes and No buttons (Go ahead and Stop under an announcement), and has minutes to be answered rather than seconds.
- **"Say stop if you don't want that."** Any refusal works, not only the word "stop": "no", "don't", "wait". "Go ahead" lets the action run straight away instead of after the pause. If you start speaking before the pause is over, Jalen waits for your words before acting (up to 12 seconds); before, a spoken "stop" reached Jalen only after the two-second pause had already run out. Silence still means it goes after two seconds. Over a limit you are asked once, with the action and the limit in the same question.
- **"... Confirm?"** Say it however you like: "yes", "uh huh", "copy that", "yes, no problem", "go". "Do it later", "scratch that" and "I'll pass" are a no. If you change your mind mid-sentence ("no no, I mean yes"), what you finish on counts. A rambling answer the rules can't read is read by a fast model before you're asked again, and that model is never asked about a clear yes or no, so it can't turn your no into a yes. A "yes" you start before the 20 seconds run out still counts. If Jalen can't tell what you meant, it repeats the question and you get the full time again.
- **Asked twice for what can't be undone.** Deleting a file (it doesn't go to the Recycle Bin), emptying the Recycle Bin, moving a folder (the original is deleted), signing out, submitting a form and a few more: after your yes, Jalen says what will be lost and asks once more. The list is `consequences:` in `config/safety.yaml`; edit it to add or remove actions.

## Development

The Python package lives in **`jalen/`**. To install the development dependencies and run the test suite:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[dev]" --only-binary claude-agent-sdk
.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider --ignore=tests\benchmark_latency.py --ignore=tests\benchmark_open.py --ignore=tests\benchmark_phrasing.py
```

Some tests use live speech services or installed Windows applications; their results depend on your environment.

## Documentation

- [Setup guide](SETUP.md)
- [Credentials and account connections](CREDENTIALS.md)
- [Chrome extension setup](docs/CHROME_EXTENSION_SETUP.md)
- [Architecture](ARCHITECTURE.md)
- [Capabilities](WHAT_JALEN_CAN_DO.md)

## License

The repository is public. Usage and redistribution are governed by [LICENSE](LICENSE).
