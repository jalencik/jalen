# Working in Jalen

A voice assistant that drives one Windows laptop. Python 3.12+, single process,
~350 MB resident. The constraint that decided the architecture: 8 GB of RAM with
~1 GB free, so no local LLM and no PyTorch — heavy work happens on someone
else's computer.

## Run it

Always via the project venv — bare `python` is a different install with none of
the packages. `jalen.ps1` resolves the interpreter from its own location.

```powershell
.\jalen.ps1 start      # voice, unmuted
.\jalen.ps1 text       # typed, same brain and same safety rules
.\jalen.ps1 check      # environment + credential diagnostic, run this first
.\jalen.ps1 todo       # what is actually outstanding
python run.py --why    # why the last run stopped
python run.py --standby  # loaded, mic CLOSED until Ctrl+Alt+J (what the kill key leaves behind)
```

## Tests

```powershell
.venv\Scripts\python.exe -m pytest tests\ -q --ignore=tests\benchmark_latency.py --ignore=tests\benchmark_open.py --ignore=tests\benchmark_phrasing.py
```

3,802 tests collect under that gate; 4,027 if the benchmarks are included,
because `pytest.ini` sets `python_files = test_*.py *_test.py benchmark_*.py`.

One test fails on this machine for a reason outside the code:
`test_overhaul_fixes::test_real_typos_and_abbreviations_still_resolve[capcut]`
expects CapCut to be installed, and `%LOCALAPPDATA%\CapCut\Apps` is now
empty. Every other failure is real.

Always `python -m pytest`, never the bare `pytest.exe` — 80 of 109 test files
have no `sys.path.insert` and depend on `-m` putting the CWD on the path.
`pytest-timeout` is not installed, so `--timeout=` kills the run before
collection.

Three voice tests hit live Groq and edge-tts and fail intermittently:
`test_stt_roundtrips_through_groq`, `test_barge_in_latency_is_measured`,
`test_failure_paths_keep_jalen_alive`. The trap is that the fallback *working*
is what trips the assertion — `transcribe()` silently falls back to moonshine
and the test asserts `engine_used == 'groq'`. Re-run two or three times in
isolation before touching `stt.py`.

There is no linter, no type checker, no CI and no git remote.

## Invariants — these fail silently when broken

- **Injection-guard ordering.** In `SafetyEngine.classify` (jalen/safety.py:134)
  the `origin=='content'` check (:173) must stay *above* the pre-approved
  destination downgrade (:200). Swap them and a pre-approved Telegram channel
  becomes an open relay for anyone who gets text in front of Jalen — and every
  behavioural test still passes. `tests/test_adversarial.py:94` compares source
  string indices for exactly this reason.
- **Secrets never become tool results.** `vault.get_secret()` is absent from
  every `REGISTRY` and from `TOOL_SPECS`, and that *absence* is the mechanism —
  a tool result reaches the model, the transcript, the audit log and possibly
  the speakers. Do not add a flag or a tier for it.
- **Four `ClaudeAgentOptions` fields are safety decisions**, not preferences:
  `tools=[]`, `setting_sources=[]`, `skills=[]`,
  `permission_mode="bypassPermissions"`. Left at their defaults the model gets
  the SDK's own Bash/Write/Edit and this machine's MCP servers — none of which
  `safety.yaml` has ever heard of, so they classify as unclassified-AMBER.
  `tests/test_agent_sdk_configuration.py` pins all four.
- **`suppress_cli_console_window()` must run before `ClaudeSDKClient` is
  constructed** (agent.py:487 before :489). It patches `anyio.open_process` by
  module attribute.
- **Never hardcode `origin='user'`** — always `taint.origin_now()`. Leaving
  `origin=` out is the same mistake: `classify()` defaults it to `"user"`.
  (The two call sites that did this, research.py and attachments.py, were
  fixed on 2026-09-30.) The ONE deliberate exception is a router rule that
  matched what he *said*, word for word: `process()` passes
  `his_own_words=True` to `handle_local()` only when `expand_references()`
  left his sentence untouched, because expansion can put read text into it
  and a recalled habit is not his sentence. The brain's tool hook never gets
  this; widening it lets a page act through the model.
  (`tests/test_his_own_words_are_not_refused_after_a_read.py`.) A question that is not about origin at all — "is
  this file / this site protected?" — goes to `SafetyEngine.protected_path()`
  or `protected_domain()`, never to a second copy of the never-touch list.
