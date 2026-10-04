# Jalen — handoff

**HISTORICAL: this is the state of 23 August 2026 (last edited in a731da6).** Its counts,
"NOT done" lists and commands are not current. For today: `docs/collab/STATUS.md`,
`docs/collab/COLLABORATOR_PROMPT.md`, `.\jalen.ps1 todo` and `CLAUDE.md`. Lines 79-111 and
797-816 are still current and `CLAUDE.md` cites them by number: keep those lines where they are.

Read this before touching anything. Then read `ARCHITECTURE.md` for the
design reasoning and `README.md` for what it does.

---

## Orientation in sixty seconds

Jalen is a voice assistant on the user's Windows 10 laptop. Wake word → VAD →
Groq Whisper → **intent router** (~88% of turns locally in an August sample, for zero
tokens) → **safety gate** → Claude Agent SDK with 165 tools (at 833adbb) → edge-tts.

```
jalen/
  app.py            the orchestrator and the mic loop. Big; read run() first.
  runtime.py        single-instance lock, stop/signal files
  crashlog.py       why a process stopped. Read this before diagnosing an exit.
  safety.py         THE GATE. classify() decides GREEN/AMBER/RED/BLACK
  timing.py         per-turn stopwatch — how "slow" became a number
  brain/
    router.py       141 rules at 833adbb, first match wins. Read the ORDERING RULE comment first.
    agent.py        Claude SDK client, system prompt, PreToolUse safety hook
    tools.py        TOOL_SPECS — what the model can see. 1:1 with the registry.
  tools/            37 modules, one REGISTRY each, merged in __init__.py
  audio/            wake, vad, stt, tts (SpeechStream is subtle — read it)
  ui/orb.py         the floating orb
  taint.py          has Jalen read someone else's text since he last spoke? (injection guard)
config/
  jalen.yaml       the DEFAULTS and the design record. Heavily commented;
                    the comments are the reasoning — read them before
                    changing values. Do not put personal settings here.
  user.yaml         per-user overlay, gitignored, optional. Overrides
                    jalen.yaml key by key. Absent = today's behaviour.
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

Run it to see today's count (5,245 collected at 833adbb). One known failure on this machine: the
CapCut case in `test_overhaul_fixes.py`. `tests/test_voice_pipeline.py` calls live Groq and edge-tts —
**re-run it alone before treating a failure there as a regression.**

---

## Disk: resolved, but stay aware

`C:` was at **100% — 0.00 GB free** on 22 August, and a test run died with
`OSError: [Errno 28]` mid-write. He cleared it to 17.7 GB then; on 1 October C: had 7-9 GB free.

That unblocked the last blocked item (mediapipe, ~250 MB); it was removed
again on 23 August with hand gestures. `.\jalen.ps1 check` reports free space as a blocking
problem under 1 GB and a warning under 5 GB, so this cannot silently return.

Two things worth knowing if it tightens again:

- `pip cache purge` and deleting `__pycache__` recover ~250 MB, and Python
  regenerates the latter automatically.
- `temp_file_report` finds ~130 MB across `%TEMP%` and `C:\Windows\Temp`.
  Say *"clear the temp files"* — RED, so it asks first.

## Three invariants from August. Breaking any of these is a security bug.

These fail SILENTLY: nothing errors and tests stay green. The full, current
list is in `CLAUDE.md` under "Invariants"; these three came first.

### 1. The injection guard runs BEFORE destination pre-approval

In `SafetyEngine.classify()` (jalen/safety.py) the `origin == "content"`
checks sit above the pre-approval check. After Jalen has read someone
else's text, RED and AMBER tools and the GREEN tools on `refuse_from_content`
are refused; the one exception is his own Saved Messages when HIS words named it.

Moving the pre-approval block above it turns his ML community channel into
an open relay for anyone who can get words in front of him.
`tests/test_adversarial.py::test_the_ordering_is_structural_not_incidental`
and `tests/test_preapproved_sends.py::test_the_preapproval_check_runs_after_the_injection_check`
assert the ordering, and `tests/test_flow_rehearsal.py::test_an_email_that_asks_to_be_posted_is_refused`
asserts it again through the whole email→post flow.

### 2. Secrets never become tool results

`vault.get_secret()` is deliberately in no REGISTRY and not in TOOL_SPECS.
A tool result reaches the model, the transcript window, the audit log and
possibly the TTS engine. Tools may list secret **names** only; the tools
that type a secret report which one went in, never what it was.

### 3. Jalen never chooses the password field

`fill_credential` (autofill.py, AMBER) types into whatever **he** has
focused; Tab-and-hope once typed into YouTube's search box and wiped it.
The one tool that finds a password field, `fill_login_field` (webforms.py, RED,
in Jalen's own Chrome), lets the page decide: exactly one `input[type=password]`,
on a host with an exact vault approval, with the login tied to that host.

---

## The orb was invisible, and it was not the colour

He reported it twice: *"that orb is still not visible on my screen, no matter
whichever windows I will be"*. It was never a Z-order problem — the window
was mapped, on screen, and `WS_EX_TOPMOST` was set. It was **painting
nothing**, and the cause is worth knowing because nothing about it looks
wrong:

`root.winfo_id()` on Windows does **not** return the top-level window. Tk
returns a CHILD window. So `set_click_through()` was putting
`WS_EX_LAYERED | WS_EX_TRANSPARENT` on the canvas's own child, and a layered
child does not composite against the parent's colour key — it simply stops
painting. The orb went blank the instant it became idle, which is where it
spends almost all its time.

Correcting the lookup was not enough on its own. `tick()` runs once directly
before `mainloop()`, when Tk's window hierarchy does not yet exist, and
`GetAncestor` on an unrealized window returns the handle you gave it — so the
"correct" lookup still returned the child, and because `_click_through_applied`
was then `True` it was never reconsidered for the rest of the session.

Measured on a solid backdrop, production config, idle:

| | orb pixels |
|---|---|
| before | **0** |
| after | **~1,270**, click-through still on |

Three things now guard it: `toplevel_hwnd()` returns `0` rather than a wrong
answer while unrealized, the handle is resolved once and cached, and no
styling happens until it resolves. Also fixed on the way past: the ctypes
calls had no prototypes, so a 64-bit `HWND` could come back truncated.

The orb itself was rebuilt to his reference image — a wireframe globe with a
node mesh, a glowing core, broken arc segments at the rim, and **JALEN**
spelled out underneath. Every state draws the same object and differs by
motion: listening sends waves outward, thinking sweeps an arc around
(circular and internal, so it cannot be mistaken for listening), speaking
pulses on the syllable with level-meter spokes.

`ui.theme.idle` went from `#161c24` to `#2bb3c9`. That is a **deliberate
reversal** of the earlier "black when stopped" instruction, and two tests
were rewritten to assert the opposite. If a future instruction asks for a
dark idle again: that is a request to make the orb invisible.

