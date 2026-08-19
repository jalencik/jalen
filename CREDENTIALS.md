# What I need from you

You said you'd provide anything needed. Here's the exact list, ordered by how
much it unlocks. **Nothing here should be pasted into this chat** — put the
values into your local `.env` file. I never need to see them.

---

## Now — required for Jarvis to talk at all

| What | Where to get it | Time | Cost |
|---|---|---|---|
| **Groq API key** | <https://console.groq.com> → API Keys → Create | 2 min | free |
| **Claude token** | run `claude setup-token` in PowerShell | 1 min | uses your Pro plan |

That's the whole requirement for a talking Jarvis.

---

## Next — the Telegram bot (your phone remote)

| What | Where | Time |
|---|---|---|
| **Bot token** | Telegram → **@BotFather** → `/newbot`, call it Jarvis | 2 min |
| **Your numeric user ID** | Telegram → **@userinfobot** → it replies with a number | 30 sec |

The user ID is a security control. The bot can drive your desktop; only IDs on
that list are obeyed.

---

## Then — Gmail and Calendar

| What | Where |
|---|---|
| **`client_secret.json`** | Google Cloud Console → OAuth client ID → **Desktop app** |

Full walkthrough in SETUP.md Step 10. **The one thing you must not skip: click
"Publish app" on the OAuth consent screen.** In Testing mode Google expires
your login every 7 days, and adding yourself as a test user does not fix it.

---

## Optional, in the order I'd do them

| What | Where | Unlocks |
|---|---|---|
| **Gemini API key** | <https://aistudio.google.com/apikey> | Free-tier offload — stretches your Claude allowance |
| **Everything** | <https://www.voidtools.com> | Instant local file search |
| **Telegram api_id + api_hash** | <https://my.telegram.org> | Reading your *real* chats (read the warning in SETUP.md) |
| **GitHub fine-grained PAT** | GitHub → Settings → Developer settings | Repos, issues, PRs |
| **Notion integration token** | <https://www.notion.so/my-integrations> | Notion pages |

---

## Questions I actually need answered

These change what gets built, and I can't guess them:

1. **Your GitHub username** — so the repo goes to the right place, and so
   Jarvis knows which account is yours.

2. **Q44, which you left blank: any app or folder Jarvis must never touch?**
   I've defaulted it to Windows system folders, your `credentials` and
   `Mother credentials` desktop folders, SSH/AWS keys, password managers, and
   banking domains. **Check `config/safety.yaml` and tell me what to add.**

3. **H61 — whose email is worth interrupting you for?** You said "email from
   specific people" but didn't name them. Give me addresses and I'll wire the
   alert.

4. **Eco Pulse and SAT TOP** — where do they live? A GitHub repo, a local
   folder, a Google Drive? Drop the paper and any README into `docs/context/`
   and Jarvis indexes them so it knows your projects properly.

5. **Do you want me to push this to GitHub for you**, or would you rather run
   `git init` yourself and keep control of the account? I'd need a token to do
   it for you, and I'd rather you didn't hand one over unnecessarily.

---

## What I will never ask you for

Your Google password, your bank details, a card number, your Telegram login
code, or any 2FA code. If anything ever appears to ask for those on my behalf,
it isn't me.