- **A GREEN tool that acts goes on `injection_guard.refuse_from_content`**
  in `config/safety.yaml`. The injection guard escalates only RED and AMBER
  on a tainted turn; a GREEN tool that types, clicks, launches, writes a
  file or stores a memory does whatever the page Jalen just read says,
  unless it is on that list. `tests/test_green_actors_refuse_content.py`
  and `tests/test_every_tool_has_a_tier.py` catch typos, not omissions.
- **Two gates decide whether you are heard, and they must agree.** The
  microphone gate (`follow_up_until`, and now `_expectation_open()`, in the
  `not listening` branch of `Jalen.run`) decides whether a sound opens a
  window; the address gate (`should_act_on`, app.py) decides whether the
  words are acted on. For a year the first was a *local variable inside
  run()* that the second could not see, so Jalen opened the microphone for
  your answer, transcribed it, and dropped it for not starting with his
  name — 45 real answers in `data/audit.jsonl`. Anything that widens one
  must widen the other, and `tests/test_answering_a_question.py` pins both.
  The same shape has now bitten three times in this file: a piece of
  conversational state whose lifetime one reader enforces and another does
  not (`_pending_rating` had a 300s expiry in `process()` and none in the
  gate). Grep for it before adding a fourth.
  `opened_by` and `vouched_by` in the audit rows (`ignored`, `acted on without
  his name`, `heard nothing`) record which window opened the microphone
  (`wake`, `follow_up`, `answer`, `barge`) and which question of Jalen's, if
  any, was open when the sound began (`question`, `bare-wake`, or empty).
- **The address gate is also the echo defence**, because Jalen's own
  sentences do not begin with his name. Any exemption you add has to call
  `_sounds_like_its_own_voice()` or it is a self-triggering loop — the
  microphone cannot tell his voice from the speakers, and there is no
  acoustic echo cancellation on this machine. It is judged from when the sound
  BEGAN (`began_at`, which `run()` stamps wherever it opens a window), never
  from when the gate is asked: the gate runs 3.2s after the sound ends at the median,
  and a 3s tail counted from there had shut before a question's own echo
  arrived (718 of 718 taken for the answer to itself).
- **`timing.wait_s` is not the wait.** It starts *after* the endpointer's
  1400ms of silence and it stops on the "Give me a second." filler, which
  fires on 65% of brain turns. `felt_wait_s` is the number a stopwatch in
  the room would give. The four original labels in the audit line
  (heard/thought/wait/spoke) are load-bearing for a month of history —
  add, never re-anchor.
- **Limits are one governor on the one engine, asked on both paths.**
  `jalen/limits.py` counts how MANY (`limits:` in `config/safety.yaml`), where
  the tiers say what KIND. It hangs off `SafetyEngine.governor`, and both the
  router (`Jalen.handle_local`) and the brain's hook (`Brain._make_hook`) call
  `admit()` after `classify()` and before the tier's own question, and
  `record()` only at the moment the action is allowed to run — a cancelled
  action must not use up a limit. Add a third path and it has no limits;
  `tests/test_limits_are_enforced_on_both_paths.py` compares source order.
  **Only he lifts one.** At a cap he is asked (`Jalen.confirm`, so the echo
  defence applies) and his yes lets that one action through; nothing the model
  can call raises a limit or ends a lockdown: there is no tool for either,
  `jalen/tools/usage.py` may only call `limits.current()`, and
  `data/jalen-usage.json` and `data/jalen-lockdown.json` are on the never-touch
  list (otherwise `move_file`, which is GREEN, could rename the lock away).
  The lockdown is a file whose *presence* means locked, so a corrupt one fails
  closed; lifting it needs `his_own_words and from_him` (`Jalen._lockdown_off`).
  Numbers are changed in `safety.yaml` only, never by voice.