---

## Three things added after the first handoff was written

### The vault passphrase was leaking, three ways

Saying *"unlock the vault, <passphrase>"* out loud put it in plain text into:

1. `data/audit.jsonl`, a file that is plain text **by design** so it can be
   read in Notepad — sitting next to the vault it opens;
2. the tool arguments, because `SafetyEngine._redact()` covered `password`,
   `token`, `secret`, `key` and `api` but **not `passphrase`**, which is the
   only argument `unlock_vault` takes;
3. **Groq**, because the audio is transcribed there before any of this
   project's code runs.

(1) and (2) are fixed. (3) cannot be — so `unlock_vault_prompt` opens a local
password box instead: keyboard → key derivation, never a transcript, a model,
a tool argument or a log line. AMBER, so the injection guard stops a web page
summoning a fake password prompt. The brain is told to prefer it and **not**
to ask him to say a passphrase aloud.

### He was interrupting himself

Reported as *"it starts talking but suddenly stops"*. In `data/audit.jsonl`,
17 replies ended far earlier than their text needed — and six stopped at
**747, 755, 757, 758, 762 and 764 ms**. Nothing random clusters inside 20 ms
of itself six times.

That is Jalen's own voice reaching the microphone ~750 ms into playback. The
VAD called it speech and barge-in killed playback on the **first frame** over
the threshold: one 32 ms blip ending a 60-second answer. There is no acoustic
echo cancellation here, so three defences compose instead — a 1.2 s grace
period, six *consecutive* frames required, and a higher threshold while
speaking than while listening. Real interruption still lands in ~200 ms, and
every one is now written to the audit log.

### And the fix was then checked against his own live session

He started using it while this was being written, which turned out to be the
best test available. From that session's audit log:

**The fix holds.** Two replies, neither cut short — the long one (752 chars)
spoke for 49.7 s of the ~53.7 s it needed, i.e. it finished. In the old logs
17 replies were cut short, six of them at ~750 ms.

**But it exposed a second bug.** Barge-in fired **22 times in seven seconds**,
at exactly 192 ms intervals — which is `barge_in_frames x frame_ms`, the
moment the counter refilled. The cause: `_speaking` stays set while the
stream waits for the model to write the NEXT sentence, so between sentences
the microphone is open, nothing is actually playing, and any continuous noise
re-triggered the whole branch. Twenty-two `stop()` calls, twenty-two audit
lines, and `collector._reset()` pulling the rug out from under itself each
time.

Fixed with a latch: barge-in fires **once per reply**, and re-arms only when
the speaker genuinely falls silent. `tests/test_barge_in.py` pins both halves
— fires once during a long playback, and is interruptible again on the next
one.

Worth noting how it was found: not by a test, but by reading
`data/audit.jsonl` after he used it. That remains the most productive
debugging tool in this project.

### Speaker identification: built, measured, deleted

He suggested Jalen should recognise its owner's voice. Implemented as MFCC
embeddings and measured against 45 real voices:

| threshold | keeps him | lets strangers in |
|---|---|---|
| 0.20 | 98.6% | **78.4%** |
| 0.70 | 29.0% | 3.7% |

To keep him 97% of the time it admits 78% of strangers; to block strangers it
rejects him 71% of the time. **That is not a working feature**, so it was
deleted rather than shipped. A real version needs a neural speaker model
(~100 MB, torch or an ONNX export) — a genuine piece of work, not a tweak.

