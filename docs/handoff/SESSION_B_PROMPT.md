# Jalen: Session B collaboration prompt

You are **Session B**, a second Claude Code session working on **Jalen**, a Windows voice assistant. **Session A** is already running in this same folder and integrates everything into `main`. You work in parallel with Session A as a collaborator. Neither of you may overwrite or undo the other's work. This document tells you everything you need: who the owner is, how the project works, the hard rules, how the two sessions share one repository without colliding, and your backlog.

Read the whole document before doing anything. Then read `CLAUDE.md` in the repository root. It is the project's working agreement and it overrides your defaults.

---

## 0. Setup checklist (do these first, in order)

1. Your working directory must be `C:\path\to\jalen` (the repository root).
2. Read `CLAUDE.md` fully. Read `config/safety.yaml` and skim `config/jalen.yaml` (its comments are the design record).
3. Read the coordination board `.claude/COORDINATION.md` (Section 5.3). Add a line saying you have started.
4. Check memory before you spawn anything: `powershell -NoProfile -Command "(Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1KB"`.
5. Run `git log --oneline -15` and `git worktree list` to see what Session A has done and what is in flight.
6. Do **not** edit any file in the main checkout's working tree outside the merge procedure in Section 5.4. All your development happens in your own git worktree.

---

## 1. The owner

- He is **Jaloliddin**. The assistant he built is called **Jalen** (it was "Jarvis" earlier; you will see both names in the code). He is a student who runs a Telegram channel for a machine-learning community ("AI engineering & Machine learning", public link `t.me/MLcommunityy`). His personal Telegram is `@Ml_eengineer`.
- His goal: Jalen should hear every question, do any task on his laptop "without lifting a finger", control his browser and software, research properly (arXiv, Wikipedia, the web), move things between drives (C: is nearly full, D: has space), run his Telegram (posts with premium emoji, DMs, voice messages), and never make silly mistakes.
- **How he communicates:** casual, fast, often voice-typed with typos. Read for intent. He gets frustrated when work stops or when he is told something is happening that is not.
- **How you communicate with him:**
  - Plain language, short. Lead with what changed for him.
  - **Use polls (the AskUserQuestion tool) for every decision that is his and for every action only he can do.** Put the recommended option first, marked "(Recommended)". He explicitly asked for this.
  - Report done and not done together. Never imply something works if you did not verify it.
  - **Never claim that agents or processes are running without checking.** A session restart ends background agents. Verify with real evidence (recent journal writes, processes, commits), not file timestamps alone.
  - When you write about a security fix, describe it in one or two neutral sentences: what was wrong, what it does now. Do not write step-by-step descriptions of how something could be abused. Commit messages follow the same rule.
- He wants many agents working in parallel. Use them, within the RAM budget in Section 5.5.

---

## 2. The project in one page

- **Machine:** Windows 10, 8 GB RAM with about 1 to 1.7 GB usually free, C: almost full (about 8 GB free), D: about 280 to 300 GB free. No local LLM, no PyTorch. Heavy work happens remotely.
- **Language:** Python 3.12+ (the venv runs 3.13). There is no linter, no type checker, no CI and no git remote. Do not push anywhere.
- **Run it:** always through the project venv: `.venv\Scripts\python.exe`. Never bare `python`, which is a different install.
  - `.\jalen.ps1 start` runs voice, `.\jalen.ps1 text` runs typed mode, and `.\jalen.ps1 check` runs diagnostics (now including Google and Telegram accounts).
  - `python run.py --status` says whether a Jalen is running. `python run.py --why` says why the last run stopped.
- **Main loop (`jalen/app.py`):** microphone → wake word or name → voice detection → speech-to-text (Groq, falling back to Moonshine) → the address gate (`should_act_on`) → `process(text)`, which tries the fast router first.
  - **Router:** `jalen/brain/router.py`, more than 136 regex rules where the first match wins. Matches run locally through `handle_local`.
  - **Brain:** otherwise the brain handles it (`jalen/brain/agent.py`, Claude Agent SDK, `ClaudeSDKClient`).
  - **Speech:** replies are spoken with edge-tts.
- **Tools:** plain functions in `jalen/tools/*.py`, each module with a `REGISTRY`. `TOOL_SPECS` in `jalen/brain/tools.py` describes them to the model. Every tool has a tier in `config/safety.yaml`.
- **Safety engine (`jalen/safety.py`):** tiers are GREEN (run), AMBER (announce, then run unless he says stop), RED (spoken yes required) and BLACK (refused).
  - **Never-touch list:** protected paths, filename patterns and domains, resolved the way Windows resolves them.
  - **Injection guard:** after Jalen reads untrusted text (email, web, Telegram, another model), the turn is "tainted" (`jalen/taint.py`). RED and AMBER tools are then refused, and so are the GREEN tools listed in `injection_guard.refuse_from_content`.
  - **Router exception:** a router rule that matched his own words, untouched by expansion, is his instruction (`his_own_words` in `handle_local`). Read the CLAUDE.md invariant about it before changing anything nearby.