- **The brain's PreToolUse hook must outlive the questions it asks.** The SDK
  cuts a hook off at 60 s (`HookMatcher.timeout`) unless told otherwise. When
  that happens the CLI stops the tool call with an error (it fails closed), but
  the question Jalen was asking is cut off mid-wait: an answer after the
  cut-off goes to a call that was already refused, and a yes was logged
  "executed". `Brain._hook_timeout_s()` sizes it for three questions in a row
  at the longest wait, a held AMBER announcement included; change
  `safety.confirm_timeout_s`, `confirm_timeout_typed_s`, `undo_window_s`,
  `undo_window_typed_s` or `vad.silence_ms` and it follows.
  `tests/test_confirmation_windows.py` pins it.
- **Past a limit, one question, not two.** The governor's question names the
  action and ends "Confirm?", so `Admission.approved_action` makes the RED
  "Confirm?" (and the AMBER announcement) stand down for that action. Do not
  let a limit question that does NOT name the action set it. It never stands
  in for the consequence question below.
- **A dangerous RED action is asked twice.** A tool under `consequences:` in
  `config/safety.yaml` carries `Verdict.second_question`: after his first yes
  he hears what it does that cannot be taken back and is asked again, on BOTH
  paths, and only a second yes runs it. A consequence must contain no plain
  answer word (`_PLAIN_ANSWER_WORDS`): its echo would answer the question, or
  his real "no" would be thrown away as its echo.
  `tests/test_dangerous_actions_ask_twice.py`.
- **A question that reads out a message can be answered by its own echo.**
  The send questions say the words (`_voice_message_summary`,
  `_text_message_summary` in safety.py), so "sure, see you at six" puts an
  answer word in the question. `_echoes_the_confirmation` treats a fragment
  holding one of the MESSAGE's answer words, half or more of it the message's
  own words (`_quoted_words`), as the echo, and reads "okay" and "ok" as one
  word (`_ANSWER_FAMILY`). A new summary that quotes text must keep the
  `saying "..."` / `starts "..."` / `saying: ...` shapes `_quoted_words` reads.
  `tests/test_a_message_is_heard_before_it_is_sent.py`.
- **The yes/no rules decide first, and nothing overrules them.**
  `_parse_yes_no` reads negatives first; a self-correction ("no no, I mean
  yes") counts only when what he finished on is nothing but an answer
  (`_his_last_word`). Only a sentence the rules left unclear goes to the model
  that reads rambling answers (`jalen/answer_reader.py`, Groq), so a model can
  never turn a refusal into approval - keep it after the rules, never before,
  and never for a question or a correction.
  `tests/test_he_answers_like_a_person.py`.
- **One answer path, and the echo tests are for the microphone only.** Every
  front door hands a sentence to `Jalen._take_as_answer(text, heard=...)`
  before treating it as an instruction: `process()` (voice, text, Telegram) and
  the extension's chat panel (`_answer_panel`, which used to go straight to the
  brain, so a typed "yes" to an open "Confirm?" started a second turn instead).
  `heard` is True only for microphone speech (`_hears_its_own_speakers()`, voice
  mode). The echo tests throw away what sounds like the question, so applied to
  typing they dropped a typed "Confirm" in silence; never pass `heard=True` for
  typed text. `tests/test_typed_answers_are_never_echo.py` pins both sides.
- **The AMBER "say stop" window takes answers, not just the word.** While it
  is open the address gate admits speech without his name; `process()` turns a
  refusal ("no", "don't", "wait") into stop and agreement into "go now", and
  ignores only an exact tail of the announcement (`_echoes_the_announcement`) -
  narrower than the confirm echo test on purpose, because here a missed echo
  can only stop an action.
- **By voice a question's window is held for a sentence on its way.** The
  AMBER 2 s end before any spoken word can arrive (3.2 s after the sound ends,
  at the median), and a "yes" begun in the last seconds of a RED "Confirm?"
  arrived after its 20 s; so `_wait_for_stop` and `_wait_for_answer` wait past
  them while a sentence that BEGAN before they ended has not been judged, for
  at most `ANSWER_HOLD_S` from its onset (`_held_for`). An unclear answer to
  "Confirm?" gets the question again in full and the whole window again
  (`_ask_again`) - not "was that a yes or a no?", whose own echo is " no ".
  `run()` publishes the sentence it is hearing or
  transcribing (`_hearing_since`; every drop path is cleared by the next idle
  frame) and adds it to `_judging` *before* starting its turn; `process()`
  lets go (`_judged`) right after `_take_as_answer`, after the answer is on
  its queue. A new way to hand a sentence on that skips `_judging` closes the
  window on a "stop" in flight, and every test of the 2 s still passes.
  `tests/test_saying_stop_stops_it.py`, `tests/test_a_late_yes_is_still_a_yes.py`.
