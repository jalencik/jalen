# Jalen

A voice assistant that runs on O'ktam's Windows laptop, wakes to **"Hey
Jalen"**, and can actually drive the machine — 102 tools, 1,545 tests, and a
four-tier safety gate every single action passes through before it runs.

```
   "Hey Jalen"                    you stop talking
        │                                │
        ▼                                ▼
  ┌───────────┐   ┌─────────┐   ┌───────────────┐   ┌──────────────┐
  │ wake word │──▶│   VAD   │──▶│ speech → text │──▶│    router    │
  │ trained   │   │ silero  │   │  Groq whisper │   │ ~88% of turns│
  │  here     │   │  2 MB   │   │    (cloud)    │   │  stop here   │
  └───────────┘   └─────────┘   └───────────────┘   └──────┬───────┘
                                                            │ novel
                                                            ▼
                                                    ┌───────────────┐
                                                    │  safety gate  │
                                                    │ GREEN AMBER   │
                                                    │  RED  BLACK   │
                                                    └───────┬───────┘
                                                            ▼
        ┌────────────┐   ┌──────────────┐   ┌───────────────────────┐
        │  speaking  │◀──│ text → speech│◀──│  Claude Agent SDK     │
        │ barge-in ✓ │   │   edge-tts   │   │  102 tools            │
        └────────────┘   └──────────────┘   └───────────────────────┘
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
your name. Composes channel posts in your house format — two templates, a
fixed three-question Q&A, an expandable block — and sends or drafts them.

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

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\download_models.py
python scripts\onboard.py     # keys, Google, Telegram, your name - one guided pass
```

`onboard.py` walks the whole thing and is resumable: run it again and it
detects what is already done rather than asking twice. If you would rather do
it by hand, the long form is in [SETUP.md](SETUP.md).

Then just press **Ctrl+Alt+J**, or say **"Hey Jalen"**.

| | |
|---|---|
| **Ctrl+Alt+J** | wake him from anywhere — launches him if he isn't running |
| **Ctrl+Alt+K** | stop him |
| *"Jalen, quit"* | "See you, Boss." |
| *"read it all"* | speak a long answer in full, uncapped |
| *"what can't you do yet"* | his own log of gaps he's hit |
| *"how fast was that"* | real per-turn timings |
| *"watch my hands"* | size the orb by pinching, if the camera extra is installed |

No microphone handy? `python run.py --text`.

Not sure what to do next? **`.\jalen.ps1 todo`** reads your machine and
prints only what is actually outstanding, with what each thing does and how
long it takes.

When something is wrong:

| | |
|---|---|
| `.\jalen.ps1 todo` | what still needs you, in the order to do it |
| `.\jalen.ps1 check` | what is missing, what is set up, how much disk is left |
| `python run.py --why` | why it stopped last time, and any crash |
| `data/audit.jsonl` | everything said and done, both directions |

Full walkthrough in **[SETUP.md](SETUP.md)**. Design reasoning in
**[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Everything you can change lives in three files

- **`config/jarvis.yaml`** — voice, personality, speed, wake sensitivity,
  orb size and position, which integrations are on, which Telegram chats he
  may send to without asking. No code. Every value is commented with WHY it
  is that value; those comments are the design record, so this is the file to
  read rather than the file to personalise.
- **`config/user.yaml`** — yours. Overlays the above, key by key, and is
  never committed. Your name, your folders, your channels. Written by
  `onboard.py`; absent, nothing changes.
- **`config/safety.yaml`** — which actions run freely, which announce
  themselves, which stop and ask, and which are refused outright.

Say *"Jalen, reload config"* after editing.

## The four tiers

| | Behaviour | Examples |
|---|---|---|
| **GREEN** (86) | Just runs | read, search, diagnose, open an app, draft an email, list what's stored |
| **AMBER** (6) | Announces, waits to hear "stop" | overwrite a file, close a browser window, change a driver setting, start a coding agent |
| **RED** (10) | Stops, waits for a spoken "yes" | send, delete, install, unlock the vault, approve a website |
| **BLACK** | Refused, always | password stores, session files, OAuth tokens, `.env`, the vault itself |

Two properties worth knowing, because they are what make the rest safe:

**Nothing Jalen *reads* can make him act.** An email or a message saying
"post this to your channel" is refused even for a channel you pre-approved.
Pre-approval means *you* sending there, not anyone who gets text in front of
him.

**Secrets never become tool results.** The vault can list what it holds by
name; the values go straight to the code that types them. `get_secret` is
deliberately not a tool, and a test asserts it never becomes one.

## The wake word was trained here

There is no pretrained "Hey Jalen" model, so `scripts/train_wake_word.py`
builds one: edge-tts generates thousands of utterances across 45 voices,
augments them, and fits a classifier head on openWakeWord's frozen feature
extractors. No GPU, no PyTorch.

Measured on 2,084 held-out clips: **0.00% false accepts on ordinary speech
and room noise at every threshold**, 2.9% missed wake words at 0.7. "Hey
Jarvis" still works too — both models load, because habit is real.
