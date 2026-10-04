# What actually happens inside Jalen

Everything between you speaking and Jalen answering, in order, with the file
that does it and the reason it exists. Timings are measured from your own
`data/audit.jsonl`, not estimated.

---

## The whole turn, at a glance

```
    you speak
       |
   [1] microphone                      jalen/audio/mic.py
       |  32 ms frames, 16 kHz mono, one queue
       |
   [2] wake word          ~3% of one core, always on, never leaves the machine
       |  openWakeWord (ONNX)          jalen/audio/wake.py
       |
   [3] voice activity + endpointing    jalen/audio/vad.py
       |  Silero VAD. Closes the phrase after 1.4 s of silence
       |
   [4] transcription                   jalen/audio/stt.py
       |  Groq Whisper, moonshine locally if Groq is down
       |  ~1.9 s p50   <-- THE SINGLE BIGGEST COST IN THE TURN
       |
   [5] THE ADDRESS GATE                jalen/app.py :: should_act_on
       |  is this even for Jalen?  if not -> silently dropped
       |
   [6] TAINT CLEARED                   jalen/taint.py
       |  he spoke, so everything read before now stops being in play
       |
   [7] REFERENCES EXPANDED             jalen/conversation.py
       |  "send it there" -> "send the draft to Saved Messages"
       |
   [8] THE CONTRACT                    jalen/plan.py
       |  ACTION + DESTINATION extracted and kept for the turn
       |
   [9] normalise + stitch              jalen/brain/router.py :: _normalise
       |  strip politeness, re-attach a cut-off fragment
       |
  [10] kill phrases -> "go on" -> pending question -> pending rating -> router
       |
  [11a] ROUTER HIT                     jalen/brain/router.py
       |   a regex matched. No model, no network, no cost.
       |   p50 3.29 s to first word
       |
  [11b] NO MATCH -> a habit?           jalen/habits.py
       |   same sentence, same decision, 3 times -> skip the model
       |
  [11c] STILL NOTHING -> THE BRAIN     jalen/brain/agent.py
       |   Claude, with 145 tools. p50 4.49 s to first word
       |
  [12] SAFETY GATE, before every tool  jalen/safety.py
       |   origin comes from taint.origin_now(), NOT a hardcoded "user"
       |   GREEN runs / AMBER announces / RED asks / BLACK refuses
       |
  [13] speech out, sentence by sentence  jalen/audio/tts.py
       |   edge-tts. Sentence 1 plays while sentence 3 is still being written
       |   no cap at all when he said "read it"
       |
  [14] on screen if long              jalen/ui/orb.py :: TranscriptWindow
       |
  [15] DID IT DO WHAT HE ASKED?        jalen/plan.py :: betrayed_by
       |   he said send and it drafted -> he is TOLD, not silently corrected
       |
  [16] rating, if it was real work    jalen/tools/feedback.py
       |
  [17] learn, if one tool did it      jalen/habits.py
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
| tests | pytest — **2,982** | |

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

**7. The safety engine** (`jalen/safety.py`), before *every* tool call:

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

## The taint: how "it read that" is remembered

The single most important control in the project, and for months it was
unreachable code.

`SafetyEngine.classify(origin="content")` refuses RED and AMBER outright.
Correct, tested, and cited in three modules as "the hard protection". But the
only line that could set it read `input_data.get("_from_content")`, and
**nothing ever set that key** except a test which injected it by hand. So the
test proved the classifier works *when told*, and could not prove anything
ever told it. Nothing did.

```
  an email body       |
  a Telegram message  |
  a web page          |---->  _fence()  ---->  taint.mark(source)
  another AI's answer |                              |
                                                     v
                              every later tool call this turn
                                                     |
                              classify(origin="content") -> RED/AMBER refused
                                                     |
                              he speaks again -> taint cleared, from ONE
                                                 call site only