- **While he speaks, only the wake word interrupts him** (`conversation.barge_in:
  wake_word`, `Jalen._barge_mode`). That branch sets `wake_initiated = True`
  because the wake model heard the name; the any-voice branch below it
  (`barge_in: true`) must never, because it fires on sound, his own echo
  included (`tests/test_reported_failures.py`).
  `tests/test_only_his_name_interrupts_him.py`.
- **"He speaks" is `Speaker.on_air`, never `speaker.speaking`.** A brain
  reply's SpeechStream holds `speaking` (and `_say_lock`) for the whole turn,
  through minutes of tools with nothing playing. Read as "his voice is in the
  room", that made Jalen deaf to everything but the wake word for the whole
  of a long task: no "yes" to the brain's own "Confirm?", no "what's up", and
  a "Hey Jalen" that stopped a silent reply and lost its answer. `on_air`
  keeps a gap between two sentences on air (`STREAM_GAP_S`) and lets go of a
  stream that is waiting, at once after a line said between its sentences.
  App code asks `_on_air(self.speaker)`. Two more things follow from the
  stream holding the lock. `Speaker.say()` speaks through the stream holding
  it (`speak_between`), and a line the stream never began is withdrawn so
  it is never said twice. `_active_stream` is the stream that HOLDS the lock,
  set in `SpeechStream._run`, never the newest one opened: a second brain
  turn's stream pointed a "Confirm?" at a stream that could not speak it.
  `tests/test_it_hears_him_while_a_long_task_works.py`.
- **A tap is an answer, never an instruction.** In `--telegram` mode a question
  goes out with Yes/No (a "Confirm?") or Stop (an AMBER announcement) under it
  (`Jalen._put_the_question`, `telegram_bot.prompt_keyboard`). A tap reaches
  `Jalen.answer_from_tap`, not `process()`, and counts only if it is from an
  allowed user, carries the id of the question open *now*, is the kind of answer
  that question takes, and is the first. Do not route taps through `process()`:
  that would make a button press a new instruction (it would clear taint). Where
  "stop" has to be typed or tapped the AMBER window is `undo_window_typed_s`,
  not the 2 s that is only long enough to say it.
- **A reply Jalen composes never quotes a stranger.** The Telegram actions
  (edit, delete, forward, pin, react) name someone else's message - and
  anything forwarded - by its time and number (`messaging._called`); only his
  own words come back. Those replies are not fenced, so quoting the message
  would hand its text to the model unmarked. And `unpin_message(entity, None)`
  unpins EVERY message in the chat, so an unpin always names one.
  `tests/test_any_action_in_telegram.py`.
- **The conversation is resumed by the id Jalen wrote down, never by
  `continue_conversation`.** `Brain` records the SDK session of every good turn
  (`brain.session_path`) and passes it as `resume=` on start;
  `continue_conversation=True` would resume the newest session in the working
  folder - his own Claude Code session on this repo. A resume that fails starts
  fresh rather than costing him the brain, a session with a private-mode turn
  is never recorded, and only a UUID reaches the CLI's command line.
  `tests/test_the_conversation_survives_a_restart.py`.
- **Never pass user text as an argument to a `.cmd`.** `shutil.which
  ("claude")` returns npm's `claude.CMD`, Windows runs it through cmd.exe,
  and cmd.exe truncates at the first newline, flattens em dashes, strips
  accents to the console codepage and expands `%VAR%`. Prefer a real
  executable — `claude-agent-sdk` bundles one. Same family as the next rule.
- **Never write a regex, backslash or non-ASCII text through a shell heredoc.**
  `\b` arrives as a literal 0x08 byte that still compiles, so the guard never
  fires and every surrounding test keeps passing. Use the Edit tool.

