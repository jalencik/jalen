# Jalen — what he can actually do

*An audit of the whole system, 2 September 2026. Written from the code, the
git history, and — crucially — from his own runtime logs.*

---

## How this report was made, and why that matters

This project has an unusual problem for a capability report: **it documents
itself, prolifically, and its documentation is out of date.** `README.md`,
`ABILITIES.md`, `WHAT_JALEN_CAN_DO.md`, `HANDOFF.md`, `PROCESS.md` and
`ARCHITECTURE.md` total ~4,000 lines and disagree with each other and with the
code on almost every number they state.

So nothing here is taken from a document. Every claim comes from one of three
sources, and each claim says which:

| Source | What it proves | What it cannot prove |
|---|---|---|
| **The code** (24,296 lines across 87 Python files) | A capability exists and is reachable | That it works on a real machine |
| **The test suite** (3,182 tests) | A capability behaves under mocks | That anyone has ever used it |
| **The runtime logs** (`data/audit.jsonl`, 4,740 lines over 13.7 days) | What Jalen has *actually done* | Anything he was never asked to do |

Sixteen parallel agents read every module, every script, every config key and
all 108 commits; then re-measured the headline claims by running commands; then
mined the runtime logs; then a critic diffed the whole inventory against the
live tool registry for gaps. Where they disagreed, the disagreement is reported
rather than smoothed over.

**Bottom line up front:** Jalen is a genuinely large and unusually
well-engineered system — 153 tools, a four-tier safety gate that really does
gate, a Chrome extension living inside the owner's real browser, an encrypted
vault, a machine technician, and 3,182 passing tests. It is also a **prototype
with a prototype's amount of real use**: 565 real tool invocations in 13.7 days,
22% of which were asking the time, and 63% of its declared capability surface
has never once been invoked.

---

## 1. The verdict in one page

### What is real and works
- **Desktop control is excellent.** Opening things by the names a person
  actually says is the best-built part of the system: 65/65 router matches and
  64/64 resolutions against a real benchmark on this machine.
- **The safety architecture is real, not decorative.** Every tool call passes a
  single classifier before it runs, on both the router path and the brain path.
  The runtime log shows 27 real confirmations refused by the owner and one hard
  block — the gate is doing work in production.
- **The browser work is serious engineering.** Two independent mechanisms (a
  CDP-attached Chrome, and a custom MV3 extension talking over native messaging
  to the running app) with a 21-command allowlist and no arbitrary-JS escape
  hatch.
- **The test suite is real.** 3,182 tests, all passing, in 10m 11s — verified
  in this audit, not quoted.

### What is claimed and is not true
- The **router hit rate is 20–27%**, not the advertised ~88%.
- The **router is not faster than the brain** on real turns (4,354 ms vs 4,504 ms
  p50). It saves tokens; it does not save time.
- **Every headline number in the README is wrong**, most by ~50–100%.
- **Three of the five runtime evidence files are contaminated by the test
  suite** — including the one the readiness report cites as proof that
  delegation works.

### What is broken right now
- **Gmail and Calendar are dead** — the OAuth token is expired/revoked.
- **Every non-Claude model is unreachable** by both the API route and the
  browser route.
- **35 capabilities are advertised and defective**, 11 of which do literally
  nothing while reporting success.
- **One confirmed security hole** lets content Jalen reads reach a pre-approved
  Telegram send with no confirmation.

### The owner's own verdict, from `data/router_misses.log`
> *"you're only good at opening chrome and telling me the time."*

The runtime evidence agrees with him. `get_time` is 22% of all real tool use.

---

## 2. The numbers, re-measured

Every figure below was measured during this audit with the command shown.

| Claim | Where it is stated | Measured | Verdict |
|---|---|---|---|
| 102 tools | `README.md:4` | **153** | refuted (50% low) |
| 145 tools | `ABILITIES.md`, `WHAT_JALEN_CAN_DO.md`, `PROCESS.md` | **153** | stale by 8 |
| 1,545 tests | `README.md:4` | **3,182** | refuted (2.06×) |
| 2,982 tests | `scripts/acceptance.py:26` | **3,182** | stale |
| 2,200 tests | `jarvis/tools/selfcontrol.py:87` | **3,182** | stale |
| GREEN 86 / AMBER 6 / RED 10 | `README.md:124-126` | **142 / 23 / 27 / 16 BLACK** names | refuted |
| ~88% of turns stop at the router | `README.md:13`, `ARCHITECTURE.md:71`, `HANDOFF.md:16` | **20.5%** logged / **27.2%** on replay | refuted (3–4× optimistic) |
| Wake word tested on 2,084 held-out clips | `README.md:147` | **exactly 2,084** | confirmed |
| 0.00% false accepts at every threshold | `README.md:147` | reproduced to the decimal — **but see §9** | confirmed, misleading |
| Router decides in ~50 ms | `jarvis/brain/router.py:11` | **0.30 ms** mean over 835 real utterances | refuted (better than claimed) |

**Verification commands** are logged in Appendix B.

### Tool counts, reconciled

Two agents produced different tier counts and both were right. They counted
different things, so here is the reconciliation:

```
config/safety.yaml declares 208 names:
    GREEN 142   AMBER 23   RED 27   BLACK 16

Of those 208, only 153 are tools that exist:
    GREEN 118   AMBER  21   RED 13   BLACK  0    (= 152)
    + web_sign_in, which has no tier at all       (= 153)

The other 56 names fall into three groups:
    • 24  router/app-level intents, not registry tools (morning_brief,
          private_mode, set_posture, reload_config…) — these work
    • 16  the ENTIRE black list — no implementation, by design (see §5)
    •  7  advertised and genuinely absent: set_reminder, archive_email,
          label_email, read_github, read_notion, create_github_issue,
          write_notion_page
    •  9  stale renames (jalen_quit vs quit, etc.)
```

`jarvis/tools/REGISTRY` (153) and `jarvis/brain/tools.py TOOL_SPECS` (153) are
**identical sets**, and a startup assertion at `jarvis/brain/tools.py:1052-1058`
enforces that. That is a genuinely good piece of design: a tool present in one
but not the other is a startup error, not a mid-conversation surprise.

---

## 3. What Jalen is

### The turn, end to end