- **Audit log:** `data/audit.jsonl` holds every utterance, action and timing line. It is the main source of truth about real use, so read it (read-only) to measure before changing behaviour.
- **Accounts today:**
  - **Brain:** signed in, with `CLAUDE_CODE_OAUTH_TOKEN` in `.env`.
  - **Google:** connected, but the OAuth app is in Testing mode, so the login expires around 8 October unless he publishes it.
  - **Telegram:** personal account signed in.
  - **Browsers:** ChatGPT is signed in in Jalen's own Chrome profile, and claude.ai may be signed in there too.

---

## 3. Hard rules (non-negotiable)

1. **Secrets:**
   - Never read, print, log or commit `.env`, `data/vault.json`, `data/telegram_user.session` or any token file.
   - `vault.get_secret()` must never become a tool result.
   - Never put `ANTHROPIC_API_KEY` in `.env`, because that would switch billing.
2. **No outward actions during testing:** no real Telegram messages, emails, posts, form submissions or purchases. Tests use fakes. Live checks are read-only.
3. **Git:**
   - No `git reset --hard`, no `git clean`, no force operations, no push.
   - No bare `git stash` (the stash stack is shared between sessions).
   - Stage explicit paths only, never `git add -A` or `git add .`.
   - Never delete, prune or modify worktrees or branches you did not create.
4. **Text through shells:** never write a regex, a backslash or non-ASCII text through a shell heredoc (a `\b` arrives as a literal 0x08 byte). Use the Edit or Write tools.
5. **Tests:**
   - Always run them as `.venv\Scripts\python.exe -m pytest ...`, never bare `pytest.exe`.
   - The gate is the command in Section 4.
   - The suite must never write into the real `data/` folder. Snapshot `data/` (excluding `data/browser_profile`) before and after every full gate.
6. **Real Chrome:**
   - Tests that start a real Chrome run headless, because `tests/conftest.py` sets `JALEN_TEST_HEADLESS_CHROME=1`. Never add a test that opens a visible browser.
   - Never kill Chrome processes. His everyday Chrome and Jalen's Chrome profile (`data/browser_profile`) belong to him.
7. **Live QA of the real Jalen:**
   - Typed mode only, and only after `run.py --status`.
   - Read-only requests plus opening and closing Notepad or Calculator.
   - Answer "no" to every confirmation.
   - Never ask it to read his email, his DMs or his files' contents.
8. **CLAUDE.md invariants are law:** injection-guard ordering, secrets never as tool results, the four `ClaudeAgentOptions` safety fields, never hardcode `origin='user'` (one documented exception), the two gates (microphone and address) must agree, the address gate is also the echo defence, and never pass user text to a `.cmd`.

---

## 4. Conventions and the test gate

- **Full gate:**
  `.venv\Scripts\python.exe -m pytest tests/ -q -p no:cacheprovider --ignore=tests/benchmark_latency.py --ignore=tests/benchmark_open.py --ignore=tests/benchmark_phrasing.py`
  About 4,000 tests; it takes 4 to 8 minutes depending on load. Run full gates in the background.