## Conventions

- A tool is a plain module-level function taking keyword args and returning a
  spoken-quality `str`. No decorator, no base class — `@tool` is applied
  programmatically once at `jalen/brain/tools.py:1064`.
- **Errors are return values, not exceptions.** They get spoken out loud, so a
  traceback is a bug and an error message is a sentence.
- **Adding a tool is three edits**: the module's `REGISTRY`, `TOOL_SPECS` in
  `jalen/brain/tools.py`, and a tier in `config/safety.yaml`. The first two
  hard-fail at startup when they drift; the third used to fail at nothing and
  silently degrade to unclassified-AMBER, and now fails
  `tests/test_every_tool_has_a_tier.py`. If the tool is GREEN and acts on the
  world, it also goes on `injection_guard.refuse_from_content` (see
  Invariants) — nothing enforces that one.
- **Every tuned constant carries its measurement in a comment.** Changing a
  number without a new measurement breaks the local convention.
- Config is read only through `cfg.get_path("dotted.key", default)` in
  `__init__`, never mid-call. Read the YAML for shipped behaviour — code
  defaults differ materially (`vad.silence_ms` defaults to 700, ships 4000).
- Heavy imports are function-local, never module-level.
- Dead or unwired config keys are labelled in place (`[NOT IMPLEMENTED]`,
  `[NOT READ]`), never deleted.
- Commit subjects are a past-tense sentence naming the *discovery*, not the
  change, with the total test count and delta at the end.
- **Report done and not-done in the same breath.** Silence about a skipped part
  reads as success.

## Where things are

| I want to... | Look at |
|---|---|
| Change what the model is told | `Brain.system_prompt()` — agent.py:119-397 |
| Add a free spoken command | `_rules()` — router.py:403-1150, 136 rules, first match wins |
| Change a tier or the never-touch list | `config/safety.yaml` only; tool docstrings are stale |
| Change how many of something he allows | `limits:` in `config/safety.yaml` — `jalen/limits.py` is the mechanism, the file is the policy |
| Make it faster | `vad.fast_silence_ms`/`silence_ms`, `stt.groq_timeout_s`, the TTS cache — tune together |
| Understand why a design is what it is | `config/jalen.yaml` comments first (683 lines, they *are* the design record), then `report.md` |
| See the real current state | `report.md` — supersedes every committed doc's numbers, and is currently untracked |

`config/jalen.yaml` comments are the design record. `PROCESS.md` is a runtime
narrative, not a working agreement; the contributor rules are at
`HANDOFF.md:79-111` and `:797-816`.

## Claude authentication

Nothing in this repo holds a Claude credential. `ClaudeSDKClient` spawns a
Claude Code CLI child that supplies its own auth. The SDK runs its **bundled**
`.venv/Lib/site-packages/claude_agent_sdk/_bundled/claude.exe`, not the npm
`claude` on PATH — so verify with that binary, because `scripts/check_env.py`
probes the other one:

```powershell
.venv\Lib\site-packages\claude_agent_sdk\_bundled\claude.exe auth status
```

`CLAUDE_CLI_PATH` in `.env` overrides which binary gets spawned
(`chosen_cli_path()` in agent.py). Empty is the supported normal state. A
`.cmd`/`.bat` is refused and logged rather than forwarded, because npm's
Windows install is a `claude.cmd` shim and the SDK cannot spawn a batch
script — so pointing this at what `where claude` returns is a no-op, not a
fix. To genuinely share one installation, install the native binary
(`irm https://claude.ai/install.ps1 | iex`) and name that.

`claude-agent-sdk` is capped at `<0.3.0` on purpose: the wheel bundles the CLI
and resolves it before PATH, so that line decides which Claude Code the brain
runs. Upgrading is a deliberate act — re-run
`tests/test_agent_sdk_configuration.py` and `tests/test_cli_path_override.py`
after.

`CLAUDE_CODE_OAUTH_TOKEN` in `.env` overrides the machine-wide login and is the
way to bind Jalen to its own subscription. Leave `ANTHROPIC_API_KEY` empty —
`load_dotenv` puts `.env` into `os.environ` and the SDK hands that to the CLI
child, so a value there silently switches the brain to per-token billing.
