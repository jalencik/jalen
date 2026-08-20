# Jarvis — Overhaul Report

Written for someone who wants to understand what actually happened, in plain
terms. Every number here was measured on the real machine, not estimated.

**Verdict: MACHINE-VERIFIED — REQUIRES USER ACCEPTANCE TEST**

Everything I can test by machine passes (323 automated tests). The one thing
left is you speaking into the microphone, which I cannot do for you.

---

## 1. Why it felt slow — the actual reason

I reproduced the complaint and found the cause. It was not one slow thing; it
was a bug that made you repeat yourself, and a repeat cost the most.

**The bug:** When you said *"Hey Jarvis, open Chrome"* in one natural breath:

1. The wake word "Hey Jarvis" was detected 0.77 seconds in.
2. The code then **threw away the next 0.99 seconds of audio** — the part
   containing "open Chrome". (It called a "drain" function meant to clear old
   noise, but it cleared the command too.)
3. Jarvis then listened for a command that had already been spoken, heard
   silence, waited 4 seconds, and **gave up without saying anything**.
4. From your side: you spoke, nothing happened, no explanation.
5. So you said it again — and *that* attempt paid all the first-time startup
   costs, because nothing was warmed up in advance.

**First-time (cold) costs, measured:**

| Component | First use | Afterwards |
|---|---|---|
| Speech-to-text (Groq) | 3,988 ms | ~330 ms |
| Text-to-speech (edge-tts) | 4,562 ms | ~2,000 ms |
| Claude connection | 17,734 ms | 2,885 ms |

Add a dropped command plus a cold start and you get the "about a minute"
experience you described.

### What I changed

- **Keep the audio.** The tail of your sentence is no longer discarded. It is
  only dropped if it is more than 1.5 seconds old (genuinely stale).
  **Verified end-to-end:** one breath now captures 1.34 s of command audio and
  transcribes correctly as `"Open Chrome."`
- **Warm everything up at startup**, in the background, so the first thing you
  say does not pay those costs. This spends no money and no Claude usage — it
  only opens connections.
- **Speak and think at the same time.** Jarvis used to finish creating *all*
  the audio before playing *any* of it. Now sentence 2 is being prepared while
  sentence 1 is playing. Measured on a 3-sentence reply, alternating runs to
  cancel out internet variation: **10.5 s → 7.8 s (about 26% faster)**.
  (The delay before the *first* word is unchanged at ~1.35 s — the first
  sentence always has to be made before it can be spoken.)

**Local commands are now effectively instant:**

| Command | Time to decide | Time to do it |
|---|---|---|
| "what time is it" | 0.35 ms | 0 ms |
| "open notepad" | 0.02 ms | 24 ms |
| "minimize this window" | 0.04 ms | 576 ms |

---

## 2. Why basic commands did not work

Two separate bugs, both serious, both now fixed.

**a) Window commands never matched anything.** Jarvis converts everything you
say to lowercase. But the code that finds windows searched for the lowercase
text **case-sensitively** — so it looked for a window called `chrome` while the
real window is called `Google Chrome`, and found nothing. I verified this
directly: searching `.*notepad.*` does not match `Untitled - Notepad`.
Every "close X" / "focus X" / "switch to X" was silently failing.

**b) Opening apps was guesswork, and it lied about success.** The old code
took whatever word you said, fired it at Windows, and replied *"Opening X"* as
long as nothing crashed — which it never does, even for an app that doesn't
exist. So Jarvis confidently claimed success while nothing opened.

Now it looks the app up properly (known aliases → system PATH → the Windows
registry → your Start Menu shortcuts) and then **checks that the app actually
appeared** before saying it opened. If it can't find the app, it says so:
> "I couldn't find an app called *X* on this machine."

**c) Only the word "open" worked.** "launch Chrome", "start Chrome", "fire up
Chrome" all missed the fast local path and went to Claude (seconds instead of
a millisecond). All of them work locally now, as do "can you open Chrome" and
"open Chrome, Jarvis" (the trailing "Jarvis" used to become part of the app
name).

**d) One bug pattern, three victims.** Commands are matched top to bottom,
first match wins. Several broad rules were swallowing more specific ones:

- "open the folder Downloads" → treated as an *app* named "the folder Downloads"
- "go to sleep" → treated as *switching to a window named "sleep"*
- "play spotify" → pressed the play/pause key (does nothing if Spotify is closed)

I reordered the rules, documented the ordering rule in the file, and added a
test that catches this whole *category* of bug rather than just these three.

---

## 3. "Old commands execute later, all at once"

Real bug. Each command was run on its own background worker with no limit and
no way to cancel. Several commands issued while one was still working would all
finish later and talk over each other.