```

Process-wide rather than thread-local, deliberately: the brain runs each tool
on whichever pool thread is free, so a thread-local set inside `read_email`
would be invisible to the `send_email` that follows it. Two overlapping turns
can therefore taint each other — that is over-blocking, and the failure in
the other direction is an email talking Jalen into sending mail.

## The present tense: what "it" and "yes" mean

`jalen/conversation.py`. Long-term memory (`jalen/tools/memory.py`,
fastembed + sqlite-vec, local) was never the gap; the gap was the last few
minutes.

| holds | for |
|---|---|
| the last **proposal** | what "yes, go ahead" agrees to |
| the current **subject** | what "it", "there", "them" point at |
| the live **tasks** | what "continue" and "cancel that" mean |

Twelve task states: `IDLE THINKING EXECUTING WAITING_FOR_USER
WAITING_FOR_CONFIRMATION WAITING_FOR_EXTERNAL_AI WAITING_FOR_BROWSER
RUNNING_BACKGROUND_JOB COMPLETED FAILED CANCELLED`. Every turn opens one and
closes it — including on an exception, which previously vanished with the
thread.

Nothing credential-shaped may become the subject: `"it"` must never expand
into a password.

## The contract: action and destination

`jalen/plan.py`. He said *"send them in my saved messages"* and got a draft
somewhere else.

```
  his sentence
      |
  ACTION = send        DESTINATION = Saved Messages
      |
  ... the turn runs ...
      |
  compare against what actually ran
      |
  save_telegram_draft is forbidden for "send"  ->  he is told
```

Reported, never silently re-run: correcting it automatically would be a
second guess on top of the first. Half a request is not a contract — "send
this" with no destination is him trusting Jalen to choose.

## Filling in forms

`jalen/tools/webforms.py`, on the page open in Jalen's own browser.

`autofill.py` refuses to pick a field, and that refusal is correct *there*:
it drives the desktop with keystrokes, where finding a field is Tab-and-hope.
Inside Playwright it is not a guess — `input[type="password"]` IS the password
box. The invariant is not relaxed; it is satisfied by a mechanism that can
satisfy it.

| tool | tier | |
|---|---|---|
| `inspect_form` | GREEN | every field, label, type, required, filled |
| `fill_form_field` | AMBER | refuses password fields outright |
| `upload_to_form` | AMBER | through the page's real file input, never the OS dialog |
| `form_errors` | GREEN | what the page is complaining about |
| `submit_form` | **RED** | reports what the page pushed back |
| `fill_login_field` | **RED** | one password box, exact host approval, value never returned |

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

---

## Two ways into a browser, and why

Jalen can drive a browser two different ways, because Chrome makes exactly
one of them impossible and the other only recently possible.

```
  the SEPARATE window  (jalen/tools/webagent.py)
    plain chrome.exe on a dedicated profile, attached over CDP
    -> his account, but not the tabs he browses in
    -> used when the extension isn't connected

  his REAL Chrome      (jalen/bridge/ + browser_extension/)
    an extension inside his everyday Chrome, talking to the app
    -> the very tab he's looking at
    -> used when he asks to use his own Chrome
```

Why two: Chrome 136+ refuses to let an outside program automate the profile
you browse in (measured: 150s timeout vs 0.8s on a dedicated one), and Google
refuses sign-ins from a program-launched browser. The extension is the only
way *inside* his everyday Chrome, because Chrome trusts an extension it does
not trust an external driver. The CDP window stays as the fallback for when
the extension isn't loaded.

### The extension data flow

```
  his voice
    -> Python brain            (Gmail, vault, safety — the authority)
    -> task state / safety tier
    -> BridgeServer            (loopback, token-authed)   jalen/bridge/server.py
    -> native_host.py          (the process Chrome launches; a dumb relay)
    -> Chrome Native Messaging (length-prefixed JSON frames)
    -> service_worker.js       (carries out an ALREADY-authorised command)
    -> the real page           (pageOp injected into the tab)
    -> result back up the same wire
    -> Python verifies, speaks / shows
```

The safety boundary is an asymmetry: **only the app originates commands.** The
page can answer a question and can raise an event, but it can never tell the
app "here is a secret, type it" — the command allowlist has no arbitrary-JS
entry, and the app classifies every action by the same tiers as everywhere
else. Passwords come from the vault, payment fields are his, CAPTCHA stops for
a human, and the app↔extension link is gated by a per-run token only his
account can read. Setup and the full security model: docs/CHROME_EXTENSION_SETUP.md.
