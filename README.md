# Jalen

A voice assistant that runs on O'ktam's Windows laptop, wakes to **"Hey Jalen"** (or "Hey Jarvis"), and can actually drive the machine. Common commands are answered from a table of rules without calling any model; everything else goes to Claude; and every action passes a four-tier safety gate before it runs. At commit `833adbb` it has 165 tools and the test gate collects 5,245 tests. Both numbers move as work merges: `.\jalen.ps1 can` lists the current tools and the test command below prints the current count.

*Work in progress (October 2026): the tools, the spoken-command rules and the browser path are being changed. This page describes what is merged.*

```
   "Hey Jalen"                    you stop talking
        │                                │
        ▼                                ▼
  ┌───────────┐   ┌─────────┐   ┌───────────────┐   ┌──────────────┐
  │ wake word │──▶│   VAD   │──▶│ speech → text │──▶│    router    │
  │ trained   │   │ silero  │   │ Groq whisper; │   │  141 rules,  │
  │  here     │   │  2 MB   │   │ moonshine if  │   │   no model   │
  └───────────┘   └─────────┘   │   offline     │   └──┬────────┬──┘
                                └───────────────┘      │ match  │ no match
                                                       │        ▼
                                                       │ ┌───────────────────┐
                                                       │ │ Claude Agent SDK  │
                                                       │ │ 165 tools         │
                                                       │ └─────────┬─────────┘
                                                       │           │ each tool call
                                                       ▼           ▼
                                                   ┌──────────────────────┐
                                                   │     safety gate      │
                                                   │ GREEN AMBER RED BLACK│
                                                   └──────────┬───────────┘
                                                              ▼ runs, asks, or refuses
        ┌────────────┐   ┌──────────────┐                     │
        │  speaking  │◀──│ text → speech│◀────────────────────┘
        │ barge-in ✓ │   │   edge-tts   │
        └────────────┘   └──────────────┘
```

## What it does

**Runs the machine.** Opens apps, files and folders by the names you actually
say. Types, clicks, reads the screen. Plays a specific YouTube video rather
than opening a search page. Tells one Chrome window from another — "close the
YouTube window" closes that one, not whichever it found first.

**Reads your mail and messages.** Gmail and your personal Telegram. Scans up
to 300 emails in one call and sorts them into worth-a-look, ordinary and
automated, so "is there an opportunity in here" gets an answer instead of a
recital.

**Writes as you.** Uses your `/my-voice` skill for anything going out under
your name. Composes channel posts in your house format ([docs/community_post_format.md](docs/community_post_format.md)): two templates, a fixed three-question Q&A and an expandable block. It sends them or saves them as drafts. Before a post goes to your channel, Jalen reads its first line and the sites its links point to aloud, and posts unless you say stop. Premium emoji are looked up from your own account; that has been tested against fakes, not yet against the real account.

**Fixes the computer.** Diagnoses wifi dropouts, disk pressure, Windows
Update, Defender and startup bloat. Findings say whether they are observed
facts or likely causes, and list what it could *not* check. Repairs are
separate, gated, reversible, and verified by reading the setting back.

**Hands work to other agents.** "Hand this task to cowork" opens the Claude
desktop app with a properly engineered brief pasted in. "Hand this off to
code" starts Claude Code on it.

**Holds your credentials.** Encrypted vault, per-domain permission, and it
asks whether an approval is for this once or from now on — then remembers.

**Asks you things and waits.** Up to three minutes, and if you don't answer
it stops rather than guessing.

## Quick start

