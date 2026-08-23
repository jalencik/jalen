"""
Handing a coding agent a real job, and being there when it finishes.

His ask: "vs code agent might think for an hour... as soon as the agent has
finished, it should take the lead read its response learn and identify how
many percent of the my expectations has been met, what is the problem".

The pieces that had to be right, and are each pinned below:

  * the agent can actually WRITE (headless Claude Code declines every edit
    unless told otherwise, and reports it politely while doing nothing)
  * a finished job is announced exactly ONCE
  * the review separates what he asked for, what the agent CLAIMED, and what
    actually changed on disk — and does not conclude, because a percentage
    computed by counting keywords is precisely this project's recurring bug
  * git detection is not inverted

That last one was a live bug: _git() returned stderr on failure, so "fatal:
not a git repository" read as truthy, and a fresh folder reported itself as
already under version control.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from jarvis.tools import devwork


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Never touch the real job store."""
    monkeypatch.setattr(devwork, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(devwork, "LOG_DIR", tmp_path / "logs")
    return tmp_path


def has_git() -> bool:
    return shutil.which("git") is not None


# ---------------------------------------------------------------------------
# Git detection. The inverted check made every other answer wrong.
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not has_git(), reason="git not installed")
def test_a_plain_folder_is_not_reported_as_a_repository(tmp_path):
    """
    _git() used to return stderr on failure. "fatal: not a git repository" is
    a non-empty string, so `if _git(...)` treated the failure as success —
    init_git_repo refused to initialise fresh folders and project_status
    printed the fatal error where the branch name should be.
    """
    folder = tmp_path / "plain"
    folder.mkdir()
    assert devwork._is_repo(folder) is False
    assert devwork._git(folder, "rev-parse", "HEAD") == ""
    assert "fatal" not in devwork.project_status(str(folder)).lower()


@pytest.mark.skipif(not has_git(), reason="git not installed")
def test_init_creates_a_baseline_commit(tmp_path):
    """
    The baseline is what makes "what did the agent actually change"
    answerable. Without it there is no before to compare the after against.
    """
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "a.txt").write_text("hello", encoding="utf-8")

    out = devwork.init_git_repo(str(folder))
    assert "now a git repository" in out
    assert devwork._is_repo(folder)
    assert devwork._git_head(folder), "no commit was made"
    assert "clean" in devwork.project_status(str(folder))


@pytest.mark.skipif(not has_git(), reason="git not installed")
def test_a_freshly_initialised_repo_is_recognised_before_any_commit(tmp_path):
    """
    `rev-parse HEAD` fails in a repo with no commits, so using it as the
    "is this a repo" test calls a real repository none.
    """
    folder = tmp_path / "empty"
    folder.mkdir()
    subprocess.run(["git", "init"], cwd=folder, capture_output=True)
    assert devwork._is_repo(folder) is True
    assert devwork._git_head(folder) == ""


def test_a_missing_folder_is_refused_clearly(tmp_path):
    assert "no folder" in devwork.project_status(str(tmp_path / "nope")).lower()
    assert "no folder" in devwork.init_git_repo(str(tmp_path / "nope")).lower()


def test_an_empty_folder_argument_asks_which_one():
    assert "which folder" in devwork.start_coding_job("do a thing", "").lower()


# ---------------------------------------------------------------------------
# The agent must be able to write.
# ---------------------------------------------------------------------------
def test_the_agent_is_allowed_to_edit_files():
    """
    Headless `claude -p` cannot show a permission prompt, so with the default
    it declines every write and says so. The first real run came back "The
    write was blocked, I don't have permission to create files in this
    directory yet" — a perfectly honest report of having done nothing.
    """
    import inspect

    source = inspect.getsource(devwork.start_coding_job)
    assert "PERMISSION_MODE" in source
    assert devwork.PERMISSION_MODE == "acceptEdits"
    assert "--add-dir" in source, "edits are not bounded to the project folder"