---

## Jalen working on Jalen

His ask: *"Jarvis should be able to control itself, its vs code and test it
instead of me."*

`jalen/tools/selfcontrol.py`, plus the coding-agent tools in
`jalen/tools/devwork.py`:

| say | it does |
|---|---|
| *"test yourself"* | runs its own full test suite in the background |
| *"are you ok"* | disk, last shutdown, tools loaded — two sentences |
| *"diagnose yourself"* | the full `jalen.ps1 check` report |
| *"open your code"* | its own source in VS Code |
| *"list coding jobs"* | what Claude Code is working on |

**THE BOUNDARY, and it is the part that matters.** Everything in
`selfcontrol.py` is read-only with respect to Jalen's own code, and
`test_selfcontrol.py` asserts the module contains no write call at all. An
assistant that can edit its own safety gate, its own tier table or its own
audit log — and then report that everything passes — has removed the only
thing that would have told anyone. Changing its own code goes through
`start_coding_job`, in a git repo, against a recorded baseline, where the
diff is visible.

**The full suite runs in the BACKGROUND.** Measured: a subset returns in 2
seconds, the whole suite in 140. Two and a half minutes of a voice assistant
saying nothing is indistinguishable from a hang — the same complaint in a new
place — so it is started, acknowledged, and announced when it finishes.

**`review_coding_job` deliberately does not compute a percentage.** He asked
for "how many percent of my expectations has been met". A number produced by
counting keywords would be this project's signature bug, and a confident 85%
is exactly the kind of answer people stop checking. So it gathers three
things — what he asked for (recorded *before* the job ran), what the agent
CLAIMED, and what `git diff` says actually changed — and hands them to the
brain to judge, with the instruction to judge against the diff. When an agent
says it did the work and changed nothing, the review says **NOTHING CHANGED**.

Two real bugs were found building this, both live:

- headless `claude -p` declines every write and reports it politely, having
  done nothing. Fixed with `--permission-mode acceptEdits` — **not**
  `bypassPermissions`, which would also let it run arbitrary shell commands
  unattended.
- `_git()` returned stderr on failure, so `"fatal: not a git repository"` read
  as truthy and git detection was **inverted**: fresh folders reported
  themselves as already under version control.

---

## Saying his name: stress, and why a list never worked

His report: *"many people give udareniya... not to a in jalen, but they give
it to e"*. English speakers say JA-len; he says ja-LEN. Under
second-syllable stress the FIRST vowel reduces to a schwa and the second
lengthens, so **both halves of the word move at once** — which is why an
enumerated spelling list never kept up.

Two layers, and only one of them could be fixed in software:

**The text layer is done.** `_JALEN` in `router.py` now covers the schwa
(jalen/jelen/jilen/jolen/julen), the lengthened stressed vowel
(jaleen/jalene/jaline/jaleyn), Cyrillic transliteration (dzh-/dz-/zh-/jh-,
for Ж and Дж), an aspirated first syllable (jahlen), and a final nasal heard
as n/m/ng. 51 spellings verified matching, 29 verified NOT matching.

**The acoustic layer cannot be.** The wake model never sees text, and JA-len
and ja-LEN are genuinely different sounds. All 45 synthetic training voices
say it the English way. Two mitigations:

- `wake.threshold` lowered 0.7 → 0.5. Free: ordinary speech measures 0.00%
  false accepts at *every* threshold, so this trades only against a person in
  his room saying a deliberate soundalike.
- `scripts/record_wake_samples.py` now alternates the stress and says which
  one to use on each take. That is the real fix.

**What the wider shape must NOT swallow** is the other half of the work, and
it is the sharper half. From the real audit log, Whisper writes his OWN name
— Jaloliddin — as `jalud`, `jalartan`, `jaloliddin`, and those open exactly
like the assistant's name. He dictates it into messages ("write my name is
Jaluddin to SAT Talk"). Matching it would silently strip his name out of a
message going to a real person. All of them are tested as non-matches.

---

## What changed in this pass

Every item is testable and tested. Numbers are from the suite, not estimates.