You need 64-bit Python 3.12 or 3.13 (this laptop runs 3.13) on Windows 10 or 11. Run everything from the project folder with the project's own interpreter: bare `python` on this machine is a different install with none of the packages.

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe scripts\download_models.py   # the "hey jarvis" wake model + the voice detector
.venv\Scripts\python.exe scripts\onboard.py           # your name, Groq key, Google, Telegram - resumable
```

If PowerShell refuses `.\jalen.ps1` with "running scripts is disabled", run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once (SETUP.md Step 3).

`models\hey_jalen.onnx` is not in git and `download_models.py` does not fetch it. On a new machine, copy it from a machine that has it, or build it with `.venv\Scripts\python.exe scripts\train_wake_word.py all` (edge-tts needs the network, and the script needs `pip install onnx`), before starting voice mode.

`onboard.py` can be run again: it skips what is already set up. It does not sign the brain in for you, connect your everyday Chrome, or build the "Hey Jalen" model; see **Sign in once** below. The long form is in [SETUP.md](SETUP.md).

Then start him with `.\jalen.ps1` (voice) or `.\jalen.ps1 text` (typed), and say **"Hey Jalen"**. To get **Ctrl+Alt+J** and **Ctrl+Alt+K** everywhere, run `.\jalen.ps1 hotkeys` once; it also starts Jalen, muted, when you log in.

| | |
|---|---|
| **Ctrl+Alt+J** | wake him from anywhere — launches him if he isn't running (after `.\jalen.ps1 hotkeys`) |
| **Ctrl+Alt+K** | stop him (after `.\jalen.ps1 hotkeys`) |
| *"Jalen, quit"* | "See you, Boss." |
| *"read it all"* | speak a long answer in full, uncapped |
| *"what can't you do yet"* | his own log of gaps he's hit |
| *"how fast was that"* | real per-turn timings |

No microphone handy? `.\jalen.ps1 text`: you type, with the same brain and the same safety rules.

Not sure what to do next? **`.\jalen.ps1 todo`** reads your machine and
prints only what is actually outstanding, with what each thing does and how
long it takes.

When something is wrong:

| | |
|---|---|
| `.\jalen.ps1 todo` | what still needs you, in the order to do it |
| `.\jalen.ps1 check` | packages, models, microphone, Claude sign-in, Google and Telegram accounts, free disk. Exit 0 means all good. It signs in to Telegram to check it, so if personal Telegram is connected run it with Jalen stopped |
| `.\jalen.ps1 why` | why it stopped last time, and any crash |
| `.\jalen.ps1 browser` | why the Chrome extension says "not connected" |
| `data\audit.jsonl` | everything said and done, both directions |

Full walkthrough in **[SETUP.md](SETUP.md)**. Design reasoning in
**[ARCHITECTURE.md](ARCHITECTURE.md)**.

## The launcher

| Command | What it does |
|---|---|
| `.\jalen.ps1` | start listening, voice on (same as `start`) |
| `.\jalen.ps1 text` | type instead of talk: same brain, same safety rules |
| `.\jalen.ps1 telegram` | control him from the Telegram bot |
| `.\jalen.ps1 status` / `stop` / `restart` | is he running / stop him / stop and start fresh |
| `.\jalen.ps1 check` | diagnostics; exit 0 means all good |
| `.\jalen.ps1 why` | why the last run stopped |
| `.\jalen.ps1 todo` | what still needs you |
| `.\jalen.ps1 setup` | the guided setup (`scripts\onboard.py`) |
| `.\jalen.ps1 can` | everything he can do, with tiers, generated from the code |
| `.\jalen.ps1 hotkeys` / `uninstall` | install or remove autostart and Ctrl+Alt+J / Ctrl+Alt+K |
| `.\jalen.ps1 vault` | create the password vault |
| `.\jalen.ps1 voice` | record your own "Hey Jalen" takes (about 15 minutes) |
| `.\jalen.ps1 browser` | why the Chrome extension says not connected |

## Sign in once

No credential lives in the repo; everything goes in `.env` or under `data\`.

| What | How | Notes |
|---|---|---|
| Claude, the brain (required) | `.venv\Lib\site-packages\claude_agent_sdk\_bundled\claude.exe setup-token`, then put the token it prints into `.env` as `CLAUDE_CODE_OAUTH_TOKEN=` and restart | Use the bundled `claude.exe`: it is the one the brain runs, not the `claude` on PATH. Leave `ANTHROPIC_API_KEY` empty, or the brain switches to per-token billing. `.\jalen.ps1 check` prints this fix with the exact path. |
| Groq, speech-to-text | `GROQ_API_KEY` in `.env` (`onboard.py` asks for it) | Free tier. If Groq fails, Jalen falls back to the local moonshine model. |
| Gmail and Calendar | `.venv\Scripts\python.exe scripts\connect_google.py` | Needs `client_secret.json` in the project folder ([CREDENTIALS.md](CREDENTIALS.md)). While the app's OAuth consent screen is in Testing mode the login expires every 7 days: run it again, or publish the consent screen in Google Cloud Console. |
| Your personal Telegram | `.venv\Scripts\python.exe scripts\connect_telegram.py` | Stores a session file under `data\`; treat it like a password. Only one program may use it at a time, so run this with Jalen stopped. |
| Telegram bot (optional) | `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS` in `.env`, then `.\jalen.ps1 telegram` | If the id list is empty, bot mode refuses to start. |
| Your everyday Chrome | `.venv\Scripts\python.exe scripts\install_extension.py`, then load `browser_extension\` unpacked | Steps in [docs/CHROME_EXTENSION_SETUP.md](docs/CHROME_EXTENSION_SETUP.md); `.\jalen.ps1 browser` diagnoses it. |
| Password vault (optional) | `.\jalen.ps1 vault` | You type the passphrase; no script sees it. |

## One laptop, and what that means

Jalen was built for one Windows 10 laptop with 8 GB of RAM and about 1 GB free. That is why there is no local language model and no PyTorch. Speech-to-text runs on Groq, with a small offline moonshine fallback; speech comes from edge-tts; and thinking is done by Claude through the Claude Agent SDK, which runs the `claude.exe` bundled in its wheel. Claude is the only brain: Gemini and OpenRouter are used only when you ask Jalen to hand something to them. Jalen itself is one Python process of about 350 MB.

- **One Jalen at a time.** `run.py` takes a single-instance lock; a second launch prints "already running" and exits.
- **One Telegram client at a time.** The personal Telegram session can be used by one program at once. Never start a second Jalen, and do not run `connect_telegram.py` or `.\jalen.ps1 check` while Jalen is running.
- **Run it from the main checkout.** `.venv`, `.env`, `data\` and `models\` are not in git, so a git worktree or a fresh clone has none of them until they are set up.

## Everything you can change lives in three files (keys go in `.env`)

- **`config/jarvis.yaml`** — voice, personality, speed, wake sensitivity,
  orb size and position, which integrations are on, which Telegram chats he
  may send to without asking first (`telegram.personal.send_without_asking_to`:
  Saved Messages goes straight out; a post to your channel is read aloud first
  and goes unless you say stop). No code. Every value is commented with WHY it
  is that value; those comments are the design record, so this is the file to
  read rather than the file to personalise.
- **`config/user.yaml`** — yours. Overlays the above, key by key, and is
  never committed. Your name, your folders, your channels. Written by
  `onboard.py`; absent, nothing changes.
- **`config/safety.yaml`** — which actions run freely, which announce
  themselves, which stop and ask, and which are refused outright.

Restart after editing: `.\jalen.ps1 restart`. (Saying "reload config" currently answers "Config reloaded." but re-reads nothing.)

## The four tiers

| | Behaviour | Examples |
|---|---|---|
| **GREEN** (125 tools at `833adbb`) | Just runs | read, search, diagnose, open an app, draft an email, list what's stored, a note to your own Saved Messages |
| **AMBER** (24) | Announces, waits to hear "stop" | edit a file, close a browser tab, change a driver setting, start a coding job, fill a form field; also a post to your pre-approved channel, read aloud first |
| **RED** (16) | Stops, waits for a spoken "yes" | send to another person, delete, move a folder, submit a form, unlock the vault, trust a website for good |
| **BLACK** | Refused, always | password stores, session files, OAuth tokens, `.env`, the vault itself |

`.\jalen.ps1 can` shows each tool's current tier; `config/safety.yaml` is where tiers are set.

Two properties worth knowing, because they are what make the rest safe:

**What Jalen reads cannot make him act.** After he reads an email, a message or a web page, anything that would send, change or act is refused until you give him a fresh instruction yourself. An email saying "post this to your channel" is refused even though the channel is pre-approved: pre-approval means *you* sending there, not anyone who gets text in front of him. The one thing the gate cannot see is something read earlier that is still in the model's memory when you later say "post today's summary". That is why posts to your channel are read aloud before they go.

**Secrets never become tool results.** The vault can list what it holds by
name; the values go straight to the code that types them. `get_secret` is
deliberately not a tool, and a test asserts it never becomes one.

## The wake word was trained here

There is no pretrained "Hey Jalen" model, so `scripts\train_wake_word.py` builds one. edge-tts says the phrase in 45 voices at several rates and pitches, the clips are augmented with noise, and a small classifier head is fitted on openWakeWord's frozen feature extractors. No GPU, no PyTorch. "Hey Jarvis" still works too: the pretrained hey_jarvis model loads alongside it, because habit is real.

What the numbers say and do not say. On 2,084 held-out synthetic clips, scored one 2-second window per clip, it missed 2.9% of wake words at threshold 0.7, and none of the ordinary-speech or room-noise clips triggered it at any threshold from 0.70 to 0.99. Three caveats. Jalen ships at 0.5, below every row of that table, because it was missing the owner. The running system slides its window every 80 ms, and under that protocol 11 of 60 ordinary-speech clips crossed 0.5 in the 2 September audit (report.md section 9.2). And every clip was synthetic: the model has never heard the owner. `.\jalen.ps1 voice` records his own takes (about 15 minutes).

`models\hey_jalen.onnx` is not in git. On a new machine, build it with `.venv\Scripts\python.exe scripts\train_wake_word.py all` or copy it over.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests\ -q -p no:cacheprovider --ignore=tests\benchmark_latency.py --ignore=tests\benchmark_open.py --ignore=tests\benchmark_phrasing.py
```

