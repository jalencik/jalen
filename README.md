# Jalen

A voice assistant for Windows that listens, answers, and helps you get things done on your computer. Say **"Hey Jalen"** to start, or use text mode when you prefer typing.

Common commands run through a local intent router. More involved requests go to Claude through the Claude Agent SDK. Actions pass through a safety gate before they run.

## What Jalen can do

- **Control your computer:** open apps and files, manage windows, type, click, and read the screen.
- **Work in your browser:** find information, manage tabs, fill forms, and use a Chrome extension to interact with pages.
- **Handle communication:** read Gmail, manage Calendar events, draft emails, and work with your personal Telegram account or a Telegram bot.
- **Help with writing and coding:** prepare drafts, start coding jobs, and hand work to other assistants.
- **Diagnose Windows problems:** check disk space, startup apps, network settings, and system health.
- **Remember useful information:** store notes and keep credentials in an encrypted vault with site-specific permissions.

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
| `.\jalen.ps1 check` | Check dependencies, models, and connected accounts |
| `.\jalen.ps1 why` | Explain the last shutdown or crash |
| `.\jalen.ps1 can` | List tools and safety tiers |
| `.\jalen.ps1 hotkeys` | Install autostart and global shortcuts |
| `.\jalen.ps1 browser` | Diagnose the Chrome connection |
| `.\jalen.ps1 vault` | Set up the encrypted vault |

After installing hotkeys, **Ctrl+Alt+J** wakes Jalen and **Ctrl+Alt+K** stops it. Say **"Jalen, quit"** to exit voice mode.

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
