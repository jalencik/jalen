# Jalen — handoff

**For a fresh session picking this up.** Written 22 August 2026, after four
phases of build. It tells you where things stand, what is genuinely
unfinished, and what you must not break while finishing it.

Read this before touching anything. Then read `ARCHITECTURE.md` for the
design reasoning and `README.md` for what it does.

---

## Orientation in sixty seconds

Jalen is a voice assistant on O'ktam's Windows 10 laptop. Wake word → VAD →
Groq Whisper → **intent router** (answers ~88% of turns locally, for zero
tokens) → **safety gate** → Claude Agent SDK with 109 tools → edge-tts.

```
jarvis/
  app.py            the orchestrator and the mic loop. Big; read run() first.
  runtime.py        single-instance lock, stop/signal files
  safety.py         THE GATE. classify() decides GREEN/AMBER/RED/BLACK
  timing.py         per-turn stopwatch — how "slow" became a number
  brain/
    router.py       ~90 local rules. Read the ORDERING RULE comment first.
    agent.py        Claude SDK client, system prompt, PreToolUse safety hook
    tools.py        TOOL_SPECS — what the model can see. 1:1 with the registry.
  tools/            26 modules, one REGISTRY each, merged in __init__.py
  audio/            wake, vad, stt, tts (SpeechStream is subtle — read it)
  ui/orb.py         the floating orb
config/
  jarvis.yaml       everything tunable. Heavily commented; the comments are
                    the design record — read them before changing values.
  safety.yaml       which tool sits in which tier, and why
```

**Ground truth lives in `data/audit.jsonl`.** Every utterance both ways,
every action, every tier decision, per-turn timings. Most bugs in this
project were found by reading it rather than by reading code. Do that first
when he reports something.

**Run the tests before and after every change:**

```powershell
.venv\Scripts\python.exe -m pytest tests\ -q `
  --ignore=tests\benchmark_latency.py `
  --ignore=tests\benchmark_open.py `
  --ignore=tests\benchmark_phrasing.py
