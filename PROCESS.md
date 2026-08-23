# What actually happens inside Jalen

Everything between you speaking and Jalen answering, in order, with the file
that does it and the reason it exists. Timings are measured from your own
`data/audit.jsonl`, not estimated.

---

## The whole turn, at a glance

```
    you speak
       |
   [1] microphone                      jarvis/audio/mic.py
       |  32 ms frames, 16 kHz mono, one queue
       |
   [2] wake word          ~3% of one core, always on, never leaves the machine
       |  openWakeWord (ONNX)          jarvis/audio/wake.py
       |
   [3] voice activity + endpointing    jarvis/audio/vad.py
       |  Silero VAD. Closes the phrase after 1.4 s of silence
       |
   [4] transcription                   jarvis/audio/stt.py
       |  Groq Whisper, moonshine locally if Groq is down
       |  ~1.9 s p50   <-- THE SINGLE BIGGEST COST IN THE TURN
       |
   [5] THE ADDRESS GATE                jarvis/app.py :: should_act_on
       |  is this even for Jalen?  if not -> silently dropped
       |
   [6] normalise + stitch              jarvis/brain/router.py :: _normalise
       |  strip politeness, re-attach a cut-off fragment
       |
   [7] kill phrases -> pending question -> pending rating -> router
       |
   [8a] ROUTER HIT                     jarvis/brain/router.py
       |   a regex matched. No model, no network, no cost.
       |   p50 3.29 s to first word
       |
   [8b] NO MATCH -> a habit?           jarvis/habits.py
       |   same sentence, same decision, 3 times -> skip the model
       |
   [8c] STILL NOTHING -> THE BRAIN     jarvis/brain/agent.py
       |   Claude, with 137 tools. p50 4.49 s to first word
       |
   [9] SAFETY GATE, before every tool  jarvis/safety.py
       |   GREEN runs / AMBER announces / RED asks / BLACK refuses
       |
  [10] speech out, sentence by sentence  jarvis/audio/tts.py
       |   edge-tts. Sentence 1 plays while sentence 3 is still being written
       |
  [11] on screen if long              jarvis/ui/orb.py :: TranscriptWindow
       |
  [12] rating, if it was real work    jarvis/tools/feedback.py
       |
  [13] learn, if one tool did it      jarvis/habits.py
```

---

## Where the time actually goes

From 122 real turns of yours:

| stage | p50 | p95 |
|---|---|---|
| hearing you (VAD + Whisper) | **1.93s** | 4.37s |
| deciding + starting to speak | 1.56s | 8.53s |
| **total silence you sit in** | **4.49s** | **11.71s** |

Transcription alone is **43% of a median turn**, paid on every turn whichever
path answers it. That is the number to attack if speed matters more than
anything else — a habit saves 1.2s and only on repeats; streaming
transcription would save ~1.6s on everything.

---

## The stack

| layer | what | why this one |
|---|---|---|
| language | Python 3.13 | the whole ML/audio ecosystem is here |
| wake word | openWakeWord (ONNX) | runs locally and free; a cloud wake word means streaming your room to somebody |
| endpointing | Silero VAD | small, fast, no network |
| speech → text | Groq Whisper, `moonshine` fallback | Groq is the fastest hosted Whisper; the fallback means a Groq outage degrades instead of stopping |
| the brain | Claude, via the Claude Agent SDK | tool use is the whole product |
| other AIs | Gemini + ChatGPT (API), Hermes (OpenRouter), **and a real browser** | the browser route works when the keys don't |
| text → speech | edge-tts | free, natural, streams sentence by sentence |
| browser control | Playwright driving your **installed** Chrome | no browser download; its own profile so your tabs are never touched |
| interface | tkinter, chroma-keyed, Win32 `WS_EX_LAYERED`/`TRANSPARENT` | no framework, no packaging weight, and click-through is one API call |
| accounts | Gmail/Calendar OAuth, Telegram (Telethon + bot) | your own accounts, your own tokens, on your machine |
| storage | JSON + SQLite on disk | nothing about you is in anybody's cloud |
| tests | pytest — **2,888** | |

---

## The seven gates a sentence passes

In order. Each exists because something went wrong without it.

