# Credentials — what Jalen needs and where each one lives

Every credential goes in one of two places, and never into a chat:

- `.env` at the root of the main checkout (`C:\Users\user\Desktop\Jarvis-setup\jarvis\.env`). Start from the template: `copy .env.example .env`. Jalen reads it once at startup, so restart Jalen after changing it.
- a file that one of the sign-in scripts below writes under `data\`. Those files are full logins. `data\` is git-ignored.

`.\jalen.ps1 check` says which credentials are present and whether Claude, Google and Telegram actually accept them. It never prints a value. This page names credentials only. Never paste a value anywhere except `.env` or the script that asks for it.

The step-by-step setup is [SETUP.md](SETUP.md). `.\jalen.ps1 setup` (`scripts\onboard.py`) walks the Groq, bot, Telegram and Google steps for you, but do its Claude step by hand as below: the script runs the `claude` on PATH, not the one the brain uses, and it does not put the token in `.env`.

---

## Required — speech and the brain

| What | Where to get it | Time | Cost |
|---|---|---|---|
| **Groq API key** — `GROQ_API_KEY` | <https://console.groq.com/keys> → Create API Key (shown once) | 2 min | free tier |
| **Claude sign-in token** — `CLAUDE_CODE_OAUTH_TOKEN` | In PowerShell at the project root run `.venv\Lib\site-packages\claude_agent_sdk\_bundled\claude.exe setup-token`. Your browser opens; sign in with your Claude account. It prints a long token: put it in `.env` as `CLAUDE_CODE_OAUTH_TOKEN=<token>`, then restart Jalen. | 2 min | your Claude subscription |

Groq turns your speech into text and transcribes Telegram voice notes. Without it, Jalen falls back to the slower local moonshine model for speech and cannot transcribe voice notes. `.\jalen.ps1 check` counts a missing key as blocking.

Use the bundled `claude.exe`, not the `claude` on PATH, because the brain runs the bundled one. `.\jalen.ps1 check` shows which binary that is and prints the exact command. Leave `ANTHROPIC_API_KEY` empty: any value there moves the brain off your subscription and onto per-token billing. Leave `CLAUDE_CLI_PATH` empty unless you deliberately want a different native `claude.exe`; a `.cmd` or `.bat` there is refused.

Those two are enough for Jalen to listen and answer. `.\jalen.ps1 check` will still list Google and personal Telegram as blocking until you connect them, because both ship switched on in `config/jarvis.yaml` (`integrations.gmail`, `integrations.calendar`, `telegram.personal.enabled`).

---

## Gmail and Calendar

1. Google Cloud Console → your project → enable **Gmail API** and **Google Calendar API**.
2. OAuth consent screen → External. While it is in **Testing**, add the Google account Jalen should use as a **Test user**, or the sign-in is refused.
3. Credentials → Create credentials → OAuth client ID → **Desktop app**. Download the JSON, name it `client_secret.json`, and put it in the project root (next to `run.py`).
4. In the project root run `.venv\Scripts\python.exe scripts\connect_google.py`. Your browser opens; sign in and press Allow. The script prints **Connected as …**, so check that it is the account you meant. If it is not, run it with `--logout` and connect again. Check the connection later with `--status`.

The script stores a refresh token at `data\google_token.json` with four permissions only: read mail, create drafts, send mail, and calendar events. You can revoke it at myaccount.google.com/permissions.

**Testing mode expires the login every 7 days.** As of 2026-10-01 this project's consent screen is still in Testing, so Gmail and Calendar stop about a week after each connect. Both `.\jalen.ps1 check` (Accounts section) and Jalen itself say so when it happens. Being a Test user does not stop the expiry. The permanent fix is OAuth consent screen → **Publish app** (no verification is needed for personal use); until then, re-run `connect_google.py`. The full walkthrough is SETUP.md Step 10.

---

## Personal Telegram (your own account, not the bot)

1. <https://my.telegram.org> → API development tools → create an app. Put the two values in `.env` as `TELEGRAM_API_ID` (the number) and `TELEGRAM_API_HASH`. They identify the app; they are not a login.
2. **Stop Jalen first** (`.\jalen.ps1 stop`), then run `.venv\Scripts\python.exe scripts\connect_telegram.py`. In the terminal it asks for your phone number in international form, the login code Telegram sends to your other devices, and your two-step password if you have one. None of those is stored.
3. The script writes `data\telegram_user.session`. That file **is** a full login to your account, so never copy, share or commit it. `--status` checks it; `--logout` signs out and deletes it.

With this set up, Jalen reads your chats and sends as you: DMs, posts to your channel and voice messages. The chats listed in `telegram.personal.send_without_asking_to` (in `config/jarvis.yaml`, or your own list in `config/user.yaml`) may be sent to without asking; everything else asks first. Read the warning in SETUP.md Step 12 before relying on it.

**One Telegram client at a time.** Only one process may use the session file. Never start a second Jalen. Stop Jalen before running `connect_telegram.py` (even `--status`) or `.\jalen.ps1 check`, because the check also opens the session to test it.

---

## Optional — the Telegram bot (control Jalen from your phone)

| What | Where | `.env` name |
|---|---|---|
| Bot token | Telegram → @BotFather → `/newbot` (any name) | `TELEGRAM_BOT_TOKEN` |
| Your numeric Telegram user id | Telegram → @userinfobot | `TELEGRAM_ALLOWED_USER_IDS` (several ids: comma-separated) |

Start it with `.\jalen.ps1 telegram`. The bot is a mode of its own, and only one Jalen runs at a time, so it runs instead of voice or text mode, not alongside them. The id list is a security control: the bot can drive the desktop, and it obeys only ids listed in `.env`. If the list is empty, the bot will not start. (`telegram.bot.allowed_user_ids` in `config/jarvis.yaml` is not used.)

---

## Passwords and codes Jalen may type for you (the vault)

Run `.\jalen.ps1 vault` (`scripts\vault_setup.py`). You type a passphrase and each secret with hidden input, and say which site each secret is for. It writes `data\vault.json` (encrypted) and `data\secret_sites.json`. The passphrase is never stored: Jalen asks for it in its own on-screen box when a secret is first needed, and the unlock lasts 60 minutes. A secret is typed only on the site it is tied to, and no tool ever hands a secret's value to the model.

---

## Sign-ins inside Jalen's own Chrome

Jalen also drives a separate Chrome profile of its own (`data\browser_profile`). Sign-ins there live in that profile, not in `.env`:

- **Google**: `.venv\Scripts\python.exe scripts\sign_in_google.py`, once. You type the password and any 2FA yourself.
- **ChatGPT** and **claude.ai**: sign in once by hand in Jalen's Chrome window.

Only one process may own that profile at a time; a second Chrome on it closes the first.

---

## Optional — handing work to other AIs

| What | Where | Unlocks |
|---|---|---|
| **Gemini API key** — `GEMINI_API_KEY` | <https://aistudio.google.com/apikey> | Used only when you ask Jalen to hand a task to Gemini. It does not offload normal conversation: every turn that reaches the brain goes to Claude. |
| **OpenRouter key** — `HERMES_API_KEY` or `OPENROUTER_API_KEY` (one, not both) | <https://openrouter.ai/keys> | Only when you ask Jalen to hand a task to Hermes. Optional `HERMES_MODEL` / `HERMES_BASE_URL` change the model or endpoint. |
| **OpenAI key** — `OPENAI_API_KEY` | <https://platform.openai.com/api-keys> | Only when you ask Jalen to hand a task to ChatGPT. |
| GitHub token — `GITHUB_TOKEN`; Notion token — `NOTION_TOKEN` | — | **Not used yet.** Jalen reads both values and `.\jalen.ps1 check` reports them, but no tool calls GitHub or Notion. Leave them empty. |

Delegation is announced before it runs (AMBER). `.\jalen.ps1 ready` reports which of these keys work. That check makes one tiny live call to Gemini, and to ChatGPT if its key is set.

---

## Leave these empty

- `ANTHROPIC_API_KEY`: a value switches the brain to per-token billing. Also make sure Windows itself has no variable of that name, because `.env` does not override one.
- `CLAUDE_CLI_PATH`: empty means the bundled `claude.exe`. A `.cmd` or `.bat` here is refused.

---

## Not a credential

- **Everything** (file search) is software, not a key. See SETUP.md Step 14: Jalen calls its command-line `es.exe` at `C:\Program Files\Everything\es.exe` (`index.everything_cli`), and uses a slower search without it.
- What Jalen must never touch is `never_touch` in `config/safety.yaml`. (A list of email senders to alert on exists at `proactive.alerts.email_from` in `config/jarvis.yaml`, but nothing reads it yet.)

---

## What Jalen never asks for

Jalen never asks for a password, a card or bank number, a Telegram login code or a 2FA code out loud, in chat, or in Telegram. You type such things in only three places, each one started by you:

- the terminal prompts of `scripts\connect_telegram.py` (Telegram's login code and two-step password, which are not stored);
- `.\jalen.ps1 vault` (secrets you choose to store, encrypted);
- Jalen's own on-screen box for the vault passphrase. Type it there; never say it aloud.

Payment fields on a web form are always left for you. If anything else asks for these in Jalen's name, it is not Jalen.