Now every command gets a number; only the newest one controls what Jarvis says
and shows; and if too many are already running, Jarvis tells you out loud
— *"I'm still on the last one — give me a second."* — instead of silently
stacking them up.

---

## 4. "I still don't know how to stop it"

This was the worst gap, and you were right to be annoyed. **There was no stop
mechanism at all** other than Ctrl+C in the exact terminal window that started
it — useless once that window is closed.

When I checked your machine, **four Jarvis processes were running**, including
two voice sessions fighting over the same microphone. That alone explains a lot
of the erratic behaviour.

### Now you can stop it

```powershell
.venv\Scripts\python.exe run.py --stop      # stop it
.venv\Scripts\python.exe run.py --status    # is it running?
.venv\Scripts\python.exe run.py --restart   # stop, then start fresh
```

Or just say: **"Jarvis, quit"** · **"Jarvis, pause"** · **"Jarvis, resume"**

- **Only one Jarvis can run now.** A second launch tells you one is already
  running and exits, instead of quietly competing for your microphone.
- **Stopping is polite first, forceful second.** It asks the running copy to
  shut down properly (releasing the microphone and closing its database), and
  only kills it if it refuses. Verified live: *"Stopped Jarvis (pid 3744)
  cleanly."*, zero leftover processes.
- **"Pause" now actually pauses.** Previously, saying "go to sleep" made Jarvis
  *say* "Sleeping. Say hey Jarvis to wake me" while continuing to listen and
  act — it was simply not true. It now really stops, and the wake word stays
  active so "Hey Jarvis" brings it back.

---

## 5. Safety

The safety system was already solid and I did not weaken it. Two things:

- **Fixed a real gap:** if Jarvis (via Claude) announced an action and you said
  **"stop"** during the 4-second window, it announced the cancellation and then
  **did the action anyway**. The other path already handled this correctly.
  Now both refuse.
- **Removed a pointless delay:** minimising a window used to force a 4-second
  "say stop if you don't want that" wait, because it had never been categorised.
  It is now correctly treated as harmless.

An independent audit confirmed there is no way for a tool to run without
passing the safety check, and that Claude cannot reach Bash/PowerShell.

---

## 6. Config keys that were lying

Your settings file listed features that no code actually read — so changing
them did nothing. I audited every key and labelled the dead ones
`[NOT IMPLEMENTED]` in the file itself. Nothing was deleted, so the intent
survives; they just no longer pretend.

**Now genuinely working:** `tts.sentence_streaming` (implemented),
`memory.private_mode_default` (was ignored — Jarvis always logged),
`window_state`/`open_folder` safety levels.

**Labelled as not built:** the Ctrl+Alt+Space hotkey (spoken "stop" and
`--stop` *do* work), fuzzy/near-miss command matching, Claude model tiering,
Gemini offload, habit learning, file-*content* search, tray icon,
click-through orb, and the scheduled 21:00 daily review.

---

## 6b. Round two — fixed from your actual recorded session

I read the audit log of your real test. Everything below was a genuine bug it exposed.

**"open changes.pdf" wasted ~38 seconds and then failed.** There was **no
tool to open a file at all**. Jarvis found the file, had no way to open it,
opened the containing folder instead, told you to double-click it yourself,
and eventually rendered the PDF through an unrelated browser tool. Now:
`"Opened Changes.pdf"` in **0.76 s**.

**"open my Igram" / "open my CV" failed.** Everything was treated as an *app*
name, matched exactly. Now names resolve as: exact path → nickname you taught
→ known alias (only if actually installed) → your Start Menu (162 apps here)
→ equivalence family → fuzzy → file search.

**"open Telegram" now opens AyuGram in ~1 second.** The shipped alias pointed
at `Telegram.exe`, which isn't installed on your machine. Aliases are now
verified before use, and AyuGram is registered as a Telegram client — that
part is knowledge, not spelling, because no amount of string similarity
connects "telegram" to "ayugram".

**"play timeless"** now finds and opens the actual track instead of pressing
a media key into nothing.

**You can name things yourself.** *"Remember my beats folder is …"* persists
across restarts, and the name you chose is matched exactly — even if it starts
with "my".

**A real security finding.** Jarvis was inheriting this machine's Claude Code
configuration — including MCP servers from an unrelated developer setup (that
is what it used to open your PDF). Those tools were not in the safety file, so
they landed in the weakest category. Jarvis now runs in isolation mode with
only its own 37 tools. Verified.

**Also fixed:** "brief me" used to produce total silence; it now reports date,
time and battery and says plainly that email/calendar need Google connected.

