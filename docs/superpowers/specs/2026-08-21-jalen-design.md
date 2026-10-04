# Jalen — design

Date: 2026-08-21
Supersedes nothing. Extends the 72-point spec that produced Jarvis.

## Why this document exists

Nine requests arrived in one message. Mapped honestly they are eight
independent subsystems, and specifying eight subsystems at once produces a
document nobody can implement. This decomposes them into four phases, built
strictly in order, each finished and tested before the next begins.

Decisions taken by the user on 2026-08-21:

| Question | Answer |
|---|---|
| How it starts | Autostart at login **and** a global hotkey (Ctrl+Alt+J) |
| Wake word | Train a real "Hey Jalen" model |
| Build order | Strictly sequential, 1 -> 2 -> 3 -> 4, test hard between |
| Improvements found mid-build | Poll **or** just implement; report what was done |
| Credential posture | Vault + allowlist + audit, **plus** spoken "once or always?" |

## The four phases

1. **Identity, launch, exit, latency.** Rename to Jalen, the launcher,
   name-first commands, and making "slow" a measured number.
2. **Comms.** Telegram send-jargon, unread digests, and the
   email -> research -> channel-post-as-draft pipeline.
3. **Technician.** Autonomous Windows diagnosis and repair.
4. **Agency.** Credentials vault, form filling, essays in his voice,
   browser authentication, ask-and-wait.

One cross-cutting piece is built in Phase 1 rather than last: the
self-evaluation and weakness log (request 9). Built first, it spends phases
2 through 4 accumulating real evidence about what Jalen cannot do — which is
precisely the point of it. Built last, it would start empty.

---

# Phase 1 — Identity, launch, exit, latency

## 1.1 Rename: Jarvis becomes Jalen

**What changes.** Every string he can hear or read: the persona prompt, the
spoken name, config `identity.name`, the wake phrase, router replies, the
launcher, the orb title, the transcript window, the Telegram bot's replies,
docs.

**What deliberately does not change.** The Python package directory stays
`jalen/`. Renaming it rewrites every import in 37 modules and 22 test
files, invalidates the `.venv` and the on-disk paths in
`data/jalen.lock`, and buys nothing a user can perceive. Internal module
paths are not product surface. This is recorded here so the decision is
visible rather than looking like an oversight.

**Legacy alias.** Speech recognition and years of habit both still produce
"Jarvis". Every place that matches the name accepts `jalen|jarvis`, so an
old habit is understood rather than dropped on the floor. Jalen always
*answers* as Jalen.

## 1.2 Launch without typing

Two mechanisms, both chosen:

**Autostart at login.** `scripts/install_autostart.py` already writes a
`.vbs` shim and a Startup shortcut, and has never been run. It is corrected
(it currently launches `run.py` with no flags, which starts muted per
config) and run.

**Global hotkey.** `config/jalen.yaml` has promised
`kill_switch_hotkey: "ctrl+alt+space"` since day one, labelled
`[NOT IMPLEMENTED]`. A single low-level keyboard listener now serves both
that promise and this one:

- `Ctrl+Alt+J` — start Jalen if dead; wake him if idle; mute him if speaking.
- `Ctrl+Alt+Space` — the kill switch that was always documented.

The listener is a separate, tiny process so that it survives Jalen crashing
and can therefore restart him. A hotkey that dies with the thing it is
supposed to launch is not a launcher.

## 1.3 Name-first commands, and "See you, Boss"

**The bug.** `brain/router.py:175` matches
`^(quit|exit|shutdown|close|kill|turn off)( jarvis| yourself)?$`. The verb
must come first. "Jarvis quit" — and therefore "Jalen quit" — matches
nothing, falls through to the LLM, and costs seconds and tokens to do what a
regex should have done in 50ms. The audit log confirms bare "quit" reaches
"Shutting down. See you, Boss." correctly, which is why this read as
intermittent rather than broken.

**The fix, generalised.** Rather than adding one more alternation to one
regex, an optional name prefix is stripped from every utterance before
routing: a leading `jalen|jarvis` followed by optional punctuation. Then
"Jalen quit", "Jalen, quit", "quit Jalen" and "quit" all reach the same
rule, and so does every other one of the 60-odd router patterns — "Jalen open
chrome" works for free.

**The words.** Exit says `"See you, Boss."`

## 1.4 Latency: measure, then cut

**What the log actually shows.** The 07:26 turn spans 43 seconds between his
utterance and Jalen's. That is *not* 43 seconds of dead air:
`SpeechStream.close()` blocks until playback ends and `audit.utterance` is
written after it, so the interval is think-time plus the full spoken
duration of a long answer.

**The real defect.** There is no per-turn timing at all. Only startup
prewarm is instrumented (`app.py:225`). "It was slow to answer" and "it
talked for far too long" are indistinguishable in the record, which is why
this complaint keeps returning without ever being fixed.

**What gets built.**

- A `TurnTimer` recording four marks per turn: speech ended, transcript
  ready, first audio out, playback finished. Written to the audit log as
  one `timing` record.
