# Jalen, from a customer's side of the desk

*What I would change, in the order I would change it, with the evidence.*

You asked me to think like a customer and to reason harder. So this is not a
feature wishlist — it is what I think is actually standing between this and
something a stranger would keep using. Everything numbered is measured from
your own `data/audit.jsonl`, not estimated.

---

## The one number that matters most

Where the silence goes, per turn, from 122 real turns of yours:

| | | p50 | p95 | |
|---|---|---|---|---|
| **router** | heard | 1.56s | 2.89s | speech ends → words exist |
| | thought | 1.15s | 6.75s | words → first sound |
| | **total** | **3.29s** | **9.56s** | |
| **brain** | heard | 1.93s | 4.37s | speech ends → words exist |
| | thought | 1.56s | 8.53s | words → first sound |
| | **total** | **4.49s** | **11.71s** | |

**Speech recognition alone is 43% of a median turn**, and it is paid on every
single turn no matter which path answers it. That reframes everything:

- The habit cache I built this session saves ~1.2s, and only on repeats.
- Making transcription streaming would save ~1.6s on **every** turn, including
  the ones habits can never help with.

I built the smaller optimisation first because you asked for it specifically.
But if you want one change that makes Jalen feel different, it is this one.

### Why it costs 1.9s and what to do

Today an utterance is recorded, the recording ends, and only then is the whole
file sent to Groq. Transcription starts after you stop talking, so it can never
overlap with you talking.

Three options, in order of what I would try:

1. **Streaming transcription.** Send audio as it arrives; the transcript is
   nearly ready when you stop. Realistic target: 1.9s → ~0.4s. Groq does not
   stream, so this means a local model — `moonshine` is already installed as
   the offline fallback and never gets used. The work is real but contained.
2. **Speak the acknowledgement first.** "On it" while the real answer is being
   composed. Cheap, and it makes 11.7s feel like 1s. It is a perception fix,
   not a latency fix, and I would only do it *alongside* the real one — an
   assistant that says "on it" and then takes twelve seconds is worse than one
   that is silently slow, because now it has promised.
3. **Cut the p95 tail specifically.** The p95s (4.37s to transcribe, 8.53s to
   think) are almost certainly network stalls. A hard timeout that falls back
   to the local model would turn "it hung" into "it was slightly worse".

---

## What I would build next, and why

### 1. Undo, instead of asking first

Right now anything that reaches another person is RED: Jalen describes it and
waits for a spoken yes. That is safe, and it is a tax you pay on every single
send — including the hundreds that were exactly right.

Gmail solved this the other way round and it is worth copying. Do the thing,
then say **"Sent. Say undo and I'll pull it back."** Ten seconds. For Telegram
and email drafts this is genuinely reversible.

Why it is better rather than just faster: a confirmation prompt interrupts you
when you were *right*, which is most of the time. An undo window interrupts you
only when you were wrong. Same protection, cost moved to where the mistakes
are.

Keep RED for the genuinely irreversible — deleting files, clearing temp,
spending money. Those have no undo, so they must have a prompt.

### 2. Show progress, not just "thinking"

The orb goes yellow and stays yellow. A 40-email inbox sweep and a hung network
call look identical for three minutes, so the rational move is to kill it — and
you told me you had done exactly that.

"Reading email 12 of 40" costs almost nothing and removes the main reason
people abandon a task that was going to work.

### 3. Tell him what it costs

No tool in the project reports spend. Every brain turn is an API call, and
right now the honest answer to "what did this month cost" is "look at your
Anthropic dashboard".

For something you might sell, `"Jalen, what have you cost me this month"` is
table stakes. It is also a trust signal: software that volunteers its own
running cost is software that is not hiding anything.

### 4. Make the second user possible

`scripts/onboard.py` exists and is 382 lines, which is real work. But the wake
word has **0 recordings of any real voice**, the vault needs a passphrase
someone must invent, and Gmail and Telegram each need their own OAuth dance.

Nobody has ever run this from nothing on a machine that is not yours. Until
somebody has, "it works" means "it works here". That is the difference between
a project and a product, and it is a day's work to find out, not a month's.

### 5. Two failure messages that give up too early

- A failing tool says `"That didn't work: {exception}"`. A type name is not
  something you can act on. It should say what it was trying, what failed, and
  the one thing worth trying next.
- The wake threshold is **lowered to 0.5** to compensate for never having heard
  your voice. That buys recall by spending precision — it is why noise wakes it.
  Fifteen minutes of recording (`.\jalen.ps1 voice`) lets it go back up, and
  fixes the false triggers properly rather than working around them.

---

## What I would deliberately *not* build

Worth as much as the list above, because the tempting features here are traps:

- **A phone app.** The value is that it is on the machine where your work is.
  A phone client is a different product wearing the same name.
- **More AI backends.** There are four. A fifth adds an opinion, not a
  capability. The bottleneck is not which model — it is the 1.9s before any
  model is consulted.
- **A settings UI.** `config/jalen.yaml` is heavily commented and honest. A
  settings panel would be a second place for the truth to live, and the two
  would disagree within a month.
- **Speaker identification.** Already tried and deleted this session: on 45
  real voices, the threshold that kept 98.6% of the owner also accepted 78.4%
  of strangers. The address gate does the same job with words, and words are
  not fooled by a loud room.

---

## The honest weaknesses a customer would hit first

1. **Six workflows have never been driven by a person.** They are unit-tested
   and reachable. Nobody has watched one work.
2. **The wake word has never heard you.** Everything about pronunciation is
   currently theory.
3. **Two AI backends are blocked by accounts** (Gemini 403, no OpenAI key), and
   a third (Hermes) has no key. The browser route now works around all three,
   but it has never been pointed at the real sites.
4. **One machine, one person, one operating system.** No evidence it survives
   contact with a second of any of those.

---

## If I had one week

| Day | Work |
|---|---|
| 1 | Streaming local STT. The single biggest felt improvement. |
| 2 | Undo windows for email and Telegram; RED reserved for the irreversible. |
| 3 | Progress reporting on long jobs. |
| 4 | Drive all six workflows with you, fix what falls out. |
| 5 | Record the wake word properly; put the threshold back up. |
| 6-7 | Install from nothing on a second machine and write down every place it stops. |

Nothing there is a new feature. All of it is the difference between something
that works and something somebody else would keep.