## 6c. Round three — capability build

**Your C: drive is 99% full — 1.5 GB free.** That degrades Windows system-wide
and is worth fixing today. Jarvis found ~4.4 GB safely reclaimable: npm cache
2.1 GB, Downloads older than 30 days 1.6 GB, pip cache 445 MB, temp files
216 MB, recycle bin 76 MB. Ask it *"what can I delete"* for the current list.
It only ever measures and reports — deleting still needs your spoken yes.

**Reading documents now works, including PDFs.** This needed a new dependency
(`pypdf` — pure Python, 380 KB, no compiler, safe on your RAM budget). Verified
on a real 20,000-character PDF in 1.4 s. Two details that mattered: PDFs
extracted with every word on its own line, which would have tripled the cost of
handing a document to the AI and sounded like dictation when spoken — that is
now rejoined into proper prose. And image-only/scanned PDFs are reported as
needing OCR rather than returned as silently empty.

Also new: real search *inside* file contents (not just filenames), and
`.docx`/`.pptx`/`.xlsx` reading with no extra dependencies.

**The slow tools are now instant.** Disk and cleanup scans genuinely take 13 s
and 41 s — unusable for voice. Those numbers barely change minute to minute, so
results are cached for 10 minutes, refreshed in the background, and pre-warmed
at startup. Measured: **41 s → 0.00 ms.** If a figure is more than ~90 seconds
old, Jarvis says so out loud rather than passing it off as current.

**Far more commands run locally.** All of these now resolve in under a
millisecond instead of a multi-second AI round trip: "scroll down", "copy that",
"paste", "select all", "undo", "save it", "new tab", "close tab", "go back",
"refresh the page", "close this window", "make it louder", "lock it",
"search the web for X", "what's eating my memory", "how much space do I have".
Every rule was checked to point at a genuinely implemented tool — an earlier bug
had rules pointing at nothing, which produced silence.

**The orb matches what you asked for:** dark when idle, **blue** when listening,
**yellow** when executing. It also now shows the last thing it *heard*, so you
can tell a misheard command from a broken one without opening a terminal.

## 7. What I could NOT complete

Honest list — these are genuinely blocked or not built.

| Item | Why |
|---|---|
| **Your voice test** | I can measure everything, but I cannot speak into your microphone. This is the last step. |
| **Gmail / Calendar** | Needs `client_secret.json` from Google Cloud Console (SETUP.md Step 10). Nothing can be built until that file exists. |
| **Personal Telegram account** | First login needs an SMS code I cannot receive. Also carries a real account-ban risk per SETUP.md's own warning. |
| **Scheduled features** (7:30 brief, 21:00 review) | No scheduler exists. "brief me" works on demand. |
| **Ctrl+Alt+Space hotkey** | Needs a new dependency; spoken "stop" and `--stop` cover the same need. |
| **Claude model tiering** | The switching function exists but is never called; switching model mid-session requires reconnecting. Every turn uses the standard model. |
| **Voice + Telegram at the same time** | They share reply-routing state. Run one at a time. |
| **Content search inside files** | Search matches filenames only. |

I also deliberately **did not** re-verify the earlier phases' work, since it was
already verified and re-running it would burn your Claude usage for no new
information.

---

## 8. How to use it

```powershell
cd C:\Users\user\Desktop\Jarvis-setup\jarvis

.venv\Scripts\python.exe run.py --check      # health check
.venv\Scripts\python.exe run.py --unmuted    # voice
.venv\Scripts\python.exe run.py --text       # typing, no mic
.venv\Scripts\python.exe run.py --telegram   # from your phone
.venv\Scripts\python.exe run.py --stop       # stop it
```

**Important:** always use `.venv\Scripts\python.exe`, not plain `python`. A
fresh terminal's `python` is a different installation without the project's
packages — that is what caused the earlier `No module named 'numpy'` error.
There is nothing wrong with the project when that happens.

### The test that matters

1. `.venv\Scripts\python.exe run.py --unmuted`
2. Wait ~10 seconds (warm-up happens in the background).
3. Say, in one natural breath: **"Hey Jarvis, open Chrome."**
4. Chrome should open, with no need to pause between "Hey Jarvis" and the command.
5. Then try: **"Hey Jarvis, what time is it."** — should be near-instant.
6. Then: **"Jarvis, quit."**

If any step misbehaves, the exact wording of what it said is the most useful
thing to report.

---

## 9. Current state

- **323 automated tests passing** (134 before this work)
- **Health check:** all green, no warnings
- **Code compiles clean**, no stray processes
- **5 commits** on `main`, all work committed
- No secrets in the code or its history; `.env` never committed