- `jalen timing` / "how fast were you" reports the last turn and the
  session median from those records.
- `tts.max_spoken_chars` drops from 700 to 320. At `rate: +18%`, 700
  characters is roughly 40 seconds of continuous speech; nobody wants a
  spoken paragraph. Longer answers already have a home — the transcript
  window — and the persona prompt already says to use it.
- The persona gains one hard rule: answers about Jalen himself, his limits,
  or his configuration get two sentences, not two paragraphs. The 43-second
  turn was exactly that kind of question.

**Explicitly not done here.** No re-tuning of `vad`, `wake` or `stt`. Those
were tuned against measured data in commit 6873023 and are not implicated.
Phase 1 adds the instrument; any further cut is made against its readings,
not against a guess.

## 1.5 Self-evaluation and the weakness log

After any turn that used the brain (router hits are excluded — they are
solved by definition), Jalen records an honest self-assessment.

- Storage: `data/weaknesses.md`, plain Markdown, openable in Notepad, one
  section per entry: what was asked, what happened, what was missing.
- Trigger: automatic on failure or refusal; on request via "how did that
  go".
- Content rule: it names the missing *capability*, not the excuse. "No tool
  can set a Windows service to delayed start" is useful. "I was unable to
  complete that request" is not.
- Read path: a `review_weaknesses` tool, so "what can't you do yet" is
  answerable out loud, and so phases 2-4 start from evidence.

## 1.6 The "Hey Jalen" wake model

No pretrained model exists for this phrase. One is trained here.

**Approach.** openWakeWord classifies on top of two frozen feature
extractors that are *already in this repo*: `models/melspectrogram.onnx`
and `models/embedding_model.onnx`. Only the small classification head needs
training.

**Positive data** comes from `edge-tts`, already a dependency, which offers
hundreds of voices. Sweeping voice, rate and pitch produces thousands of
distinct utterances of "Hey Jalen" at no cost and no API key. Augmentation
adds room noise and gain variation so the model does not only recognise
studio-clean speech.

**Negative data** comes from the same generator saying confusable phrases —
"hey Alan", "hey Jason", "hey Helen", "Jalen" alone — plus ambient audio.
Confusable negatives are what stop it triggering on the television.

**Delivery.** `scripts/train_wake_word.py` runs unattended and writes
`models/hey_jalen.onnx`. `wake.enabled` and `wake.model` already come from
config, so the swap is a one-line config change once the model validates.
Until it validates, the existing `hey_jarvis` model keeps waking him and
Jalen answers as Jalen. A wake model that fires on the TV is worse than an
old wake word, so it ships only when measured against held-out audio.

## Testing

`pytest.ini` and 22 existing test files set the standard; this follows it.

- Rename: a test asserting no user-visible string says "Jarvis", and that
  both name aliases route identically.
- Name-prefix stripping: table-driven over every router pattern, asserting
  bare / "jalen X" / "jalen, X" produce the same intent.
- Exit: "jalen quit", "quit jalen", "quit", "shut down" all reach
  `jalen_quit` with "See you, Boss."
- Hotkey: the resolver is tested as a pure function; the OS hook is not
  unit-testable and is verified live.
- Timing: `TurnTimer` tested with an injected clock.
- Weakness log: append, read back, and no-duplicate-on-repeat.
- Wake model: held-out positive/negative audio, with a stated
  false-accept and false-reject rate. No rate, no ship.

Three existing tests call live Groq and edge-tts and fail on a flaky
network rather than on a regression; they are re-run before any failure is
treated as real.

---

# Phases 2-4 — scope only

Detailed designs are written when each phase begins, against what the
weakness log has learned by then.

**Phase 2 — Comms.** Router grammar for "telegram X about Y" and "tell X
that Y" (drawn from the real unmatched phrasings in
`data/router_misses.log`). Unread-since-last-seen digests for chats and
channels. The channel-post composer, modelled on his existing post format:
title, intro with bold entities, sectioned bullets, an expandable Q&A
block, and a DM footer. Saved as a Telegram **draft** via `SaveDraftRequest`
— never sent. The email path reads the offer, researches the lab or program,
explains it in plain words, asks permission, then drafts.

**Phase 3 — Technician.** A diagnose/repair loop over evidence sources
Windows already exposes: Event Log, `netsh wlan show wlansettings`, driver
power-management flags, Windows Update state, Defender status, disk and
SMART. Findings are ranked by confidence, each with a named fix. The
existing four-tier gate governs the fixes: reading is GREEN, a reversible
setting change is AMBER, anything installing or deleting is RED. The
wifi-every-five-minutes case is the first target and the acceptance test.

**Phase 4 — Agency.** An encrypted credentials vault (never `.env`, never a
plaintext Desktop folder). A per-domain approval store answering "once or
always", asked out loud, remembered. Form filling and essay writing through
the existing `voice_guide` / `/my-voice` path. An ask-and-wait primitive
that blocks execution until he answers, which nothing in the current
codebase can do.