**1. Wake word or the hotkey.** `Ctrl+Alt+J` is a wake word you press.

**2. The address gate** (`should_act_on`). Acts only on a sentence that starts
with its name, answers a question it just asked, is a continuation of your own
last sentence, or is an emergency stop. *Why:* the microphone cannot tell your
voice from the television or from Jalen's own voice returning through the
speakers. "I didn't catch that" was once 30 of the 255 things Jalen ever said.

**3. Kill phrases.** "stop", "cancel", "abort" and sixteen more, checked before
everything else and exempt from the gate. *Why:* the one command you need under
pressure must not need a prefix.

**4. A pending question.** If Jalen asked you something, your next sentence is
the answer — not a new command.

**5. A pending rating.** If it just asked for a score, a number is a score.

**6. The router.** ~95 regex rules. A hit costs nothing and needs no network.

**7. The safety engine** (`jarvis/safety.py`), before *every* tool call:

| tier | behaviour | examples |
|---|---|---|
| GREEN | runs immediately | reading email, playing music, opening apps |
| AMBER | says what it will do, you can say stop | overwriting a file, delegating |
| RED | asks and waits for a spoken yes | sending messages, deleting files |
| BLACK | refused outright | SSH keys, the vault file, `.env`, session files |

**The invariant that matters most:** anything Jalen *read* — an email, a web
page, a message — is `origin="content"` and can trigger **nothing**. Without
that, an email saying "post this to your channel" becomes a publishing API for
anyone who can email you.

---

## What is written to disk, and what never is

| file | what |
|---|---|
| `data/audit.jsonl` / `audit.db` | every utterance, action and timing |
| `data/vault.json` | credentials, PBKDF2 200k + HMAC |
| `data/habits.json` | learned shortcuts |
| `data/ratings.jsonl` | your scores |
| `data/web_chats.json` | browser delegations and their judgements |
| `data/browser_profile/` | Jalen's own Chrome session |
| `data/crash.log`, `last_exit.json` | why it stopped last time |

**Never written anywhere:** the vault passphrase. It is typed, never spoken,
never sent to transcription, never a tool result, never in the audit log, and
it goes out of scope with the function that reads it.

---

## The things that run on their own

| thread / process | what it does |
|---|---|
| main loop | owns the microphone |
| `jalen-turn-N` | one per turn, so a slow turn never deafens the mic |
| `jalen-orb` | the overlay's Tk root |
| `jalen-transcript` | the on-screen answer window, its own Tk root |
| `jalen-browser` | **owns Chrome.** Playwright is thread-bound; sharing it across threads is what crashed delegation |
| `scripts/hotkeys.py` | a separate *process*, so the key that starts Jalen survives Jalen dying |

---

## How delegation is supervised

```
  your goal
      |
  a structured WORK ORDER          objective, context, success criteria,
      |                            constraints, must-nots, edge cases
      |                            -- a thin brief is REFUSED, not sent
      |
  Chrome opens, ChatGPT or Gemini
      |
  signed out? -> handed to you.  CAPTCHA? -> handed to you. Always.
      |
  the brief is typed and submitted
      |
  WAIT, on three signals:  stop button gone
      |                    text exists
      |                    text unchanged for 2.5s
      |
  read the answer back
      |
  JUDGE IT AGAINST YOUR CRITERIA  -- Jalen computes no score. It puts the
      |                              objective, the criteria and the answer
      |                              in front of the model and says JUDGE IT
      |
  satisfied? -> tell you
  not?       -> a corrective prompt into the SAME conversation, max 3 rounds
```

A model saying "I have completed all requirements" is not evidence that it
has, and a percentage computed by counting keywords would be a confident
number nobody checks.

---

## How it gets faster

Not a cache of answers — a cache of **decisions**.

```
  same sentence, same tool, same arguments, 3 times
      -> next time, skip the model entirely       ~1.2s faster
```

It refuses to learn: anything using more than one tool (a plan is where the
model earns its cost), anything with a secret-shaped argument, anything whose
arguments vary, and anything above GREEN. A recalled habit still passes
through the safety engine — being fast is never a reason to skip a
confirmation you would otherwise have been asked for.

Ask it `"what have you learned"`. Correct it with `"forget that"`.