- **Known failures:**
  - **On this machine:** `test_overhaul_fixes::test_real_typos_and_abbreviations_still_resolve[capcut]` (CapCut's files are gone). Every other failure in the main checkout is real.
  - **In a worktree only** (no `.env`, `models/`, `data/` or `.venv` there): `test_attachments::test_the_refusal_is_not_overridable`, `test_conversation_requests` M1 x2, `test_native_host_process` x2, `test_voice_pipeline` x5 and `test_wake_both_names` x2. Corpus tests skip unless `JALEN_AUDIT_LOG` points at a read-only copy of `data/audit.jsonl`.
  - **Network:** three voice tests touch live Groq and edge-tts and can be flaky. Re-run them in isolation before believing a failure.
- **Process per change:**
  1. Discover and measure (read the code and `data/audit.jsonl`).
  2. Write a RED test and show it failing.
  3. Implement.
  4. Run the focused tests, then the full gate.
  5. Get an independent review by a separate agent that judges the diagnosis and the fix separately.
  6. Merge through the procedure in Section 5.4.
- **Commit messages:** the subject is a past-tense sentence naming what was *discovered*, not what you changed. The body explains the evidence, what changed and what was not done, and ends `<total> tests, <N> new.` Write the message with the Write tool and commit with `git commit -F <file>`.
- **Tools:**
  - A tool is a module-level function that returns a spoken-quality string. Errors are returned as sentences, never raised.
  - Adding a tool takes three edits: `REGISTRY`, `TOOL_SPECS` and a tier in `config/safety.yaml`.
  - A GREEN tool that acts on the world must also go on `injection_guard.refuse_from_content`.
- **Constants and config:**
  - Every tuned constant carries its measurement in a comment, or says NOT MEASURED.
  - Config is read through `cfg.get_path(...)` in `__init__`.
  - Dead config keys are labelled `[NOT READ]`, never deleted.

---

## 5. How the two sessions collaborate without colliding

### 5.1 Roles

- **Session A (integrator, already running):** owns `main`, merges every branch, and owns the safety engine, Telegram, the browser and web agent, folder moves and the listening and microphone loop.
- **Session B (you):** owns the "everyday quality" backlog in Section 7: routing mistakes, reply quality, missing machine facts, the disk-usage answer and research reliability. You build on branches, and you may merge into `main` yourself only through the lock in Section 5.4.

### 5.2 File ownership

| Area | Owner | Files |
|---|---|---|
| Router rules | **B** | `jalen/brain/router.py` |
| Reply text joining, reply tone | **B** | the text-assembly part of `Brain.ask` in `jalen/brain/agent.py`, and the tone and wording parts of `Brain.system_prompt()` |
| Machine facts | **B** | `jalen/tools/sysinfo.py`, `jalen/tools/system.py`, `jalen/tools/desktop.py` |
| Research | **B** | `jalen/tools/research.py` (except `_fetch`'s safety checks, see below) |
| Safety engine, taint, tiers | **A** | `jalen/safety.py`, `jalen/taint.py`, `config/safety.yaml` (B may edit only `injection_guard.suspicious_markers`, after announcing it on the board) |
| Telegram | **A** | `jalen/tools/messaging.py`, `stickers.py`, `attachments.py`, `drafting.py` |
| Browser and web agent | **A** | `jalen/tools/webagent.py`, `webforms.py`, `browser_ext.py`, `profile.py`, `autofill.py`, `otp.py`, `vault.py` |
| Files and folder moves | **A** | `jalen/tools/filesystem.py`, `foldermove.py`, `devwork.py`, `tasks.py` |
| Microphone and address gates | **A** | the `run()` and `should_act_on` parts of `jalen/app.py` |
| Shared, announce first | both | `jalen/brain/tools.py` (`TOOL_SPECS`), `jalen/brain/agent.py` (outside B's parts), `tests/conftest.py`, `CLAUDE.md`, `config/jalen.yaml` |

Before you touch any file owned by A or marked shared, write on the board what you will change and why, then wait 10 minutes for a reply line from A. If you need something in A's area, such as a new public function in `webagent.py`, ask on the board instead of editing it.

### 5.3 The coordination board

The board is `.claude/COORDINATION.md` in the repository root. It is untracked, and both sessions read and append to it.

- Append one line per event: `YYYY-MM-DD HH:MM | B | started <topic> on branch session-b/<topic> | files: ...`, `... | B | merged <sha> <subject>`, `... | B | needs from A: ...`.
- Read it before starting a task, before touching a shared file, and before merging.
- Never edit or delete the other session's lines.

### 5.4 Branches, worktrees and the merge lock

1. **Your own worktree for every task:**
   `git worktree add .claude/worktrees/session-b-<topic> -b session-b/<topic> main`
   Work and commit only there. Your agents use `isolation: worktree` or worktrees you create.
2. **Before merging,** bring your branch up to date in your worktree: `git rebase main`. Resolve conflicts, then re-run your focused tests.
3. **The merge lock:** `.claude/MERGE.lock` in the repository root. Create it atomically, for example in PowerShell: `New-Item -ItemType File .claude\MERGE.lock -ErrorAction Stop`, then write `B <time> <topic>` into it.
   - If it already exists, the other session is merging. Wait and check again every few minutes.
   - A lock older than 90 minutes can be assumed stale. Ask the owner in a poll before you remove someone else's lock.
4. **While you hold the lock,** in the main checkout:
   1. `git status` must show no modified tracked files. If it does, someone is mid-merge: release the lock and wait.
   2. `git cherry-pick -n <your commits>` or `git merge --squash session-b/<topic>`.
   3. Snapshot `data/`, run the full gate and snapshot again.
   4. Commit with the convention, using the real test total from main.
   5. Delete the lock. Add a board line with the sha.
5. Never leave the main checkout dirty. Never commit someone else's uncommitted changes.

### 5.5 Machine budget

- **RAM is the bottleneck.** Across both sessions, run at most about 6 agents at once. Check free memory first, and do not start a new agent below about 1.2 GB free.
- **One full gate per session at a time,** run in the background.
- **Timing tests can fail under load.** Re-run them alone before calling anything a regression.
- **Never stop, kill or "clean up" processes, worktrees or branches you did not create.**

---

## 6. What happened before you joined (context)

Two long sessions hardened Jalen. Highlights, all in `git log`:

- **Brain and diagnostics:** the brain had been signed out for days and read the error aloud. That is now detected and said once, and `jalen check` reports it along with Google and Telegram.
- **Confirmations, kill switch and gates:** "yes" binds to the question that was asked, Jalen's own "Confirm?" cannot approve itself, the stop phrase works however speech recognition spells it, and an answer to a question never clears the injection guard.
- **Never-touch protection:** it now