| Was | Now |
|---|---|
| Silent exits were undiagnosable | `jalen/crashlog.py`; `python run.py --why` |
| Hand-gesture resize not started | `jalen/ui/gestures.py`, 55 tests, camera path unrun |
| No way to record real wake samples | `scripts/record_wake_samples.py` |
| Six flows never driven end to end | `tests/test_flow_rehearsal.py` + `scripts/rehearse.py` |
| `vault_setup.py` itself untested | 12 tests, including "a typo must not destroy the vault" |
| Config hardcoded to him | `config/user.yaml` overlay, `{home}` expansion |
| No onboarding | `scripts/onboard.py` |
| No packaging / licence / updates | `pyproject.toml`, `LICENSE`, `scripts/update.py` |
| `weaknesses.md` held a stale entry | deleted; verified handled in the prompt first |
| Orb invisible on screen | top-level hwnd fix; 0 → ~1,270 idle pixels, measured |
| Orb looked nothing like his photo | wireframe globe + node mesh + core + JALEN label |
| Listening and thinking looked alike | waves outward vs. a sweeping arc — distinct motion |
| "ja-LEN" stress not understood | shape widened; 51 spellings pass, 29 correctly rejected |
| Wake word misses him | threshold 0.7 → 0.5; recorder now teaches both stresses |
| "hold on" / "carry on" + name dead | phrasal-verb particles were read as prepositions |
| "you can talk" was a dead rule | normalisation ate it before the rule ever saw it |
| Spoken vault passphrase leaked 3 ways | redacted twice over; typed unlock box added |
| Replies cut off at ~750ms | it was hearing itself; grace + sustained frames |
| Gestures "not working" with no clue why | live calibrator, spoken feedback, measured why |
| No way to hand VS Code / Claude real work | 6 tools, background jobs, diff-checked review |
| Jalen could not test itself | 4 self-control tools, read-only by assertion |

---

## What is genuinely NOT done

### 1. Hand-gesture orb resize — REMOVED 23 August (a731da6); kept as a record

mediapipe 1.0.1 is installed and the adapter has run for real. Verified on
this machine:

- the MediaPipe 1.x Tasks adapter builds (the legacy `mp.solutions.hands` API
  is gone in 1.x, as expected — `_make_detector()` handles both)
- it found a hand in **his own reference photo** and measured the pinch
  correctly (`gap=0.023`, well under the 0.35 threshold)
- the live camera path opens, runs, and releases cleanly with no leaked
  threads
- `models/hand_landmarker.task` is downloaded, and `download_models.py` now
  fetches it

**He still reports it as not working for him**, and the measurements say why.
Off his own webcam, 40 frames:

| | |
|---|---|
| hands detected per frame | **1.4** |
| median hand span | **6% of frame width** |
| frames where a hand was pinching | 13 of 57 |

The two-hand gesture needs BOTH hands, pinching, in the same frame. At 1.4
hands per frame that condition is rarely met — he is sitting far enough back
that detection is marginal. The pinch detection itself is fine.

So the gap was never the algorithm; it was that **nothing told him what the
camera could see**. Three things now do:

1. **`scripts/calibrate_hands.py`** — a live preview with the hand skeleton
   drawn on, the measured pinch gap against the threshold, and whether the
   orb is grabbed. Say *"calibrate my hands"* or *"can you see my hands"*.
   Thirty seconds in front of it and the gesture is learnable.
2. It **says** when it first sees hands, and says so if it has been watching
   for twelve seconds and seen nothing.
3. *"Are you watching my hands"* now answers with the live count.

**What he should try:** run the calibrator, sit closer, and watch the line
between thumb and finger turn green. If two hands are hard to hold in frame,
the ONE-hand gesture (pinch, hold half a second, then spread) needs only one.

Still off by default — he confirmed that choice. The webcam only opens when
he asks.

### 2. Why Jalen exited on its own — STILL NOT ROOT-CAUSED

**Do not report this as fixed. It has not been diagnosed.**

What is new is that the event is now *findable* in the log, and that the same
disappearance cannot happen silently again.

The event is session `e6194039`, 21 August 15:47:09. It is the only session
on 21–22 August with no `Jalen stopped` line: four prewarm lines, then
nothing, 15 seconds in. Three structural holes were swallowing the evidence,
and all three are closed:

1. **Autostart has no stderr.** `start_jalen.vbs` runs `pythonw.exe` with
   window mode `0`. Under pythonw `sys.stderr` is `None` and the interpreter
   *discards* tracebacks before anything can see them. Now: `crashlog.install()`
   replaces the missing stream and every unhandled exception — main thread,
   worker thread, and unraisable — lands in `data/crash.log`.
2. **The audit line came last.** `shutdown()` wrote "Jalen stopped" *after*
   `speaker.stop()`, `orb.stop()`, `mic.stop()`. One raising step lost the
   only record that the process had stopped. Now the reason is written first
   and each teardown step is independently guarded.
3. **The loop exit had no reason.** `break`, the mic generator ending, and an
   exception all left identical (empty) evidence. Now `_exit_reason` names
   which, and `data/last_exit.json` carries it across restarts.

So if it recurs: **`.\jalen.ps1 why`**. If it says "ended WITHOUT
shutting down", the process was killed or died somewhere it could not
report, and `data/crash.log` will have a traceback if there was one.

Two candidates I could not rule out, both worth holding in mind: the disk was
full enough to break a write, and the microphone can stop delivering audio
without raising (now detected and logged after 10 s).

### 3. The wake model has never heard HIS voice

Unchanged, and still the single cheapest real improvement available.

`models/hey_jalen.onnx` was trained on 2,160 synthetic positives across 45
edge-tts voices. Measured on held-out synthetic audio: **0.00% false accepts
on ordinary speech at every threshold**, 2.9% missed wake words at 0.7.

**The miss rate on him specifically is unmeasured**, and accent is exactly
what these models are sensitive to. If he says it misses him:

1. Lower `wake.threshold` toward 0.5 first — free, instant.
2. Otherwise record real samples. That is now one command, about fifteen
   minutes:

```powershell
.venv\Scripts\python.exe scripts\record_wake_samples.py
.venv\Scripts\python.exe scripts\train_wake_word.py train --augment 2
```

The recorder rotates the three positive phrasings, and **refuses** takes that
are silent, clipping, too short, too long, or contain no speech per Jalen's
own VAD. That refusal is the point: a bad positive teaches the model that
"hey jalen" sounds like room tone, and it then fires on room tone at 3am. The
trainer's embedding cache is keyed on the clip count, so new recordings
invalidate it automatically and only the new clips cost time.

Known miss: **"Hey Galen" fires it.** G and J are barely separable in most
accents. Accepted deliberately.

### 4. Flows that exist but have never run end to end — HALF DONE

`tests/test_flow_rehearsal.py` (18 tests) now drives each flow through the
**real** `Brain._make_hook()` gate and the **real** tool registry, faking
only the outermost sink. That covers the composition — tiers, ordering,
argument shapes, whether a partial failure is reported — which is where this
project's bugs actually live.

**What it does not cover is the last inch**, and three of these genuinely
need a person:

| Flow | What still needs eyes |
|---|---|
| Email → research → community post | a real opportunity email, and a draft that actually appears in the channel |
| `fill_credential` on a real site | needs the vault, and a real password box with focus |
| `send_posts` with several posts | messages arriving in a chat a human can read |
| `ask_user` through a live voice turn | the mic hearing the answer without a second wake word |
| `clear_temp_files` | that "no" really deletes nothing |
| `hand_off_to_cowork` | the paste landing in Claude's input box |

```powershell
.venv\Scripts\python.exe scripts\rehearse.py
```

It walks them one at a time and records what he observed in
`data/rehearsal.md`. It **refuses a bare "yes"** — "it said it sent" and "it
arrived" are the two things this project keeps confusing, and a rehearsal
that accepted the first would have reproduced the bug it exists to catch.

### 5. The vault had never been created (22 August) — `.\jalen.ps1 todo` item 1 shows today's state

`data/vault.json` does not exist. Nothing is stored, so `fill_credential`
cannot be tested for real. **He needs to run this once, himself** — it asks
for a passphrase with `getpass` and must never be typed by an assistant:

```powershell
.venv\Scripts\python.exe scripts\vault_setup.py
```

What is new: the script itself is now tested (12 tests), driven with
`getpass` faked. Including the case that would be a disaster — **typing the
existing passphrase wrong does not destroy the vault**. It was correct
already; it had simply never been verified, and it is the one script where a
bug is discovered with the passphrase already typed.

`.\jalen.ps1 check` now names this as outstanding, so it is not only in this
document.

### 6. It is built for HIS machine — MOSTLY ADDRESSED, NOT FINISHED

| Was | Now |
|---|---|
| No onboarding | `scripts/onboard.py` — eight steps, resumable, skippable |
| Hardcoded name/paths/channel | `config/user.yaml` overlay + `{home}` expansion |
| The my-voice skill is his | `personal.voice_guide` points anywhere |
| `@Iht_student` in the post format | `personal.channel_handle` |
| No packaging | `pyproject.toml`, `pip install -e .`, `jalen` on PATH |
| No licence | `LICENSE` — all rights reserved |
| No update path | `scripts/update.py` |

**The licence is a decision made on his behalf and he should look at it.**
All rights reserved, because the choice is asymmetric: proprietary → MIT is
always possible, MIT → proprietary never is. If he would rather people could
build on it, that is a two-minute change and the file says how.

**What is still not done for a second user:**

- **No installer.** `pip install -e .` plus onboarding, not one click. The
  project reads `config/`, `data/` and `models/` by path relative to the repo
  root; a proper wheel needs all of those turned into package data or
  user-directory lookups. Real refactor, real chance of breaking a working
  machine, no benefit until a second user exists.
- **Not tested on a second machine.** Every per-user path is unit-tested and
  his own configuration is asserted byte-identical to before, but nobody has
  actually run this as a different person on different hardware.
- **`{home}` is the only placeholder that matters** and Windows-only paths
  are still assumed throughout (`C:/Program Files/Everything/es.exe`, UIA,
  pywin32).
- **No telemetry, no crash reporting, no auto-update.** `scripts/update.py`
  is a `git pull` with backups and a test run; this checkout has no git remote, so it stops at the fetch.

---

## One test that lies when it fails

`tests/test_voice_pipeline.py::test_wake_word_fires_on_true_positive_not_on_true_negative`
synthesises "hey jalen" with edge-tts and feeds it to the wake model. When
edge-tts returns empty or near-silent audio — which it does occasionally —
the model scores ~0.001 and the assertion said *"wake word never fired"*,
blaming the one component that was working.

That is this project's signature bug living inside a test. It now checks the
AUDIO first and fails with *"edge-tts returned near-silent audio — this is a
synthesis failure, not a wake-word failure"*, and the model assertion says
explicitly that the audio was real before blaming the model.

Observed frequency: roughly one run in five, and it passes on a re-run. The
handoff's standing advice holds — **re-run that file alone before treating a
failure there as a regression** — but now the failure message tells you which
kind it was.