```
  "Hey Jalen"   →  wake (2 ONNX models, max score)
                →  VAD endpoint (Silero, 1400 ms fast / 4000 ms patient)
                →  STT (Groq whisper-large-v3-turbo, 8 s bound
                        → moonshine locally if Groq fails)
                →  pronoun expansion, plan extraction, kill-phrase check
                →  ROUTER  (136 regex rules → 89 tools, 0.3 ms, zero tokens)
                     ↓ miss (four times in five)
                →  HABITS  (never fires — see §8)
                     ↓
                →  BRAIN   (Claude Agent SDK, 153 MCP tools)
                →  SAFETY GATE  (GREEN / AMBER / RED / BLACK)
                →  tool call
                →  TTS (edge-tts, sentence-by-sentence, barge-in enabled)
```

### The machine it was built for

`ARCHITECTURE.md` opens with the constraint that explains almost every technical
choice: **8 GB of RAM, ~1 GB free, no NVIDIA GPU.** That rules out a local LLM,
local Whisper above `tiny`, local neural TTS, PyTorch anywhere, and any vector
store that wants to be a service. So Jalen is cloud-first by necessity, with a
~350 MB local footprint of ONNX models sharing one Python process.

This constraint is load-bearing and worth preserving: several of the oddest
decisions in the codebase (extracting Silero's ONNX out of a wheel rather than
`pip install silero-vad`; training a wake word in pure NumPy with no PyTorch;
parsing `.docx`/`.pptx`/`.xlsx` as raw OOXML zips rather than adding a
dependency) exist to stay inside it, and each is documented with its reason.

### The stack

| Layer | Choice | Notes |
|---|---|---|
| Wake word | openWakeWord 0.6.0 (ONNX), two models | `hey_jalen` trained in this repo; `hey_jarvis` pretrained |
| Endpointing | Silero VAD v5, bare `.onnx` | 2.3 MB, avoids a multi-GB torch dependency |
| Speech → text | Groq `whisper-large-v3-turbo` | `moonshine` TINY_STREAMING as offline fallback |
| Brain | Claude Agent SDK on the Claude Code subscription | no `ANTHROPIC_API_KEY` needed — see §10 |
| Text → speech | edge-tts, `en-US-AndrewNeural` | decoded with PyAV, cached in RAM + on disk |
| Desktop control | `uiautomation` + `pywin32` | UIA tree as text, cheaper than vision |
| Browser | Playwright attached over CDP + a custom MV3 extension | two independent paths |
| Telegram | Telethon (personal) + aiogram (bot) | two different things, see §4.4 |
| Memory | fastembed bge-small (384-dim) + sqlite-vec | local, no API |
| Orb | tkinter | ~18 MB vs ~90 MB for Qt |

---

## 4. What he can do, by domain

The audit catalogued **673 discrete capabilities** across twelve subsystems:

| Status | Count | Meaning |
|---|---|---|
| works | 492 | complete code path, tested |
| partial | 109 | works in a narrow case only |
| broken | 35 | a real defect was found and confirmed |
| unverified | 24 | looks complete, could not be confirmed |
| stubbed | 13 | signature exists, does nothing real |

A caution the critic raised and I agree with: **do not add those numbers up as a
headline.** At least 11 of the 673 rows are features that the same agent's own
limitations section says do not exist (hand gestures, tray icon, offline TTS).
They are catalogued so the report is complete, not because they are capabilities.

### 4.1 Voice and presence

**Works.** Wakes to "Hey Jalen" *or* "Hey Jarvis" — both models load and the
max score across them fires, because habit is real and the old name was in the
owner's muscle memory. Wake stays live while paused, so a spoken "resume" can
be heard. A wake hit does **not** discard the rest of the sentence: "Hey Jalen,
open Chrome" spoken in one breath keeps the command, because draining the mic
backlog was measured to throw away 0.99 s of a 1.78 s phrase.

Endpointing is two-stage and the reason is measured: 55% of 409 real utterances
are six words or fewer, and only 1.0% end on a word implying more is coming — so
the common case closes at 1,400 ms and only a transcript ending in a conjunction
buys the patient 4,000 ms.

Speech is streamed sentence-by-sentence off the model's token stream, so the
wait ends at the first sentence rather than the last. Barge-in cuts within
~50 ms via three composed heuristics, and it needs heuristics because **there is
no acoustic echo cancellation anywhere in the project** — Jalen can hear
himself.

"Read it all" removes the 320-character spoken cap for one turn.

**The wake word was genuinely trained here**, and that work is impressive:
`scripts/train_wake_word.py` renders 2,160 positives and 2,250 negatives across
45 edge-tts voices × 4 rates × 3 pitches, embeds them through openWakeWord's
frozen feature extractors, and fits a 1536→128→128→1 head in pure NumPy with
Adam — no GPU, no PyTorch — then exports ONNX with normalisation baked in as
Sub/Div nodes.

**But see §9.** The published accuracy table does not describe the running
system, and the model has never heard the owner's voice.

**The orb** is three tkinter windows on three threads: a chroma-keyed
always-on-top overlay, a scrolling "full answers" window, and a modal vault
passphrase box. It is unconditionally click-through in every state with zero
mouse bindings — the permanent fix for the reported "my cursor stops working"
bug. Six states are distinguished by colour and motion, arbitrated in one place
on a priority order where *working beats listening*.