```

1,637 tests, ~2 minutes. `tests/test_voice_pipeline.py` calls live Groq and
edge-tts and occasionally fails on the network — **re-run it alone before
treating a failure there as a regression.**

---

## Three invariants. Breaking any of these is a security bug.

These fail SILENTLY. Nothing errors, tests stay green, and the damage is
only visible after something has already gone out.

### 1. The injection guard runs BEFORE destination pre-approval

In `SafetyEngine.classify()`, the `origin == "content"` check sits above the
pre-approved-destination check. Text Jalen merely *read* — an email, a
Telegram message, a web page — can never trigger a RED tool, even one aimed
at a destination he approved.

Moving the pre-approval block above it turns his ML community channel into
an open relay for anyone who can get words in front of him.
`tests/test_preapproved_sends.py::test_the_preapproval_check_runs_after_the_injection_check`
asserts the ordering.

### 2. Secrets never become tool results

`vault.get_secret()` is deliberately **not** in any REGISTRY. A tool result
reaches the model, the transcript window, the audit log, and possibly the
TTS engine. Tools may list secret **names** only. `fill_credential` types the
value and reports which secret went in, never what it was.

### 3. Jalen never chooses the field

`autofill.py` types into whatever **he** has focused. Chrome does not permit
reliable field-finding, so any "find the password box" implementation is
Tab-and-hope — and Tab-and-hope already typed into YouTube's search box and
wiped it. If you add element-finding here, a password can land in the wrong
box.

---

## What is genuinely NOT done

### 1. Hand-gesture orb resize — NOT STARTED

He asked for it twice, with a reference photo of hands pinching a glowing
sphere. It needs camera hand-tracking: a webcam feed plus a pose library
(MediaPipe Hands is the obvious choice), a pinch-distance → size mapping,
and a way to turn it off that does not involve the camera.

**Deliberately not folded into another change** — it is a real dependency
(~50MB, plus a camera permission) and a feature in its own right, not a
tweak. Voice control (`"make the orb bigger"`) works today as the stand-in.

Start at `jarvis/ui/orb.py::resize_by()` — the plumbing already exists and is
clamped to the screen; you only need a new source of deltas.

### 2. Why Jalen exited on its own — UNEXPLAINED

Once, on 21 August, a voice-mode instance started and stopped 18 seconds
later with nothing in the audit log but the prewarm lines. It has been
stable across many restarts since, for hours at a time.

**It has not been diagnosed, only not-reproduced.** Do not report it as
fixed. If it recurs: `data/audit.jsonl` will show the start with no
corresponding `Jalen stopped` reason, and `runtime.py` writes the lock file
that would reveal a second instance racing.

### 3. The wake model has never heard HIS voice

`models/hey_jalen.onnx` was trained on 2,160 synthetic positives across 45
edge-tts voices. Measured on held-out synthetic audio: **0.00% false accepts
on ordinary speech at every threshold**, 2.9% missed wake words at 0.7.

**The miss rate on him specifically is unmeasured**, and accent is exactly
what these models are sensitive to. If he says it misses him:

1. Lower `wake.threshold` toward 0.5 first — free, instant.
2. If that is not enough, record 30–50 real samples of him saying it, drop
   them into `data/wake_training/positive/`, and re-run
   `scripts/train_wake_word.py train --augment 2`. Embeddings are cached, so
   only the new clips cost time.

Known miss: **"Hey Galen" fires it.** G and J are barely separable in most
accents. Accepted deliberately — someone would have to say "Hey Galen" in
his room.

### 4. Flows that exist but have never run end to end

Each is built, unit-tested, and reachable. None has been driven all the way
through by the brain in a live voice turn. **Test these with him present**,
because most reach other people:

| Flow | Why it is untested |
|---|---|
| Email → research → community post | Needs a real opportunity email and his approval to post |
| `fill_credential` on a real site | Needs a vault (not created yet) and a real login page |
| `send_posts` with several posts | Sends to real chats; wanted him present |
| `ask_user` through a live voice turn | Unit-tested with a fake; the real mic path is unverified |
| `clear_temp_files` | RED, deletes files; wanted him present |
| `hand_off_to_cowork` end to end | The app launches and the clipboard is verified; the paste landing in Claude's box was never confirmed by eye |

### 5. The vault has never been created

`data/vault.json` does not exist. Nothing is stored, so `fill_credential`
cannot be tested for real. **He needs to run this once, himself** — it asks
for a passphrase with `getpass` and must never be typed by an assistant:

```powershell
.venv\Scripts\python.exe scripts\vault_setup.py
```

### 6. It is built for HIS machine, not for sale yet

This is the honest gap between "works" and "a product someone else can buy":

- **No onboarding.** A second user has no path from install to working. They
  need Google OAuth, a Telegram login, a Groq key, and a Claude token, and
  today that is four scripts and a README.
- **Hardcoded to him.** `config/jarvis.yaml` carries his name, his projects,
  his channel, `@Iht_student` in the post format, and his Desktop paths.
  Nothing is per-user.
- **The my-voice skill is his.** `voice_guide` reads a file from his
  `~/.claude/skills`. Another user has no equivalent.
- **No packaging.** It runs from a venv and a git checkout.
- **No licence, no telemetry, no update path.**

---

## Known limitations that are working as intended

Do not "fix" these without reading why. Each is written down because the
obvious fix is worse.

| Limitation | Why |
|---|---|
| Background Chrome tabs are invisible | Chrome does not publish its tab strip. Only the ACTIVE tab of each window is addressable, and every reply says so rather than failing to find a tab he can see. |
| One Telegram draft per chat | Telegram's model, not ours. `send_posts` refuses to batch drafts rather than appearing to save fifty and leaving one. |
| Gmail attachments cap at ~18MB | The 25MB limit is on the whole encoded message and base64 inflates by a third. |
| `scan_inbox` sorting is a guess | It says so, and tells the brain to open anything close with `read_email` before calling it an opportunity. |
| The technician never fixes in the same call it diagnoses | Diagnosis must be free to run or he stops asking, and a fix inside a diagnostic is a fix nobody reviewed. |
| `max_spoken_chars` is 320 | Seven hundred was forty seconds of talking. "Read it all" removes the cap on demand. |
| `fill_credential` is AMBER, not RED | The real gate is the per-domain check inside it. RED would ask aloud on every field even for a site he approved forever. |

---

## How he works, and what he has said about it

Worth knowing before you propose anything:

- **He wants depth per feature, not breadth.** Finish one thing properly
  before starting the next. He has said this explicitly.
- **Implement obvious improvements without asking.** Reserve questions for
  genuine forks where the options differ materially — he engages with those
  and answers in detail.
- **Report done AND not-done.** Silence about a skipped part reads as
  success and he has called that out more than once.
- **He tests it for real and reports in blunt terms.** "It is ignoring me",
  "it is rewriting my thing rather than executing it". Those are accurate
  descriptions of real bugs — go to the audit log and find them.

**The recurring failure mode in this codebase is output that sounds right
and is not.** Every module shipped one: the wifi repair claimed a change it
could not make, the attachment guard crashed instead of protecting, a send
reported success with nothing attached, "I played it" for a video that never
started. Test for that first.

---

## Suggested order for finishing

1. **He runs `vault_setup.py`.** Unblocks the entire Phase 4 credential path.
2. **Drive the untested flows once each, with him.** Cheap, and the most
   likely place a real bug is still hiding.
3. **Record real wake samples** if he reports misses — 30 minutes, and it is
   the one thing standing between the wake word and being genuinely his.
4. **Hand-gesture resize**, if he still wants it. Self-contained.
5. **Productisation**, if selling is still the goal. That is a project, not
   a task: onboarding, per-user config, packaging, licence.

---

## Current state, verified 22 August 2026

- **109 tools** — 91 GREEN, 7 AMBER, 11 RED
- **1,637 tests passing**, 43 test files
- `tests/test_every_request_reachable.py` is a **capability ledger** — 31
  capabilities and 19 spoken commands checked for reachability. When a row
  there fails, a feature has gone missing, which is a different kind of
  failure from an assertion changing.
- Autostart and the Ctrl+Alt+J / Ctrl+Alt+K hotkeys are installed
- Wake word: `hey_jalen` **and** `hey_jarvis_v0.1` both load — he asked for
  both names to work everywhere
- `data/weaknesses.md` works and holds exactly ONE entry so far: Jalen
  could not hand a task to "CloudCork". **That entry is now stale** — it was
  Whisper mangling "Claude Code", and both the router and the prompt
  understand it now. Worth deleting, and worth knowing the loop produced a
  real, actionable item on its first outing. Read the file before deciding
  what to build next; it is written by Jalen from real failures.