At commit `833adbb` this collects 5,245 tests; run it to see today's count. Always use `python -m pytest` with the venv's interpreter, never bare `pytest.exe`. `pytest-timeout` is not installed, so do not pass `--timeout`. One failure is expected on this machine and is not a code problem: `tests/test_overhaul_fixes.py::test_real_typos_and_abbreviations_still_resolve[capcut-True]` expects CapCut to be installed. Three voice tests call live Groq and edge-tts and can fail intermittently (`test_stt_roundtrips_through_groq`, `test_barge_in_latency_is_measured`, `test_failure_paths_keep_jarvis_alive`); re-run them on their own before treating that as a regression. Any other failure is real.

## Where the documents are

| File | What it is |
|---|---|
| [SETUP.md](SETUP.md) | setup step by step on a fresh Windows laptop |
| [CREDENTIALS.md](CREDENTIALS.md) | every key and sign-in: where to get it and what it unlocks |
| [docs/CHROME_EXTENSION_SETUP.md](docs/CHROME_EXTENSION_SETUP.md) | connecting Jalen to your everyday Chrome |
| [WHAT_JALEN_CAN_DO.md](WHAT_JALEN_CAN_DO.md) | every tool, its tier and a phrase that reaches it; generated by `scripts\capabilities.py` (`.\jalen.ps1 can` prints it) |
| [ABILITIES.md](ABILITIES.md) | the same in detail; generated by `.\jalen.ps1 abilities` |
| `config/jarvis.yaml` | every setting, each with the measurement behind it: the design record |
| `config/safety.yaml` | tiers, the never-touch list and the injection guard |
| [ARCHITECTURE.md](ARCHITECTURE.md) | design reasoning |
| [report.md](report.md) | the 2 September 2026 audit, measured from the code and the logs |
| [CLAUDE.md](CLAUDE.md) | rules for anyone, or any Claude session, changing the code |
| [HANDOFF.md](HANDOFF.md) | history and open items for a new working session |
| [docs/community_post_format.md](docs/community_post_format.md) | the channel post format |