**Off-screen control**: `Ctrl+Alt+J` wakes him from anywhere (and launches him
if he isn't running), `Ctrl+Alt+K` kills him. Both are registered by a separate
always-running process via Win32 `RegisterHotKey`, talking to a live Jalen
through a one-shot sentinel file. `data/hotkeys.log` shows 150 lines of real
daily use — this works.

### 4.2 Controlling the machine

43 tools, no LLM in the loop. **This is the strongest domain in the system.**

- **Open anything by the name you say.** A Start-Menu index (164 shortcuts on
  this machine), learned nicknames, app families, and typo tolerance. The fuzzy
  cutoff is 0.75 and the reason is documented: at 0.6, "claude"→"code",
  "discord"→"vscode", "opera"→"Computer" and "zoom"→"Zotero" all scored 0.60-0.62.
  At 0.75, "wrod"→"word" still passes at exactly 0.750.
- Filler words are stripped — 11 of 14 ordinary phrasings like "open telegram
  when you get a chance" failed before that was added.
- Window management that **tells one Chrome window from another** — "close the
  YouTube window" closes that one.
- UIA accessibility-tree screen reading, clicking, typing, keyboard shortcuts.
- Media and volume via Windows media keys (works with Spotify Free, unlike the
  Spotify API which went Premium-only in Feb 2026).
- Screenshots, system facts, disk/memory reports with caching (a cold
  `cleanup_suggestions` measured 41–92 s, hence a 600 s cache).
- File read/list/search/create/edit/copy/move/rename/delete, plus content search
  inside files.

31 of the 43 have literal router phrases and answer in ~0.15 ms for zero tokens.

**Defects found:** `click_element`'s name regex is case-sensitive while the
window lookup is not (`.*Start.*` matches, `.*start.*` does not);
`volume_mute_toggle`'s `mute` argument only picks the reply string while the
body always toggles, so "unmute" can mute and then claim it unmuted; and
`search_files` matches a single raw substring, so multi-word queries fail.

### 4.3 The browser

Three mechanisms, and the layering is the interesting part.

**(1) CDP attach.** `webagent.py` launches a plain `chrome.exe` — deliberately
*not* via Playwright's launcher — on a dedicated profile directory, then attaches
over the debugging port. The reason is measured and excellent: Playwright's
launch flags set `navigator.webdriver = true`, and against the real
`accounts.google.com` that produced *"Couldn't sign you in. This browser or app
may not be secure."* After the change, the same sign-in reached the actual
password page in the owner's real account. It also uses a dedicated profile
because Chrome 136+ locks the real one — 150 s timeout vs 0.8 s.

**(2) The Chrome extension.** An unpacked MV3 extension with a pinned ID runs
inside the everyday Chrome, `connectNative`s to a batch shim, which relays frames
to `jarvis.bridge.native_host`, which reads a port and token from
`data/bridge.json` and opens a localhost socket to the running app,
authenticating with a constant-time compare. Commands are an **allowlist of 21 —
there is no arbitrary-JS command** — and only the app may originate a command;
the extension may only respond or raise allowlisted events. Measured
availability over four minutes: up in 20 of 24 samples, self-healing within
~5 s.

**(3) Window level.** `browsertabs.py` enumerates real browser windows via
`EnumWindows` plus owning process image name; `autofill.py` reads Chrome's
address bar via UIA and types into whatever the owner focused.

On top sit: sign-in driving that **stops permanently at the password box and
2FA**; an OTP reader that pulls a fresh code from the owner's own Gmail and
types it **without ever speaking or returning it**; personal-info autofill that
hard-refuses payment fields and fields belonging to anyone else; and a
delegation supervisor that drives ChatGPT/Gemini in a real browser, detects
completion by output stability, and refuses to score the answer itself.

**Status caveat:** the delegation supervisor has never completed a real run —
see §6 and §10.

### 4.4 Mail, calendar and messaging

24 tools. Reads and drafts are GREEN; **every send is RED**.

- **Gmail**: search with Gmail's own query syntax, read one message in full,
  and `scan_inbox` — up to **300 emails in one call** (100/page, hard cap 300),
  sorted into worth-a-look / ordinary / automated, so "is there an opportunity
  in here" gets an answer rather than a recital.
- **Calendar**: read a day, search 90 days forward, create events.
- **Telegram, two separate things:** his *personal account* over MTProto
  (Telethon) — list chats, read, search across all messages, send as him, write
  into a chat's draft box, send files; and a *bot*, which is only a remote-control
  front door onto the same brain.
- Two destinations are **pre-approved** and skip the confirmation: "Saved
  Messages" and his ML channel. Pre-approval is applied *after* the injection
  guard, and a dedicated test asserts that ordering.
- Attachments refuse to send login files. Ceilings are derived, not guessed:
  Telegram 2 GiB, Gmail 18 MiB (from Gmail's 25 MB encoded cap and base64's
  one-third inflation).

**Live state: personal Telegram works right now. Gmail and Calendar do not** —
see §10.

### 4.5 Writing, research and documents

- **Research is real**: DuckDuckGo's keyless HTML endpoint, page text extracted,
  everything fenced as UNTRUSTED CONTENT and tainted.
- **Document reading is genuinely broad**: PDF via pypdf (verified live —
  a real 20,064-character extraction), and `.docx`/`.pptx`/`.xlsx` parsed as the
  OOXML zip-of-XML they are, using only `zipfile` and `ElementTree`.
- **Writing as the owner** is a lookup, not a system prompt: `voice_guide` finds
  and caches a 34,846-byte `my-voice` SKILL.md and prefixes it with a "match it"
  instruction.
- **The community post format** is codified — two templates, a fixed
  three-question Q&A in an expandable blockquote, a fixed two-line sign-off.
- Essays go to `data/drafts/YYYYMMDD-HHMMSS-slug.md` **and** the clipboard.

### 4.6 The technician

Two deliberately separated layers, and the separation is enforced by a test that
greps `technician.py` for write cmdlets.

**Looking only** (`technician.py`): ten read-only PowerShell probes across wifi,
storage, updates, security and startup. Every finding is tagged **observed**
(fact) vs **likely** (hypothesis), and every scan ends with a *"could not
check"* list so a partial scan never reads as a clean bill of health. That is a
genuinely unusual and correct piece of design.

**Changing only** (`repairs.py`): one real AMBER repair (wifi adapter power
management, **verified by reading the setting back**), one RED delete, four
GREEN "open the Settings page" shims.

Live run on this machine, 23.6 s: *"Problem: drive C: is nearly full — 7
gigabytes left of 146."*

**Self-diagnosis** is a separate stack: every unhandled exception plus a
`pythonw`-nulled stderr routes into `data/crash.log`, and `data/last_exit.json`
is a running/stopped state machine — which is what makes *"why did you stop last
time"* answerable.

### 4.7 Delegation and self-control

**Outward** — four routes: Gemini/ChatGPT/Hermes over HTTP; the Claude Code CLI
foregrounded or headless in the background; and the Claude desktop app via the
clipboard. Every route except one passes a **brief-quality gate** that refuses a
thin brief and names the missing section.

The review tools deliberately **compute nothing**: they lay out what was asked
(recorded *before* the work started), what the agent claimed, and what git says
actually changed — then instruct the brain to judge. Refusing to grade its own
homework is the right call.

**Inward** — Jalen operating on Jalen: run his own pytest suite, run his own
diagnostics, report disk and last-shutdown health, open his own source in VS
Code, log what he cannot do and read it back, **ask the user a question and block
up to 180 s** (stopping rather than guessing if no answer comes), report per-turn
latency split into wait-vs-spoke, and mute/sleep/pause/resume/quit by voice.

**Local semantic memory** is fastembed bge-small over sqlite-vec, with a
credential refusal before embedding.

### 4.8 Safety, secrets and audit

Covered in full in §5.

---

## 5. The safety model

This is the best-designed part of the system and deserves its own section.

### One classifier, two enforcement paths

`SafetyEngine.classify()` is a **pure function** returning a verdict plus a
redacted argument dict. It only decides — *asking* is the caller's job. That
lets the same verdict drive two independent enforcement points:

- the Agent SDK `PreToolUse` hook (`jarvis/brain/agent.py:410`), and
- the regex-router path (`jarvis/app.py:834`).

Neither can be bypassed by going through the other.

### The four tiers

| Tier | Behaviour | Live tools |
|---|---|---|
| **GREEN** | runs, logs | 118 |
| **AMBER** | announces, waits `undo_window_s` for "stop", then proceeds | 21 (+1 unclassified) |
| **RED** | stops, waits for a spoken "yes"; silence cancels | 13 |
| **BLACK** | refused, always, not overridable by voice | 0 implemented |

`paranoid_first_week: true` promotes every AMBER to RED.

### BLACK is a deny-list for code that does not exist

All 16 BLACK names — `execute_payment`, `transfer_funds`, `enter_card_number`,
`read_password_manager`, `disable_antivirus`, `solve_captcha`, `buy_crypto`… —
map to **no implementation at all**. That is not rot; it is the sharpest idea in
the repo. BLACK is not a gate on existing tools, it is a **pre-emptive refusal
for tools nobody has written yet**. If someone later implements
`execute_payment`, it is born refused.

`ARCHITECTURE.md` explains the money decision honestly, including where it
overrode the owner: he answered *"money — yes with confirmation each time"*, and
the build split it. Jalen will look up prices, fill a cart, find the flight and
put the confirmation screen in front of him — but anything that types a card
number is BLACK, permanently, because *"a voice assistant that can move your
money is one misheard sentence from a very bad day."*

### Unclassified fails safe

Exactly one live tool, `web_sign_in`, has no tier. `jarvis/safety.py:160-163`
treats any unknown tool as **AMBER, never silently GREEN**, and flags it
`unclassified` in the audit detail. So it fails safe — but nobody made a
decision about it, and it drives real website logins, which is the wrong tool to
leave to a fallback.

### Two invariants that make the rest safe

**1. Nothing Jalen reads can make him act.** A process-wide sticky taint flag is
raised by content fences in `gmail.py`, `messaging.py`, `research.py` and
`webagent.py`, and cleared only by a fresh utterance (or after 600 s). With the
taint up, `classify(origin="content")` returns **BLACK** — a hard refusal, not a
confirmable prompt.

Verified live in this audit: after a single `taint.mark()`, `send_email`,
`send_telegram_message`, `delete_file`, `fill_credential` and `web_sign_in` all
return BLACK, while `draft_email`, `save_telegram_draft` and
`create_calendar_event` stay GREEN.

*The cost, which is not documented anywhere:* **"read my latest email and reply
to it" cannot complete in one utterance.** Neither can "send what you just
found". This is a deliberate trade, but a reader will meet it as a bug because
`HANDOFF.md`'s "known limitations working as intended" table does not list it.

Worth knowing: this guard was **unreachable code for months**. The
`origin="content"` check was correct and well-tested; nothing ever set the flag.
Commit `b940613` — *"The injection guard had never once run"* — is one of the
most valuable commits in the history.

**2. Secrets never become tool results.** The vault (PBKDF2-SHA256, 200k rounds;
counter-mode keystream XOR; HMAC-SHA256 tag; 3,600 s unlock TTL) can list what
it holds *by name*. The values go straight to the code that types them.
`get_secret` is deliberately **not a registered tool**, and a test asserts it
never becomes one.

Per-domain approval is exact-host, with a once / always / never model — and
"once" is deliberately not storable.

### The audit trail

Dual-sink (SQLite + JSONL), redacting secret-shaped arguments and secret-shaped
utterances. The redactor demonstrably works: one ratings record stores
`[mentioned a credential - not included]` in place of a spoken secret.

---

## 6. What the logs say he actually did

This is the section no document in the repo contains, and it is the most
important one.

### First: the evidence itself is contaminated

**Three of the five runtime files are polluted by the test suite.**

| File | Pollution |
|---|---|
| `data/audit.jsonl` | **49% of logged actions are synthetic** — 180 fake `transfer_funds` denials, 196 `delete_file` rows against `C:/tmp/doesnotmatter.txt`, 83 `some_undefined_amber_tool_xyz` cancellations |
| `data/router_misses.log` | **43% of lines are two test strings** — one repeated 2,828 times, another 1,676 times |
| `data/web_chats.json` | **100% test-generated** — all 29–31 entries share one objective, one 806-byte brief, one 6-character answer |

`tests/conftest.py:41-90` fixes this correctly *going forward* for the audit log,
router misses, memory DB and crash log — but it was only added 21–23 August,
the pollution dates from 19–20 August, and it was never purged. And it still
does **not** patch `webagent.CHATS_PATH`, so `web_chats.json` is being polluted
to this day.

**This matters practically:** `scripts/readiness.py` — the one document
explicitly sold as *"probed on this machine just now, not read off the code"* —
reports *"Supervised delegation — 31 conversations run and judged."* All 31 are
one test fixture replayed. **Zero genuine supervised delegations have ever run.**

### After removing the pollution: what really happened

**565 real tool actions across 88 distinct tools, in 13.7 days.**
Outcomes: 537 executed, 27 cancelled, 1 blocked.
Tiers: 474 green, 65 amber, 25 red, 1 black.

The real top twenty:

```
get_time              123   ← 22% of ALL real tool use
open_target            42
close_app              21
search_email           20
play_on_youtube        16
play_media             16
read_email             15
search_files           14
cleanup_suggestions    14
send_telegram_message  13
voice_guide            12
open_url               11
web_search             11
ask_user               11
community_post_guide   10
web_delegate            9
open_app                8
web_read                8
log_weakness            8
```

The remaining 55 tools fired between one and seven times each.

**131 of the 208 declared tools (63%) have never been invoked once.** Entire
subsystems have zero runtime evidence: the whole vault, all browser
form-filling, all git/dev handoff, all calendar writes, every volume and media
transport verb, `morning_brief`, `self_diagnose`. Some of those absences are
correct (the BLACK honeypots *should* never fire); the rest are simply unproven.

### The safety gate genuinely works in production

- **27 real cancellations** at the confirmation prompt — `close_app` ×11,
  `delete_file` ×4 (all temp paths), `open_target` ×5, `send_email` ×2 to a
  named academic's address, `empty_recycle_bin`, `click_element`.
- **1 real BLACK block** — a `send_telegram_message` on 26 August.
- **89 utterances correctly ignored** as "not addressed to Jalen" — the address
  gate works.

### The self-ratings

Nine ratings, 21 August to 1 September: **10, 6, 3, 1, 7, 6, 7, 1, 2.** Mean
4.8 — and the first record is itself wrong (a spoken "5" was stored as 10 by a
parser bug since fixed), so the true mean is **4.2**. Four of nine are ≤ 3.

`web_delegate` is blamed in four of the nine. The two most recent and lowest
(1 and 2) blame malformed Telegram posts.

Recurring complaints, in the owner's words from the miss log:
*"why you're so slow"*, *"you're taking too much time to respond to me"*,
*"why you are silent and not speaking to me"*, and on the wake word,
*"you're kind of deaf when I'm saying Jalen 100 times."*

### The router misses are a roadmap

121 singleton phrases are real speech and cluster into coherent unserved
intents — this is the single best "what to build next" signal in the repo:

- **machine health** — "whats eating my disk", "is my disk failing", "check my
  battery health", "is my antivirus on", "is windows up to date"
- **email/calendar** — "read 100 emails", "scan my inbox for opportunities",
  "put a meeting at two tomorrow", "what have i got tomorrow"
- **vault** — "unlock the vault", "lock the vault", "what is in the vault"
- **browser/forms** — "whats on this page", "fill this form in", "use my own chrome"
- **orb** — "make the orb bigger", "move yourself to the top left"

Note how many of these *already have working tools behind them*. They are
router-coverage gaps, not capability gaps — cheap to fix.

Also in the miss log: 171 instances of `jaluddin`, plus "jarry stout", "crome",
"gogle chrome" — mangled wake attempts that never woke him.

### Scale check

Across 13.7 days the whole system logged **317 timed turns, 835 user utterances,
565 real tool actions, 1 genuine written deliverable and 9 ratings.**

That is a prototype's worth of real use. Every capability claim in this report
should be read against that denominator.

---

## 7. Where the time goes

Measured from 317 logged timing lines:

| Stage | median | p90 | max |
|---|---|---|---|
| **heard** (speech end → words exist) | 1,848 ms | 3,538 ms | 36,041 ms |
| **thought** (words → first sound) | 1,558 ms | 7,565 ms | 30,433 ms |
| **spoke** (talking) | 14,469 ms | 55,319 ms | 393,445 ms |

Two conclusions the project has not drawn:

**1. The router does not make things faster.** Over 214 real turns, router-path
wait was p50 **4,354 ms** and brain-path wait p50 **4,504 ms** — a 150 ms
difference. Speech recognition is ~43% of a median turn and *both paths pay it
identically*. The router's job is protecting a finite Claude Pro quota, and at
that it succeeds. It is not a latency optimisation, and the README implies it is.

**2. The real problem is `spoke`, not `thought`.** A median of 14.5 seconds of
Jalen talking, p90 of 55 seconds, max of six and a half minutes. Every one of
the owner's "why are you so slow" complaints is at least as likely to be about
answer *length* as about latency. `jarvis/timing.py` was written specifically to
separate these two — and the separation says the length problem is the larger
one.

`PRODUCT_REVIEW.md` already identified streaming local STT as the single biggest
felt improvement (1.9 s → ~0.4 s on *every* turn). That analysis holds. But
capping spoken length, or summarising before speaking, is cheaper and would move
a bigger number.

---

## 8. Advertised but defective

35 confirmed defects. The ones that matter most, grouped.

### It reports success and does nothing (the worst class)

| What | Reality |
|---|---|
| **`reload_config`** | Says *"Config reloaded."* and reloads nothing. Advertised in three places (`README.md:118`, `config/jarvis.yaml:4`, `scripts/capabilities.py`) as the way to apply an edit without restarting. `SafetyEngine` caches tiers at construction, so a safety edit needs a full restart. |
| **`jalen_restart` (voice)** | Says *"Restarting."*, sets `_restart_requested = True` — which is **read nowhere** — and quits. Saying "restart" leaves the machine with no assistant running. `.\jalen.ps1 restart` and `run.py --restart` do work; only the voice path lies. |
| **`hand_off_to_cowork`** | Puts the brief on the clipboard correctly, launches the Claude desktop app, then calls `keyboard_shortcut("ctrl+v")` — but `uiautomation`'s SendKeys needs `{Ctrl}v`. It types the six literal characters `c,t,r,l,+,v`. Returns True and reports *"Claude's open with the brief pasted in."* |
| **`.\jalen.ps1 hands`** | Documented in the launcher header, printed as valid in its own help — and absent from the switch. Running it prints *"Unknown command: hands"* and then lists `hands` as valid. |
| **Orb caption row** | 20 px of window height reserved for a transcript strip that nothing ever feeds. `set_transcript()` is called only from tests. |
| **`readiness.py` mic probe** | Reports *"Microphone AVAILABLE — captured real audio."* It enumerates devices. No stream is opened; no audio is captured. |
| **`readiness.py` Claude-desktop probe** | Cannot ever report missing — it tests a hardcoded non-empty string constant. The "not found" branch is unreachable. |
| **`scripts/update.py --check`** | This repo has no remote. `git rev-list` fails, the empty result is falsy, and it prints *"already up to date."* |

### Correctness bugs

- **Pre-approved destinations match by substring, bidirectionally.**
  `safety.py:258` does `allowed in target or target in allowed`, so `to='m'`,
  `to='ai'` and `to='machine'` all classify **GREEN**. A real Telegram chat named
  "AI" would be messaged with no confirmation. (Empty string is guarded; short
  substrings are not.)
- **`diagnose(area="updates")` never works by voice.** The router does
  `rstrip("s")`, producing `"update"`, which is not a key. Live output: *"I don't
  have a check for 'update'."* The other four areas are fine.
- **`temp_file_report` times out 100% of the time on this machine.** Budget is
  45 s; the walk needs 88 s over 8,130 files. The docstring claims the .NET
  enumerator makes it ~2 s — contradicted by measurement.
- **The brief-quality gate fails in both directions.** It is substring matching,
  not judgement: 60 words of `"context success do not"` repeated *passes*; a
  genuinely good 70-word brief with real constraints and a real success
  criterion is *refused*.
- **`worth_asking_about` never fires for the flagship delegation.** Its
  real-work set contains `hand_off_to_claude_code`, which is not a tool; the
  actual names (`hand_off_to_code`, `ask_claude_code`) are absent.
- **`web_delegate` rubber-stamps stubs.** `verified_complete` is copied verbatim
  from the adapter's `finished` flag with no check that the answer is
  non-trivial — which is why 29 six-character answers are all stored as
  completed.
- **`never mind` doesn't cancel cleanly.** `app.py:1167` reads
  `if intent.tool != "cancel": return` — so the one intent meaning *"stop, I
  withdraw that"* is the one that falls through.
- **"search the web for X" opens a tab instead of reading.** The router
  intercepts the two most natural research phrasings into `open_url`, before the
  brain — contradicting the system prompt's own rule against describing a page
  it has not read.

### Never fires in production

- **Habit learning.** `LEARN_AFTER = 3`; all 23 stored habits sit at count 1.
  The fast path has **provably executed zero times**. Root cause is visible in
  the keys: habits are keyed on the raw utterance, so *"could you please sign me
  in the chat gpt"* and *"hey jalen could you please sign me into chatgpt…"* are
  two separate count-1 entries for the same intent and same tool. **Fix: key on
  the resolved tool+args signature, not the utterance.**
- **Pronoun resolution.** `conversation.propose()` and `remember_subject()` are
  never called, so "it"/"yes" resolution never fires despite being built and
  tested.
- **Model tiering.** `pick_model()` exists and reads all three model keys — and
  has zero callers. `model_fast`, `model_deep` and `escalate_on_keywords` are
  decorative; every turn uses `model_default`.
- **`router.fuzzy_threshold: 86`.** Assigned in `__init__`, never read. Router
  matching is exact regex only; the only fuzziness is downstream in the launcher.
- **The 21:00 daily review and 07:30 brief.** `daily_digest()` exists, but
  **there is no scheduler anywhere in the package** — no timer, no thread, no
  cron. Both only happen if you ask out loud.
- **`weaknesses.md` never increments.** All entries read "Seen: 1 time" despite
  two of them describing the identical failure an hour apart — so the
  prioritisation signal the file exists to provide does not exist.

### Dead weight

`mediapipe 1.0.1`, `opencv` and `models/hand_landmarker.task` (7.8 MB) are still
installed — ~250 MB of dependency for the deleted hand-gesture feature. The test
that "proves" the removal only checks packaging metadata and cannot see what is
actually installed, so it passes while the weight remains.

Also: 43 of 136 config keys are never referenced by any code; `GITHUB_TOKEN` and
`NOTION_TOKEN` are both **set in `.env`** and read by nothing (two live
credentials widening the blast radius for zero benefit); and `ANTHROPIC_API_KEY`
is requested in `.env.example` but not needed — the brain runs on the Claude Code
subscription.

---

## 9. Security findings

### 9.1 Confirmed: injected content can reach a pre-approved send

**Severity: high. This is a real, reproducible chain, not a theoretical one.**

The taint mechanism is correct, and the fences in `read_email` / `read_telegram`
/ `web_read` work. But **the bulk and list read tools do not fence or taint their
output**:

`search_email`, `unread_email_summary`, `unread_email_headline`, `scan_inbox`
and `list_telegram_chats` return attacker-controlled sender names, subject lines,
snippets and chat titles **without the untrusted fence and without calling
`taint.mark()`**.

The chain:

1. An attacker puts instruction text in an email **subject line**.
2. The owner says *"scan my inbox"* → the subject reaches the model untainted.
3. Because nothing tainted the turn, `origin` stays `"user"`.
4. Because "Saved Messages" and the ML channel are **pre-approved**,
   `send_telegram_message` to them classifies **GREEN**.
5. It sends with **no confirmation at all**.

The auditing agent reproduced this. Note that step 4 is made worse by the
substring bug in §8 — the pre-approval check is far looser than intended.

**Fix:** apply the same `_fence()` + `taint.mark()` treatment used in
`gmail.py:150` to the five bulk/list read paths. It is the same one-line pattern
already used correctly elsewhere in the same file.

### 9.2 The wake-word accuracy figures do not describe the running system

`README.md` presents *"0.00% false accepts on ordinary speech and room noise at
every threshold"* under a heading that reads as a measurement report. The
underlying table was reproduced exactly in this audit, so it is not fabricated.
But three things make it misleading:

1. **The evaluation protocol is not the runtime protocol.** The evaluator scores
   **one fixed 2.0 s window per clip**; the runtime **slides a ~1.96 s window
   every 80 ms and takes the max**. Measured on the same 60 ordinary-speech
   clips: 0/60 single-window (matching the published table) vs **11/60 (18.3%)
   crossing 0.5** under the streaming protocol.
2. **The shipped threshold is 0.5, which is below every row of the published
   table** (lowest row: 0.70). No false-accept number exists for what actually
   ships.
3. **Every one of the 292 ordinary-speech negatives is synthetic** — edge-tts
   with injected noise, not one real recording of the owner's room. Zero of 292
   gives a 95% upper bound near 1.0%, not 0.00%.

`config/jarvis.yaml:142-144` *does* admit the parallel caveat for positives. The
README drops it.

**This is also why the wake word feels deaf.** The threshold was lowered to 0.5
precisely because the model has never heard the owner's voice — buying recall by
spending precision. `scripts/record_wake_samples.py` is complete, validated and
tested, and has **never been run**: zero `real_*.wav` files exist. Fifteen
minutes of recording fixes this properly.

### 9.3 Undeclared MCP tools ran without a declared tier

The audit log shows `COMPOSIO_REMOTE_BASH_TOOL` (arbitrary remote shell) and
`COMPOSIO_REMOTE_WORKBENCH` invoked twice each, plus several
`chrome-devtools-mcp` tools — **none declared in `config/safety.yaml`**, while
their local equivalents (`run_powershell`, `run_terminal_command`) are. A default
was applied at runtime, so they were not ungated; but the decision is not
reviewable in the file that exists to make it reviewable.

### 9.4 Good news, verified

- No secret was reproducible from any log this audit read.
- The redactor works — a spoken credential is stored as
  `[mentioned a credential - not included]`.
- `get_secret` is not a tool, and the test enforcing that passes.
- The vault, Google token and Telegram session are all on the `never_touch` list.
- The injection guard, where it *is* wired, hard-refuses rather than prompting.

---

## 10. Operational state, right now

| Thing | State |
|---|---|
| **Personal Telegram** | ✅ Working — returned real chat names and unread counts during this audit |
| **Gmail / Calendar** | ❌ **Dead.** `invalid_grant: Token has been expired or revoked`. Every Gmail and Calendar tool raises `GoogleNotConnected` |
| **Gemini API** | ❌ `PERMISSION_DENIED` on one probe, `503` on another. Key is set, non-functional |
| **ChatGPT API** | ❌ No key. `OPENAI_API_KEY` is read by the code but **absent from `.env.example`**, so nothing ever told the owner to add it |
| **Hermes / OpenRouter** | ❌ Same — read by code, missing from the template |
| **Browser fallback to ChatGPT/Gemini** | ❌ Not signed in inside Jalen's Chrome profile |
| **Claude brain** | ✅ Runs on the Claude Code subscription, no API key needed |
| **Wake word** | ⚠️ Works, but never trained on a real voice; threshold lowered to compensate |
| **`Everything` file search** | ⚠️ Configured but `es.exe` is not installed — silently degrades to walking three folders |
| **Test suite** | ✅ 3,182 passed, 0 failed, 10m 11s |
| **Last shutdown** | ✅ Clean — stop-file, 22.5 min uptime, 1 Sept |

**So every non-Claude model is currently unreachable by both routes.** The
sentence in `ABILITIES.md` promising *"the browser route works instead"* is false
as of today.

The Google fix is a **Google Cloud Console change, not code**: the OAuth consent
screen is in Testing mode, which expires the refresh token every 7 days. Adding
yourself as a test user does not fix it — that *is* Testing mode. It must be
**published to production** (no verification submission needed).
`ARCHITECTURE.md` already warns about this; it just hasn't been done.

### The single highest-leverage bug

`OSError('crash-log stderr has no file descriptor')` appears **7 times** in
`crash.log` and is named in `weaknesses.md` as the cause of *every*
`web_delegate` failure. The crash logger's own stderr redirection breaks
Playwright's subprocess launch.

Fixing that one interaction plausibly unblocks the entire browser-delegation
feature set — which is simultaneously the most-complained-about capability in
the ratings and the one with the most code behind it.

---

## 11. How he got here

108 commits, **19–24 August 2026** — six days — then two weeks of real use
through 1 September. Test growth by day: 37 → 134 → 627 → 1,637 → 2,891 → 3,182.

| Phase | Date | What happened |
|---|---|---|
| **Bootstrap** | 19 Aug | Imported from a zip. 37 safety tests. Phases 0–1 only. Toolchain verified, event-loop bug fixed, Telegram + local semantic memory landed |
| **Product overhaul** | 20 Aug | Driven by *"slow, basic, can't stop it"*. Cooperative stop, 92-second waits killed, compound commands, fuzzy open-anything, disk/memory reports |
| **The rename + 4-phase spec** | 21 Aug | Nine owner requests decomposed into Identity / Comms / Technician / Agency. Became **Jalen**. Wake word trained from scratch. Vault, per-site permission, technician, Gmail/Calendar/Telegram |
| **Productisation** | 22 Aug | `HANDOFF.md`, the capability ledger, bulk mail, essay drafting |
| **Real-use repair** | 23 Aug | Six-bugs-from-his-own-log commits. Speaker ID built, measured, **deleted**. Orb rebuilt after 2,886 tests failed to notice it was broken |
| **Browser reality** | 24 Aug | Ghost-mode Chrome killed, CDP attach, the Chrome extension shipped, **the injection guard found never to have run**, OTP-from-email, profile autofill |

### The project's own recurring failure mode

It names this about itself, and it is the throughline: **"output that sounds
right and is not."** Almost every bug in the history was found by reading
`data/audit.jsonl` after real use, not by a test. The two most telling commits:

- *"I broke the orb completely and 2886 tests said it was fine"*
- *"The injection guard had never once run"*

Both are the same lesson: **a green test suite is not evidence that a feature
works.** This report is, in a sense, that lesson applied at scale — which is why
§6 exists.

### Things deliberately deleted

Worth as much as things built:

- **Hand-gesture orb resize** — opened a webcam and cost ~250 MB for a feature
  that never worked for the person who asked for it.
- **Speaker identification** — measured, then deleted: on 45 real voices, the
  threshold keeping 98.6% of the owner also accepted **78.4% of strangers**. The
  address gate does the same job with words, and words are not fooled by a loud
  room.

Both deletions were correct. Both left their documentation behind — `HANDOFF.md`
still lists hand gestures as *"WORKING, AWAITING HIS TEST"* in four places.

---

## 12. Genuinely not done

Straight from the project's own `whats_left.py`, `readiness.py` and `HANDOFF.md`,
verified:

1. **Zero of six end-to-end flows have ever been driven by a person.** Real
   email, real chat, real login — all unit-tested, all mocked. The project's own
   assessment: *"most likely place a real bug is still hiding."*
2. **The wake model has never heard the owner's voice.** 2,160 synthetic
   positives, 0 real recordings.
3. **The vault has never been created.** Needs a passphrase only he can invent.
4. **Local semantic memory has never stored a row.** Full schema, zero
   `memories`. There is no router rule for remember/recall.
5. **Nobody has ever installed this from nothing on another machine.** ~150 tests
   silently skip without Chrome, git or a mic — on a clean machine the suite
   still reports green while testing less.
6. **No spend tracking.** *"What did this month cost"* has no in-product answer.
7. **Why Jalen exited on its own** — 4 recorded silent deaths (one after 19.9 h),
   still not root-caused.
8. **Telegram HTML parse mode.** `send_telegram_message` sets no parse mode, so
   the whole house post format arrives as literal `<b>` and
   `<blockquote expandable>` tags. **This is the direct cause of the two lowest
   ratings (1 and 2), and it is open as of 1 September.**

---

## 13. What I would do next

Ordered by value per hour, using only evidence from this report.

**Today, under an hour each:**

1. **Fix the Telegram parse mode.** One argument. It is the direct cause of the
   two most recent and lowest ratings.
2. **Publish the Google OAuth consent screen.** Console change, no code.
   Restores Gmail and Calendar, which are 35 of the 565 real actions.
3. **Fence the five bulk-read tools** (§9.1). Same one-line pattern already used
   correctly ten lines away. Closes the one confirmed security hole.
4. **Fix the pre-approval substring match** — require exact equality after
   normalisation.
5. **Fix `keyboard_shortcut("ctrl+v")` → `"{Ctrl}v"`.** One string. Makes the
   cowork handoff actually work.

**This week:**

6. **Fix the `crash-log stderr` / Playwright interaction.** Highest leverage
   single bug in the system — plausibly unblocks all browser delegation.
7. **Record 15 minutes of wake samples** and put the threshold back to 0.7. The
   script is written and tested; it has just never been run. Fixes the
   most-complained-about behaviour after slowness.
8. **Re-key habits on tool+args instead of the raw utterance.** The mechanism is
   built and correct; only the key is wrong. Turns a provably-dead feature on.
9. **Add router rules for the 121 real misses** — most already have working
   tools behind them. Cheapest capability gain available.
10. **Purge the test pollution** from `audit.jsonl`, `router_misses.log` and
    `web_chats.json`, and patch `webagent.CHATS_PATH` in `conftest.py`. Until
    then, every self-report the system makes about itself is wrong.

**Then:**

11. **Cap or summarise spoken answers.** `spoke` p50 is 14.5 s and p90 is 55 s —
    a bigger number than anything in `thought`.
12. **Streaming local STT** (`PRODUCT_REVIEW.md`'s recommendation, still right):
    ~1.6 s off *every* turn.
13. **Regenerate the docs from the code** and delete the hand-coded numbers.
    `scripts/abilities.py` already does this — it just hasn't been re-run.
14. **Drive all six end-to-end flows with a person present.** Nothing else
    substitutes for it.

**Explicitly do not build:** a phone app, a fifth AI backend, or a settings UI —
`PRODUCT_REVIEW.md`'s reasoning on all three is sound and this audit found
nothing to contradict it.

---

## Appendix A — capability inventory by subsystem

| Subsystem | Catalogued | works | partial | broken | other |
|---|---|---|---|---|---|
| Brain and fast router | 77 | 64 | 6 | 3 | 4 |
| Delegation and self-control | 64 | 47 | 13 | 2 | 2 |
| Project history and docs | 61 | 47 | 14 | 0 | 0 |
| On-screen presence and desktop UX | 60 | 43 | 8 | 8 | 1 |
| Driving a real web browser | 60 | 50 | 10 | 0 | 0 |
| Installation, config and operations | 60 | 40 | 11 | 4 | 5 |
| Fixing the computer | 55 | 30 | 15 | 5 | 5 |
| Voice pipeline | 53 | 41 | 7 | 2 | 3 |
| Safety, audit and secrets | 50 | 37 | 7 | 2 | 4 |
| Controlling the Windows machine | 50 | 38 | 6 | 4 | 2 |
| Mail, calendar and messaging | 50 | 29 | 8 | 2 | 11 |
| Writing, research and documents | 33 | 26 | 4 | 3 | 0 |
| **Total** | **673** | **492** | **109** | **35** | **37** |

Coverage check: 151 of 153 registered tools appear in the inventory. The two
missing are `site_permission` and `remember_site_decision` (both real, both in
`jarvis/tools/vault.py:508-518`).

---

## Appendix B — verification log

Commands run during this audit, with their results.

```
# Tool count
python -c "import jarvis.tools as t, jarvis.brain.tools as bt;
           print(len(t.REGISTRY), len(bt.TOOL_SPECS),
                 set(t.REGISTRY)==set(bt.TOOL_SPECS))"
→ 153 153 True

# Test suite (full, no fail-fast)
python -m pytest -q --no-header -p no:cacheprovider
→ 3182 passed, 65 warnings in 611.06s (0:10:11)

# Offline subset (8 browser/voice files ignored)
→ 3036 passed in 129.56s

# Safety tiers vs live registry
python -c "import yaml, jarvis.tools as t;
           d=yaml.safe_load(open('config/safety.yaml',encoding='utf-8'));
           R=set(t.REGISTRY)
           for k in ('green','amber','red','black'):
               print(k, len(d[k]['tools']), len(set(d[k]['tools']) & R))"
→ green 142 118 | amber 23 21 | red 27 13 | black 16 0
→ unclassified: {'web_sign_in'}

# Router hit rate from logged route= values
→ 65 router / 227 brain / 25 unknown  =  20.5% router

# Router hit rate by replaying all real utterances
→ 227 hits / 608 misses over 835 utterances  =  27.2%
→ mean route() latency 0.300 ms

# Wake held-out split, reproduced from first principles
python -c "import numpy as np;
           b=np.load('data/wake_training/features_13891_2_0.npz');
           print(len(b['x']), len(b['x'])-int(len(b['x'])*0.85))"
→ 13891 2084

# Real wake recordings
ls data/wake_training/positive/real_*.wav | wc -l   → 0

# Audit log
→ 4740 lines, 0 malformed, 2026-08-19 → 2026-09-01 (13.7 days)
→ 915 sessions; user 835 / jarvis 778
→ 565 real actions after removing test pollution
→ data/audit.db holds 4644 rows — 96 fewer than the JSONL (sinks out of sync)

# Taint enforcement, live
taint.mark(...); classify(tool, origin='content')
→ BLACK: send_email, send_telegram_message, delete_file,
         fill_credential, web_sign_in
→ GREEN: draft_email, save_telegram_draft, create_calendar_event

# Google connection
jarvis.tools.gmail.google_status()
→ invalid_grant: Token has been expired or revoked

# Dead config keys
→ 43 of 136 leaf keys in config/jarvis.yaml referenced by no code
```

---

*Audited by sixteen parallel agents against the code, the git history, the test
suite and 13.7 days of runtime logs. Every number in this report was measured,
not quoted. Where a project document disagrees, the measurement is reported and
the document is named.*