---

## Four bugs his 23 August session exposed

All four came out of `data/audit.jsonl` rather than from a test. That remains
the most productive debugging tool in this project, by a distance.

### 1. A turn held a slot for THREE MINUTES

Two turns reported `spoke=197970ms` and `spoke=191657ms` — almost exactly
`_await_playback`'s 180-second ceiling, so that was a timeout, not speech.
He heard *"I'm still on the last one"* three times while nothing was playing.

A streamed reply spends most of its life blocked in `self._q.get()` waiting
for the model to write the next sentence — **a get() with no timeout**.
`Speaker.stop()` set `_interrupt` and nothing else, so the loop that would
check the flag was asleep, `_speaking` stayed set, and everything waiting for
playback waited out the whole ceiling.

`stop()` now sets the flag **and** pushes the existing `_WAKE` sentinel into
the stream's queue. Measured: **180 s → 11 ms**.

### 2. The orb showed the wrong thing while working

*"It is saying I'm on the last one, but the orb is orbiting like in blue...
should not it have been green while it is working?"* He was right. Six places
called `orb.set_state()` and the last one to fire won — so any noise in the
room took the follow-up branch, set `listening`, and the orb sat blue for the
whole three minutes.

There is now one authority, `_refresh_orb()`, and a priority order:

    muted → speaking → working → listening → idle

**Working beats listening**, which is the whole fix. The speaker is now an
*input* to that decision rather than a caller of it: wired straight to
`set_state` before, the end of a sentence set the orb to `idle` while the
turn behind it was still going.

### 3. "The full text is on screen" was true exactly once per session

His words: *"there is no full text on the screen"*. Jalen's own reply admitted
it: *"I said full detail is on screen but never actually wrote it anywhere"*.

`TranscriptWindow.show()` queued the text and started a worker **only if one
was not already running**. The worker read ONE item, opened a window, and
called `mainloop()` — which never returns while the window is open. Every
subsequent long answer went onto a queue nobody was reading. Nothing errored.

Verified before fixing: two `show()` calls, queue depth 1 afterwards. It is
now one window that stays, polling its own queue with `after()` from inside
the event loop, appending each answer at the top. Verified after: **5 of 5
rendered, 0 stranded.**

### 4. Handing off produced thin briefs

*"It should have given a master prompt like a prompt engineer with at least
10 years of experience."* The tool descriptions already said "a complete,
self-contained brief" — descriptions are advice, and advice gets followed
about a third of the time.

There is now a written standard (`master_prompt_guide`) and, more usefully, a
**gate**: `delegate_task`, `hand_off_to_cowork`, `hand_off_to_code` and
`start_coding_job` all REFUSE a brief missing context, success criteria or
constraints, and name the missing part. One standard for every destination —
having two bars would just mean the lower one gets used.

---

## Handing work to Gemini and ChatGPT

*"It should be able to chat with chatgpt like a real human... compare it
against my expectations, and give it another prompt that will fix it."*

`jalen/tools/agents.py`. The loop:

| | |
|---|---|
| `delegate_task` | send a gated brief, record what he actually wanted |
| `review_delegation` | lay out want vs ask vs answer, for the brain to judge |
| `follow_up_task` | reply into the SAME conversation, with its history |
| `list_delegations` | what has been handed out, and how many rounds |

`review_delegation` **does not score anything**. He asked for "how many
percent of my expectations has been met", and a number produced by counting
keywords would be this project's signature bug — a confident 90% is exactly
what people stop checking. It gathers the evidence and tells the brain to
judge point by point, then WRITE the follow-up prompt and send it.

**Two live findings.** The pinned model name `gemini-2.5-flash` was already
retired — Google returned *"no longer available to new users, please update
your code"*. Both model names are now aliases (`gemini-flash-latest`), because
a pinned version is a time bomb that goes off in his hands rather than in a
test. And then his key returned **403 PERMISSION_DENIED — "your project has
been denied access"**, which is an account problem no code can fix; the error
now says so and offers Claude Code instead.

ChatGPT works the moment `OPENAI_API_KEY` is in `.env`; the library is
installed. Without a key it says so and offers the clipboard route rather
than pretending.

---

## `.\jalen.ps1 can` — the capability list

He asked for a list of everything Jalen can do, to test against. It is
GENERATED from the live tool registry, safety tiers and router rules, so it
cannot promise something that no longer exists — this project has already
shipped documentation that outlived the code it described. It prints its own count
(147 in August), with checkboxes and a `*` on the ones that answer by voice for free.

---

## The gap-closure pass: four more real bugs

### The "flaky network test" was a test bug all along

`test_wake_word_fires_on_true_positive_not_on_true_negative` failed roughly
one run in three for days. It was re-run each time and excused as edge-tts
flakiness — including by me, in an earlier report. That was wrong.

edge-tts returns "Hey Jalen" with about 1.4 s of trailing silence most of the
time and with almost none occasionally. **The speech is identical either way
— 0.36 s, measured.** openWakeWord scores a ~1.96 s window that has to CLOSE
after the phrase, so with no tail the window never completes and the score
sits at 0.001 instead of 1.000.

