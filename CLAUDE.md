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
`test_failure_paths_keep_jarvis_alive`. The trap is that the fallback *working*
is what trips the assertion — `transcribe()` silently falls back to moonshine
and the test asserts `engine_used == 'groq'`. Re-run two or three times in
isolation before touching `stt.py`.

There is no linter, no type checker, no CI and no git remote.

## Invariants — these fail silently when broken

- **Injection-guard ordering.** In `SafetyEngine.classify` (jarvis/safety.py:134)
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
  fixed on 2026-09-30.) A question that is not about origin at all — "is
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
- **The address gate is also the echo defence**, because Jalen's own
  sentences do not begin with his name. Any exemption you add has to call
  `_sounds_like_its_own_voice()` or it is a self-triggering loop — the
  microphone cannot tell his voice from the speakers, and there is no
  acoustic echo cancellation on this machine.
- **`timing.wait_s` is not the wait.** It starts *after* the endpointer's
  1400ms of silence and it stops on the "Give me a second." filler, which
  fires on 65% of brain turns. `felt_wait_s` is the number a stopwatch in
  the room would give. The four original labels in the audit line
  (heard/thought/wait/spoke) are load-bearing for a month of history —
  add, never re-anchor.
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
  programmatically once at `jarvis/brain/tools.py:1064`.
- **Errors are return values, not exceptions.** They get spoken out loud, so a
  traceback is a bug and an error message is a sentence.
- **Adding a tool is three edits**: the module's `REGISTRY`, `TOOL_SPECS` in
  `jarvis/brain/tools.py`, and a tier in `config/safety.yaml`. The first two
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
| Make it faster | `vad.fast_silence_ms`/`silence_ms`, `stt.groq_timeout_s`, the TTS cache — tune together |
| Understand why a design is what it is | `config/jarvis.yaml` comments first (683 lines, they *are* the design record), then `report.md` |
| See the real current state | `report.md` — supersedes every committed doc's numbers, and is currently untracked |

`config/jarvis.yaml` comments are the design record. `PROCESS.md` is a runtime
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