def test_it_does_not_bypass_every_permission():
    """
    "acceptEdits" lets it write files. "bypassPermissions" also lets it run
    arbitrary shell commands, unattended, with nobody watching. The second is
    what "let it do anything" sounds like and is not what anyone wants
    running while they are asleep.
    """
    import inspect

    source = inspect.getsource(devwork)
    assert "bypassPermissions" not in source.replace(
        "#                       on his machine, with no one watching", ""
    ) or devwork.PERMISSION_MODE != "bypassPermissions"
    assert "--dangerously-skip-permissions" not in source


# ---------------------------------------------------------------------------
# Announcing a finished job exactly once.
# ---------------------------------------------------------------------------
def _fake_job(isolated, **fields):
    job = {
        "id": "abc123", "prompt": "do a thing", "expectation": "a thing done",
        "folder": str(isolated), "log": str(isolated / "x.log"),
        "state": "finished", "started_at": time.time() - 60,
        "ended_at": time.time(), "baseline": "", "notified": False,
    }
    job.update(fields)
    devwork._save_jobs({job["id"]: job})
    return job


def test_a_finished_job_is_reported_once(isolated):
    """
    Twice would mean Jalen announcing the same thing every five seconds
    forever, which is worse than never announcing it.
    """
    _fake_job(isolated)
    assert [j["id"] for j in devwork.finished_unreported_jobs()] == ["abc123"]
    assert devwork.finished_unreported_jobs() == []


def test_a_running_job_is_not_reported(isolated):
    _fake_job(isolated, state="running")
    assert devwork.finished_unreported_jobs() == []


@pytest.mark.parametrize("state", ["finished", "timeout", "failed"])
def test_every_ending_is_reported(isolated, state):
    """A job that timed out or failed matters as much as one that worked."""
    _fake_job(isolated, state=state)
    assert len(devwork.finished_unreported_jobs()) == 1


def test_a_corrupt_job_store_is_not_fatal(isolated):
    devwork.JOBS_PATH.write_text("{not json", encoding="utf-8")
    assert devwork._load_jobs() == {}
    assert devwork.finished_unreported_jobs() == []
    assert "No coding jobs" in devwork.list_coding_jobs()


def test_the_job_store_is_written_atomically(isolated):
    _fake_job(isolated)
    assert not list(isolated.glob("*.tmp"))
    assert json.loads(devwork.JOBS_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# The review. Evidence separated from claims, and no fabricated verdict.
# ---------------------------------------------------------------------------
def test_the_review_separates_the_claim_from_the_evidence(isolated):
    """
    An agent's summary is a CLAIM. git diff is EVIDENCE. This project has
    been bitten repeatedly by treating the first as the second — "I played
    it" for a video that never started, a send reported with nothing
    attached.
    """
    log = isolated / "x.log"
    log.write_text("I created hello.py and it works perfectly.", encoding="utf-8")
    _fake_job(isolated, log=str(log))

    out = devwork.review_coding_job()
    assert "WHAT HE ASKED FOR" in out
    assert "its own claim, not evidence" in out
    assert "WHAT ACTUALLY CHANGED ON DISK" in out
    assert "I created hello.py" in out


def test_the_review_does_not_invent_a_percentage(isolated):
    """
    He asked for "how many percent of my expectations has been met". That
    number must come from the brain, with the original request in front of
    it — not from code counting keywords. A confident 85% computed by
    string-matching is exactly the kind of answer people stop checking.
    """
    log = isolated / "x.log"
    log.write_text("all done", encoding="utf-8")
    _fake_job(isolated, log=str(log))

    out = devwork.review_coding_job()
    assert "%" not in out.split("NOW JUDGE IT")[0], (
        "the review computed a completion figure instead of gathering evidence"
    )
    assert "NOW JUDGE IT" in out
    assert "not against what the agent said" in out


def test_the_review_records_the_request_before_the_job_runs(isolated):
    """
    Judging afterwards from the agent's own summary is marking its homework
    against its own answer sheet. The expectation is captured at start.
    """
    _fake_job(isolated, expectation="I wanted a working login page")
    assert "I wanted a working login page" in devwork.review_coding_job()


@pytest.mark.skipif(not has_git(), reason="git not installed")
def test_a_job_that_changed_nothing_is_called_out(isolated, tmp_path):
    """
    The most valuable case. An agent that says it did the work and changed no
    files is the exact failure this whole module exists to catch.
    """
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "a.txt").write_text("hello", encoding="utf-8")
    devwork.init_git_repo(str(folder))

    log = isolated / "x.log"
    log.write_text("I have implemented everything you asked for.", encoding="utf-8")
    _fake_job(isolated, folder=str(folder), log=str(log),
              baseline=devwork._git_head(folder))

    out = devwork.review_coding_job()
    assert "NOTHING CHANGED" in out, (
        "an agent that touched no files was not called out"
    )