The obvious fix — pad the front — changed nothing, which is worth recording:

| | score |
|---|---|
| truncated to 0.57 s | **0.000** |
| + 1.4 s LEADING silence | **0.000** |
| + 1.4 s TRAILING silence | **1.000** |

Padding at the end made it 8/8. The whole suite is now deterministic for the
first time: **2,721 passing, zero failures**, network tests included.

### Gmail sorted the wrong thing, and it was this project's fault

His words: *"I said you to sort out my emails about machine learning
committee."* Jalen's own account: *"I sorted your Eco Pulse research outreach
because that's what filled the inbox."*

The cause was `scan_inbox`, not the model. It sorted every message into
WORTH A LOOK / ordinary / automated, where "worth a look" is `_PROMISING` —
`research|lab|collaborat|opportunit`. That is precisely his Eco Pulse
outreach. The tool handed back a list already sorted under a confident
heading, answering a question he had not asked, and the brain sorted what it
had been given.

**A tool that pre-judges relevance is answering its own question, and its
answer is the one that gets used.** `scan_inbox` now takes `about` — what he
actually asked for — and buckets against that. Measured on the exact
confusion:

| | generic sort | with `about="machine learning community"` |
|---|---|---|
| "Research collaboration on PM2.5" | promising | **other** |
| "ML community meetup" | other | **promising** |

Without `about` the heading now says so out loud, and warns not to present a
generic sort as an answer to a specific question.

### "What windows are open" listed Chrome tabs

The browser-tab rule matched a bare `windows?` and sits above
`get_window_list`, so a plain question about the desktop was answered with
browser tabs. Found by `tests/benchmark_phrasing.py`, which is excluded from
the normal run and had been failing quietly at 89/90. Now 95/95.

### "Summarise my emails" reached nothing at all

`config/safety.yaml` has carried the comment `unread_email_summary  #
"summarise my emails"` since that tool was written, and no router rule ever
matched the phrase. Found by trying the phrase the comment advertised.

---

## What was probed rather than assumed

`.\jalen.ps1 ready` opens no windows and sends no messages, but it does make
one real call to each model provider. Result on this machine:

**13 AVAILABLE · 3 PARTIAL · 2 EXTERNAL BLOCK**

The two blocks are not code problems and no code change will fix them:

- **Gemini** — Google returns `403 PERMISSION_DENIED: your project has been
  denied access`. A fresh key from aistudio.google.com/apikey.
- **ChatGPT** — no `OPENAI_API_KEY`. The library is installed and the code
  path is written; it works the moment a key exists.

Also new: `.\jalen.ps1 disk` classifies every large folder as SAFE TO
REGENERATE / SAFE WITH CONFIRMATION / DO NOT TOUCH, and deletes nothing. His
own Desktop folders are listed for context only.

## Security, probed adversarially

`tests/test_adversarial.py` — 88 tests that attack rather than exercise. 48
injection combinations across six tools, lookalike domains
(`accounts.google.com@evil.tld` and friends), secret-shaped arguments,
exfiltration by attachment, confirmation spoofing, and posture downgrade.
All blocked. The injection-guard ordering is asserted structurally as well as
behaviourally, because two checks passing today does not stop someone
swapping them tomorrow.

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
| One-hand gesture is a LATCH, not a hold | You resize by *opening* your fingers, which breaks the pinch. If the pinch also held the grab, a one-hand gesture could never grow the orb — it would release the instant it started working. An open palm releases instead, measured on the other four fingers. |
| The gesture deadband is proportional, not fixed | The noise is landmark jitter multiplied through a scale factor, so it is a percentage of the orb, not a pixel count. Measured: ~45% of a 2% wobble survives the low-pass — 2.7px at 300px, 8px at 900px. One fixed threshold cannot sit above both. |
| `user.yaml` lists REPLACE, they do not merge | Those lists are allowlists — safe folders, pre-approved send destinations. "These are my folders" must not silently mean "mine and his". |

---

## How he works, and what he has said about it

Worth knowing before you propose anything:

- **He wants depth per feature, not breadth.** Finish one thing properly
  before starting the next. He has said this explicitly.
- **Implement obvious improvements without asking.** Reserve questions for
  genuine forks (privacy, what goes public, money, accounts) and ask them as
  a poll: 2-4 labelled options, the recommended one first.
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

## Start here: `.\jalen.ps1 todo`

He said it plainly — *"I still do not know how to do this man, could you
please navigate me"* — and he was right. Five outstanding items handed over
as five script paths is homework, not navigation. A path only helps someone
who already knows what the script does, how long it takes, and why it is
worth doing.

So there is now one command that reads the machine's real state and prints
only what is actually outstanding, each with what will happen when he runs
it and how long it takes:

```powershell
.\jalen.ps1 todo
```

Every task is one word:

| | |
|---|---|
| `.\jalen.ps1 vault` | create the password vault (he types the passphrase) |
| ~~`.\jalen.ps1 hands`~~ | removed with hand gestures (a731da6); now prints "Unknown command" |
| `.\jalen.ps1 voice` | teach the wake word his own voice |
| `.\jalen.ps1 rehearse` | drive the six flows that need him present |
| `.\jalen.ps1 licence` | decide who may use this, and record it (deferred at his request) |

The checklist ticks itself from real evidence, not from having run the
script. `calibrate_hands.py` (removed on 23 August) wrote its marker **only
after it had actually seen a hand**, and reported what it saw: how many hands at
once, the best pinch gap against the threshold, whether the orb was ever grabbed. A marker
written merely because a window opened would tick the box for someone who
learned nothing — the same "reported success, nothing happened" failure this
project keeps producing, moved into a checklist.

---

## What is left, and who has to do it

Everything that could be finished without him is finished. What remains needs
his hands, his voice, or his judgement — that is not a way of avoiding work,
it is the actual shape of what is left.

### Needs HIM (about an hour, total)

1. **`scripts\vault_setup.py`** — thirty seconds. It asks for a passphrase
   with `getpass`, and a passphrase that passes through an assistant is one
   the assistant could have kept. Unblocks the whole credential path.
   **Pick a fresh one**: an earlier passphrase was typed into a chat window.
2. ~~`scripts\calibrate_hands.py`~~ — removed with the hand-gesture feature
   on 23 August (a731da6), at his request. The orb no longer resizes at all:
   its size and place are `ui.orb_size` and `ui.orb_position` in
   `config/jalen.yaml`. `.\jalen.ps1 hands` now prints "Unknown command".
   Nothing here needs him any more.
3. **`scripts\rehearse.py`** — one sitting. Six flows that have never been
   driven end to end by a person. Cheapest place a real bug is still hiding.
4. **`scripts\record_wake_samples.py`** — fifteen minutes, if the wake word
   misses him. It now alternates JA-len and ja-LEN and says which to use.

### Needs a DECISION from him

5. **The licence.** `.\jalen.ps1 licence` states the two options in plain
   terms, writes the file for him, and records that he decided. All Rights
   Reserved today, chosen on his behalf because the choice is one-way:
   closed → open is always possible, open → closed never is, since every
   copy already distributed keeps its licence forever.

### Genuinely unfinished engineering

6. **The 21 August silent exit is still not root-caused.** It has not
   recurred. What changed is that a recurrence is now *findable*:
   `.\jalen.ps1 why`, `data/crash.log`, `data/last_exit.json`.
   **Do not report it as fixed.**
7. **Speaker identification** would need a neural model (~100 MB). The
   numbers for the cheap version are in the section above and they do not
   support shipping it.
8. **Productisation** — an installer, and a second machine to test on. Every
   per-user path is unit-tested and his own config is byte-identical to
   before, but nobody has run this as a different person on different
   hardware. That is a project, not a task.

---

## State on 22 August 2026 (HISTORICAL — today's state is in docs/collab/STATUS.md)

- **125 tools** — 100 GREEN, 14 AMBER, 11 RED (at 833adbb: 165 — 125 GREEN, 24 AMBER, 16 RED)
- **2,721 tests passing** then (zero failures, including the live-network ones), 60 test files (was 1,637 across 43); at 833adbb: 136 test files, run the gate for the count
- `tests/test_every_request_reachable.py` is a **capability ledger** — when a
  row there fails, a feature has gone missing, which is a different kind of
  failure from an assertion changing. Hand control is now in it, in both
  directions: the OFF phrase contains the ON phrase, so a rule reordering
  would leave him with a camera he cannot switch off by voice.
- Autostart and the Ctrl+Alt+J / Ctrl+Alt+K hotkeys are installed
- Wake word: `hey_jalen` **and** `hey_jarvis_v0.1` both load
- `data/weaknesses.md` is now **empty**, deliberately. Its one entry —
  Jalen could not hand a task to "CloudCork" — was Whisper mangling "Claude
  Code". Verified handled before deleting: the router deliberately does not
  route it (the brain needs the conversation to write the prompt) and
  `agent.py`'s system prompt names the mis-transcription explicitly. Both
  are pinned by tests in `test_handoff.py`.
- New diagnostics: `.\jalen.ps1 why`, and `.\jalen.ps1 check` now reports
  the Claude sign-in, Google and Telegram, disk, the vault, wake-word
  recordings, rehearsal progress, and how the last run ended.
- `tests/test_conversation_requests.py` is a **second ledger**, and a
  different one: it asks "did we build what he asked for", tagged by the
  message each request came from. 59 rows at 833adbb. A failure there means a
  capability he explicitly asked for has stopped existing.
- **Jalen can test itself**: say "test yourself" and it runs its own full
  suite in the background and tells you the result. Read-only with respect
  to its own code, asserted by test.
- **Hand gestures were removed on 23 August** (a731da6), and orb resizing
  after them: the orb's size and place are `ui.orb_size` / `ui.orb_position`
  in `config/jalen.yaml`. Nothing opens the camera.
- **Coding agents**: "start a coding job" runs Claude Code headless for up
  to an hour, announces when it finishes, and "review the coding job"
  compares the git diff against what was asked.
