r"""
Giving a coding agent a job, and being there when it finishes.

His ask, nearly verbatim: "vs code agent might think for an hour, after it
has been done thinking, it should be able to get notified, as soon as the
agent has finished, it should take the lead read its response learn and
identify how many percent of the my expectations has been met, what is the
problem".

WHAT WAS MISSING
----------------
`ask_claude_code` opens a console window and returns. That is fire and
forget: Jalen cannot read the result, cannot tell when it finished, and
cannot say whether it did what was asked. For a five-second edit that is
fine. For an agent that thinks for an hour it is useless — the whole value
is in the part after it stops.

So this module runs the agent HEADLESS (`claude -p`), captures everything it
says to a file, tracks the job across restarts, and can report afterwards on
three separate things that are easy to confuse:

    what the agent SAID it did       its own summary, which is a claim
    what actually CHANGED on disk    git diff, which is evidence
    what he ASKED FOR                recorded at the start, before anyone
                                     had an opinion about whether it worked

THE PERCENTAGE IS NOT COMPUTED HERE, AND THAT IS DELIBERATE
-----------------------------------------------------------
He asked for "how many percent of my expectations has been met". A number
produced by counting keywords would be exactly this project's recurring bug
— output that sounds right and is not — and it would be worse here than
usual, because a confident 85% is precisely the kind of thing you stop
checking.

So `review_coding_job` gathers the three things above and hands them to the
brain to judge. The evidence is mechanical; the judgement is the model's,
and it has the original request in front of it when it makes it.

WHY HEADLESS RATHER THAN DRIVING THE GUI
----------------------------------------
The desktop app and the VS Code extension have no supported way to be given
a prompt and asked for the answer back. Driving them means synthetic
keystrokes into a window that may not have focus — the failure mode that has
already produced "I played it" for a video that never started, and a
password typed into a search box. `claude -p` is a documented interface that
returns text and an exit code.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
JOBS_PATH = ROOT / "data" / "coding_jobs.json"
LOG_DIR = ROOT / "data" / "coding_jobs"

# A long agent run is the case this exists for, so the ceiling is generous.
# It is still a ceiling: a wedged agent must not hold a slot forever.
DEFAULT_TIMEOUT_S = 3600.0
MAX_LOG_CHARS = 60_000

# How much the agent may do without asking.
#
# "acceptEdits", NOT "bypassPermissions", and the distinction is the whole
# safety story of this module. Headless `claude -p` has no way to show an
# interactive permission prompt, so with the default it declines every write
# and reports it — which is honest, and useless: the first real test came
# back "The write was blocked, I don't have permission to create files in
# this directory yet", having done nothing.
#
#   acceptEdits         may create and edit files in the folder it was given
#   bypassPermissions   may also run arbitrary shell commands, unattended,
#                       on his machine, with no one watching
#
# The second is what "let it do anything" sounds like and is not what anyone
# actually wants running while they are asleep. --add-dir bounds the edits to
# the project folder, and Claude Code's own gate still stands in front of
# anything more dangerous than a file write.
PERMISSION_MODE = "acceptEdits"


# ---------------------------------------------------------------- job store
def _load_jobs() -> dict[str, dict]:
    try:
        blob = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        return blob if isinstance(blob, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_jobs(jobs: dict[str, dict]) -> None:
    """
    Temp-file-and-replace. A job list half-written by a process that died is
    a job list that reads as corrupt, and losing the record of a running
    agent is worse than losing the agent.
    """
    try:
        JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = JOBS_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(jobs, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, JOBS_PATH)
    except OSError:
        pass


def _update(job_id: str, **fields: Any) -> None:
    jobs = _load_jobs()
    if job_id in jobs:
        jobs[job_id].update(fields)
        _save_jobs(jobs)


def _claude_cli() -> str | None:
    return shutil.which("claude")


def _resolve(folder: str) -> tuple[Path | None, str | None]:
    """A folder name or path -> a real directory, or a sentence saying why not."""
    raw = (folder or "").strip().strip('"')
    if not raw:
        return None, "Which folder should it work in?"
    path = Path(raw).expanduser()

    # The Desktop search is for BARE NAMES only — "SAT TOP web application",
    # the way he actually names a project out loud. A path that is already a
    # path is left alone.
    #
    # Without that guard "." resolved to `Desktop/.`, because that directory
    # exists: asking for the status of the current folder reported the
    # Desktop instead. Caught live. Same for "..", "./x" and anything with a
    # separator in it.
    looks_like_a_path = (
        path.is_absolute()
        or raw.startswith((".", "~"))
        or "/" in raw
        or "\\" in raw
    )
    if not looks_like_a_path:
        for base in (Path.home() / "Desktop", Path.home(), ROOT):
            if (base / raw).is_dir():
                path = base / raw
                break
    if not path.is_dir():
        return None, f"There's no folder at {path}."
    return path.resolve(), None


# ------------------------------------------------------------------ running
def start_coding_job(prompt: str, folder: str, expectation: str = "") -> str:
    """
    Start Claude Code on a job in the background — AMBER.

    Returns immediately with a job id. The agent may take an hour; Jalen
    checks on it and speaks up when it is done.

    `expectation` is what HE wanted, in his words, recorded now rather than
    reconstructed later. Judging afterwards from the agent's own summary is
    marking its homework against its own answer sheet.
    """
    text = (prompt or "").strip()
    if not text:
        return "Tell me what the job is and I'll start it."
    cli = _claude_cli()
    if cli is None:
        return (
            "The Claude Code CLI isn't on PATH. Install it with "
            "npm install -g @anthropic-ai/claude-code."
        )

    path, problem = _resolve(folder)
    if problem:
        return problem

    # AFTER the folder, deliberately. A missing required argument is a more
    # basic problem than a thin brief, and reporting the quality complaint
    # while he has not even said which folder is answering a question he did
    # not get to yet.
    #
    # The same standard every other handoff is held to: an agent that may run
    # for an hour on a two-line guess is the most expensive way there is to
    # get the wrong answer. See handoff.brief_or_problem.
    from .handoff import brief_or_problem

    if problem := brief_or_problem(text, "Claude Code"):
        return problem

    job_id = uuid.uuid4().hex[:8]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{job_id}.log"

    jobs = _load_jobs()
    jobs[job_id] = {
        "id": job_id,
        "prompt": text,
        "expectation": expectation.strip(),
        "folder": str(path),
        "log": str(log_path),
        "state": "running",
        "started_at": time.time(),
        "started_iso": time.strftime("%Y-%m-%d %H:%M"),
        "baseline": _git_head(path),
        "notified": False,
    }
    _save_jobs(jobs)

    def run() -> None:
        try:
            with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
                completed = subprocess.run(
                    [cli, "-p", text, "--permission-mode", PERMISSION_MODE,
                     "--add-dir", str(path)],
                    cwd=str(path),
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    timeout=DEFAULT_TIMEOUT_S,
                    text=True,
                )
            _update(job_id, state="finished", exit_code=completed.returncode,
                    ended_at=time.time())
        except subprocess.TimeoutExpired:
            _update(job_id, state="timeout", ended_at=time.time())
        except Exception as exc:  # noqa: BLE001
            _update(job_id, state="failed", error=f"{type(exc).__name__}: {exc}",
                    ended_at=time.time())

    threading.Thread(target=run, name=f"claude-job-{job_id}", daemon=True).start()
    return (
        f"Started Claude Code on it in {path.name} — job {job_id}. "
        "It runs in the background; I'll tell you the moment it's finished."
    )


def start_background_run(args: list[str], cwd: Path, label: str,
                         summarise=None) -> str:
    """
    Run any long command in the background and announce it when it ends.

    Same machinery as a coding job, because the problem is the same one: a
    command that takes minutes must not be run inside a turn. Jalen's own
    test suite is 140 seconds, and 140 seconds of silence from a voice
    assistant is indistinguishable from a hang.

    `summarise` turns the captured output into the sentence he hears. It is
    passed in rather than hardcoded so the caller owns what "finished well"
    means — pytest counts tests, another command might count something else.
    """
    job_id = uuid.uuid4().hex[:8]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{job_id}.log"

    jobs = _load_jobs()
    jobs[job_id] = {
        "id": job_id,
        "prompt": label,
        "expectation": label,
        "folder": str(cwd),
        "log": str(log_path),
        "state": "running",
        "kind": "command",
        "started_at": time.time(),
        "started_iso": time.strftime("%Y-%m-%d %H:%M"),
        "baseline": "",
        "notified": False,
    }
    _save_jobs(jobs)

    def run() -> None:
        try:
            with open(log_path, "w", encoding="utf-8", errors="replace") as fh:
                completed = subprocess.run(
                    args, cwd=str(cwd), stdout=fh, stderr=subprocess.STDOUT,
                    timeout=DEFAULT_TIMEOUT_S, text=True,
                )
            summary = ""
            if summarise is not None:
                try:
                    summary = summarise(
                        log_path.read_text(encoding="utf-8", errors="replace")
                    )
                except Exception as exc:  # noqa: BLE001
                    summary = f"(couldn't read the result: {exc})"
            _update(job_id, state="finished", exit_code=completed.returncode,
                    ended_at=time.time(), summary=summary)
        except subprocess.TimeoutExpired:
            _update(job_id, state="timeout", ended_at=time.time())
        except Exception as exc:  # noqa: BLE001
            _update(job_id, state="failed", error=f"{type(exc).__name__}: {exc}",
                    ended_at=time.time())

    threading.Thread(target=run, name=f"bg-{job_id}", daemon=True).start()
    return f"Running {label} in the background - I'll tell you how it went."


def list_coding_jobs(limit: int = 10) -> str:
    """What Claude Code is working on, and what it finished — GREEN."""
    jobs = sorted(_load_jobs().values(), key=lambda j: j.get("started_at", 0),
                  reverse=True)[:limit]
    if not jobs:
        return "No coding jobs yet."
    lines = []
    for job in jobs:
        state = job.get("state", "?")
        if state == "running":
            mins = (time.time() - job.get("started_at", 0)) / 60
            state = f"running for {mins:.0f} min"
        lines.append(
            f"  {job['id']}  {state}  {Path(job.get('folder','?')).name}  "
            f"- {job.get('prompt','')[:60]}"
        )
    return f"{len(jobs)} coding job(s):\n" + "\n".join(lines)


def finished_unreported_jobs() -> list[dict]:
    """
    Jobs that have finished and that he has not been told about.

    Used by the orchestrator's poll, which is what turns "it finished an
    hour ago and nobody noticed" into "it just finished". Marks them as
    notified, so a job is announced exactly once.
    """
    jobs = _load_jobs()
    done = [
        j for j in jobs.values()
        if j.get("state") in ("finished", "timeout", "failed")
        and not j.get("notified")
    ]
    for job in done:
        jobs[job["id"]]["notified"] = True
    if done:
        _save_jobs(jobs)
    return done


# ------------------------------------------------------------------ reading
def _git(path: Path, *args: str, timeout: float = 20.0) -> str:
    """
    Run git and return its OUTPUT, or "" if the command failed.

    Returning stderr on failure looks helpful and is a trap: "fatal: not a
    git repository" is a non-empty string, so every `if _git(...)` treats a
    failed command as a successful one. That inverted the repository check —
    init_git_repo reported a fresh folder as "already a git repository" and
    refused to initialise it, while project_status printed the fatal error
    as if it were a branch name. Checked live before this comment existed.

    Failures are reported through _git_failed() when a caller needs the
    reason; everything else just wants "did it work".
    """
    try:
        out = subprocess.run(
            ["git", *args], cwd=str(path), capture_output=True, text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode != 0:
        return ""
    return (out.stdout or "").strip()


def _is_repo(path: Path) -> bool:
    """
    True only when this folder is inside a git work tree.

    `rev-parse HEAD` is not the test: a freshly initialised repository has no
    commits, so HEAD does not resolve and a real repository reads as none.
    """
    return _git(path, "rev-parse", "--is-inside-work-tree") == "true"


def _git_head(path: Path) -> str:
    return _git(path, "rev-parse", "HEAD")


def review_coding_job(job_id: str = "") -> str:
    """
    Everything needed to judge whether the agent did the job — GREEN.

    Returns three things side by side, labelled, and deliberately does NOT
    conclude: what he asked for, what the agent said, and what actually
    changed on disk. The brain reads this and gives the verdict, with the
    original request in front of it.

    The separation is the point. An agent's summary is a CLAIM. `git diff`
    is EVIDENCE. This project has been bitten repeatedly by treating the
    first as the second.
    """
    jobs = _load_jobs()
    if not jobs:
        return "No coding jobs to review."
    if job_id:
        job = jobs.get(job_id.strip())
        if job is None:
            return f"No job {job_id}. Say 'list coding jobs' to see them."
    else:
        job = max(jobs.values(), key=lambda j: j.get("started_at", 0))

    path = Path(job.get("folder", "."))
    parts = [
        f"JOB {job['id']} - {job.get('state', '?')}",
        f"folder: {path}",
        "",
        "WHAT HE ASKED FOR (recorded before the job started):",
        f"  {job.get('expectation') or job.get('prompt', '')}",
        "",
        "THE PROMPT THE AGENT WAS GIVEN:",
        f"  {job.get('prompt', '')[:1500]}",
        "",
    ]

    if job.get("state") == "running":
        mins = (time.time() - job.get("started_at", 0)) / 60
        parts.append(f"STILL RUNNING - {mins:.0f} minutes so far. Nothing to judge yet.")
        return "\n".join(parts)

    if job.get("error"):
        parts += ["THE JOB FAILED:", f"  {job['error']}", ""]

    log = Path(job.get("log", ""))
    try:
        said = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        said = ""
    if len(said) > MAX_LOG_CHARS:
        said = said[:MAX_LOG_CHARS] + "\n[...truncated...]"
    parts += ["WHAT THE AGENT SAID (its own claim, not evidence):",
              said.strip() or "  (it produced no output)", ""]

    baseline = job.get("baseline") or ""
    if not path.is_dir():
        # Distinguished from "not a repository", which is what it used to
        # say: a folder that has been moved or deleted since the job ran is
        # a different fact, and reporting the wrong one sends whoever reads
        # it looking for a git problem that does not exist.
        parts += ["WHAT ACTUALLY CHANGED ON DISK:",
                  f"  The folder {path} no longer exists, so there is nothing "
                  "left to inspect. Judge on the agent's output alone, and say "
                  "that is what you are doing."]
    elif _is_repo(path):
        changed = _git(path, "diff", "--stat", baseline) if baseline else _git(path, "diff", "--stat")
        untracked = _git(path, "ls-files", "--others", "--exclude-standard")
        status = _git(path, "status", "--porcelain")
        parts += ["WHAT ACTUALLY CHANGED ON DISK (evidence):"]
        parts.append(f"  files touched:\n{changed or '  (nothing)'}")
        if untracked:
            parts.append(f"  new files:\n{untracked}")
        if not changed and not untracked and not status:
            parts.append("  NOTHING CHANGED. Whatever it said, it did not edit this repo.")
    else:
        parts += ["WHAT ACTUALLY CHANGED ON DISK:",
                  "  Not a git repository, so there is no before-and-after to "
                  "compare. Say 'initialise git here' first next time and the "
                  "next job becomes checkable."]

    parts += [
        "",
        "NOW JUDGE IT. Compare what he asked for against what actually "
        "changed, not against what the agent said. Say roughly what "
        "proportion of the request is genuinely done, name anything missing "
        "or wrong, and say plainly if the agent claimed something the diff "
        "does not support.",
    ]
    return "\n".join(parts)


def project_status(folder: str) -> str:
    """Git state and shape of a project folder — GREEN."""
    path, problem = _resolve(folder)
    if problem:
        return problem
    if not _is_repo(path):
        entries = sorted(p.name for p in path.iterdir())[:40]
        return (
            f"{path} is not a git repository.\n"
            f"Contents: {', '.join(entries) or '(empty)'}"
        )
    return "\n".join([
        f"{path}",
        f"branch : {_git(path, 'rev-parse', '--abbrev-ref', 'HEAD')}",
        f"head   : {_git(path, 'log', '-1', '--oneline')}",
        f"status :\n{_git(path, 'status', '--short') or '  clean'}",
    ])


def init_git_repo(folder: str) -> str:
    """
    Start version control in a folder — AMBER.

    Worth doing before handing work to an agent, and this says why in its
    own reply: without a baseline commit there is no before-and-after, and
    "what did it actually change" becomes unanswerable.
    """
    path, problem = _resolve(folder)
    if problem:
        return problem
    if _is_repo(path):
        return f"{path.name} is already a git repository."
    if not shutil.which("git"):
        return "Git isn't installed, or isn't on PATH."
    _git(path, "init")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "Baseline before agent work")
    head = _git(path, "log", "-1", "--oneline")
    if not head:
        return (
            f"Ran git init in {path.name}, but nothing was committed - the "
            "folder may be empty, or git has no user.name/user.email set."
        )
    return (
        f"{path.name} is now a git repository, with everything committed as a "
        f"baseline ({head}). Anything an agent changes from here is visible "
        "as a diff."
    )


def open_in_vscode(folder: str) -> str:
    """Open a folder in VS Code — AMBER."""
    path, problem = _resolve(folder)
    if problem:
        return problem
    exe = shutil.which("code") or shutil.which("code.cmd")
    if exe is None:
        return (
            "VS Code's `code` command isn't on PATH. In VS Code press "
            "Ctrl+Shift+P and run 'Shell Command: Install code command in PATH'."
        )
    try:
        subprocess.Popen([exe, str(path)], shell=True)
    except OSError as exc:
        return f"Couldn't start VS Code: {exc}"
    return f"VS Code is opening {path.name}."


REGISTRY: dict[str, Any] = {
    "start_coding_job": start_coding_job,
    "list_coding_jobs": list_coding_jobs,
    "review_coding_job": review_coding_job,
    "project_status": project_status,
    "init_git_repo": init_git_repo,
    "open_in_vscode": open_in_vscode,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