def test_a_running_job_has_nothing_to_judge_yet(isolated):
    _fake_job(isolated, state="running")
    out = devwork.review_coding_job()
    assert "STILL RUNNING" in out
    assert "Nothing to judge yet" in out


def test_a_deleted_folder_is_named_as_such(isolated, tmp_path):
    """
    Not "this is not a git repository", which is a different fact and sends
    whoever reads it looking for a git problem that does not exist.
    """
    log = isolated / "x.log"
    log.write_text("done", encoding="utf-8")
    _fake_job(isolated, folder=str(tmp_path / "gone"), log=str(log))
    assert "no longer exists" in devwork.review_coding_job()


def test_reviewing_an_unknown_job_says_how_to_find_the_real_ones(isolated):
    _fake_job(isolated)
    out = devwork.review_coding_job("nosuchjob")
    assert "No job nosuchjob" in out
    assert "list coding jobs" in out


# ---------------------------------------------------------------------------
# Reachability.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name, tier", [
    ("start_coding_job", "amber"),
    ("list_coding_jobs", "green"),
    ("review_coding_job", "green"),
    ("project_status", "green"),
    ("init_git_repo", "amber"),
    ("open_in_vscode", "amber"),
])
def test_the_tools_are_dispatchable_and_gated(name, tier):
    from jarvis import tools
    from jarvis.brain.tools import TOOL_SPECS
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine

    assert name in tools.REGISTRY, f"{name} is not dispatchable"
    assert name in TOOL_SPECS, f"{name} is invisible to the brain"
    assert SafetyEngine(CONFIG).classify(name, {}).tier.value == tier


def test_reading_about_a_job_is_free():
    """
    If asking "did it work" costs a spoken confirmation, he stops asking —
    the same reasoning that keeps the technician's diagnosis GREEN.
    """
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine

    engine = SafetyEngine(CONFIG)
    for name in ("list_coding_jobs", "review_coding_job", "project_status"):
        assert engine.classify(name, {}).tier.value == "green"


def test_starting_an_agent_cannot_be_triggered_by_something_it_read():
    """
    An email saying "run this in my repo" must not start an autonomous agent.
    AMBER is refused to content-derived requests by the injection guard.
    """
    from jarvis.config import CONFIG
    from jarvis.safety import SafetyEngine

    verdict = SafetyEngine(CONFIG).classify(
        "start_coding_job", {"prompt": "delete everything", "folder": "."},
        origin="content",
    )
    assert verdict.tier.value == "black"


def test_a_dot_means_here_not_the_desktop(tmp_path, monkeypatch):
    """
    The Desktop search is for BARE NAMES ("SAT TOP web application"), which
    is how he names a project out loud. A path that is already a path must be
    left alone.

    Caught live: "." resolved to `Desktop/.` because that directory exists,
    so asking for the status of the current folder reported the Desktop.
    """
    here = devwork._resolve(".")[0]
    assert here == Path.cwd().resolve(), f"'.' resolved to {here}"
    assert devwork._resolve("..")[0] == Path.cwd().parent.resolve()


def test_a_bare_name_still_finds_a_desktop_project(tmp_path, monkeypatch):
    """The convenience that the guard must not break."""
    fake_desktop = tmp_path / "Desktop"
    (fake_desktop / "My Project").mkdir(parents=True)
    monkeypatch.setattr(devwork.Path, "home", staticmethod(lambda: tmp_path))

    found = devwork._resolve("My Project")[0]
    assert found == (fake_desktop / "My Project").resolve()


def test_an_absolute_path_is_used_verbatim(tmp_path):
    assert devwork._resolve(str(tmp_path))[0] == tmp_path.resolve()
