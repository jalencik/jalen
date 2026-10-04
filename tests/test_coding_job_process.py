r"""
The background coding job, from the moment it is launched to the moment it
is stopped - things that were each wrong in a way that sounded right.

1. THE BRIEF WAS CUT AT LINE ONE. devwork._claude_cli() was a bare
   shutil.which("claude"), which on this machine is npm's claude.CMD.
   Windows runs a .CMD through cmd.exe, and cmd.exe truncates an argument at
   its first newline - so the brief lost every line after the first AND every
   flag after it (--permission-mode acceptEdits, --add-dir). coding.py had
   already fixed this for ask_claude_code; devwork never got the fix.

2. A TIMEOUT KILLED THE WRAPPER, NOT THE AGENT. subprocess's timeout ends the
   direct child. With a .CMD that child is cmd.exe, and claude.exe carries on
   editing his repository with nobody tracking it.

3. EVERY EXIT WAS "FINISHED". Exit code 1 - an auth failure, a crash - was
   announced as "just finished, say review the coding job".

4. "STOPPED X." STOPPED NOTHING. cancel_task wrote CANCELLED into a state
   nothing reads, and said so out loud as if it had stopped something.

Then a review of the fix for those four found the fix's own holes:

5. STOPPING MATCHED A SUBSTRING. A job in "app" was killed by "cancel my
   appointment", and with one job live, "cancel the job application email"
   ended an hour of an agent's work because it contained the word "job".

6. THE JOB STORE LOST ENDINGS. Creating a job was an unlocked
   load-insert-save, so an ending recorded in between was overwritten back
   to "running". And an unlocked READER was enough on its own: on Windows
   os.replace onto a file another handle has open fails (measured below),
   and the save swallowed it.

7. ON THE SHIM ROUTE THE AGENT WAS TOLD TO READ A FILE OUTSIDE ITS FOLDER.
   The brief was parked in %TEMP% and the agent got --add-dir for the
   project only; headless -p cannot ask permission to read anywhere else.

8. "STOPPED" ABOUT A JOB THAT HAD FINISHED ON ITS OWN, overwriting its real
   ending with "cancelled".

9. A TIMEOUT WHOSE KILL FAILED dropped the handle while the process lived.

10. A BACKGROUND COMMAND (the self-test) WAS CALLED A CODING JOB.

No test here spawns a real Claude Code: subprocess is faked throughout, and
the resolver is pointed at files in tmp_path.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from jalen.tools import coding, devwork

PAYLOAD = (
    "Objective \u2014 ship it. \"Quoted\", \u00fcn\u00efc\u00f6d\u00e9,\n"
    "and a second line with 100% of the %PATH% in it."
)

# Any test where a wrong answer could reach _kill_tree. On Windows every
# taskkill goes to the fake below; elsewhere a fake pid would meet a real
# os.killpg.
WINDOWS_ONLY = pytest.mark.skipif(
    sys.platform != "win32", reason="the fake pids must only ever meet a fake taskkill"
)

# What cmd.exe acts on inside an argument - written out here rather than
# imported from devwork, so the code is not graded against its own list.
CMD_ACTIVE = set('%&|<>^!"\r\n')


def survives_cmd(arg: str) -> bool:
    return arg.isascii() and not (set(arg) & CMD_ACTIVE)


def what_the_shim_is_handed(argv: list[str]) -> str:
    """
    The command line a .CMD actually receives. cmd.exe reads it up to the
    first line break and no further: everything after that, flags included,
    never reaches the script.
    """
    line = subprocess.list2cmdline(argv[1:])
    for brk in ("\r", "\n"):
        line = line.split(brk, 1)[0]
    return line


# ---------------------------------------------------------------------------
# A fake process table. Nothing real is ever started.
# ---------------------------------------------------------------------------
class FakeStdin(io.BytesIO):
    """A stdin pipe that remembers what was written and whether it was closed."""

    was_closed = False

    def close(self):
        # Recorded, not performed, so the bytes stay readable to the test.
        self.was_closed = True


class FakeProc:
    """Stands in for subprocess.Popen. The table decides how it behaves."""

    def __init__(self, table, argv, kw):
        self.table = table
        self.argv = argv
        self.kw = kw
        self.pid = 40000 + len(table.procs)
        self.returncode = None
        self.ended = threading.Event()
        self.stdin = FakeStdin() if kw.get("stdin") == subprocess.PIPE else None
        table.procs.append(self)
        out = kw.get("stdout")
        if table.output and hasattr(out, "write"):
            out.write(table.output)
            out.flush()
        if table.exit_code is not None and not table.hang:
            self._end(table.exit_code)

    def _end(self, code):
        if self.returncode is None:
            self.returncode = code
        self.ended.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is not None:
            return self.returncode
        if timeout is None or self.table.hang == "until-killed":
            # Blocks like a real long-running agent, until something ends it.
            if not self.ended.wait(timeout=10):
                raise AssertionError("the fake agent was never stopped")
            return self.returncode
        # "timeout": a bounded wait runs out of time, as an hour-long job would.
        raise subprocess.TimeoutExpired(self.argv, timeout)

    def kill(self):
        if not self.table.unkillable:
            self._end(-9)

    def terminate(self):
        self.kill()


class LiveProc:
    """A held process with no job thread behind it: running until ended."""

    def __init__(self, table):
        self.table = table
        self.pid = 40000 + len(table.procs)
        self.returncode = None
        table.procs.append(self)        # so the fake taskkill can find it

    def _end(self, code):
        if self.returncode is None:
            self.returncode = code

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def kill(self):
        if not self.table.unkillable:
            self._end(-9)


class FakeTable:
    def __init__(self):
        self.procs: list = []
        self.launched: list[list[str]] = []   # every argv, either API
        self.taskkills: list[tuple[list[str], dict]] = []
        self.output = ""
        self.exit_code: int | None = 0
        self.hang: str | None = None
        # taskkill and Popen.kill both fail, and the process lives on.
        self.unkillable = False
        # The process finishes by itself, with this code, just as taskkill
        # looks for it - so taskkill finds nothing to end.
        self.ends_on_its_own: int | None = None

    # subprocess.Popen
    def popen(self, argv, **kw):
        self.launched.append(list(argv))
        return FakeProc(self, list(argv), kw)

    # subprocess.run - taskkill goes here; so did the OLD claude launch.
    def run(self, argv, **kw):
        argv = list(argv) if not isinstance(argv, str) else [argv]
        if "taskkill" in str(argv[0]).lower():
            self.taskkills.append((argv, kw))
            pid = int(argv[argv.index("/PID") + 1])
            targets = [proc for proc in self.procs if proc.pid == pid]
            if self.ends_on_its_own is not None:
                for proc in targets:
                    proc._end(self.ends_on_its_own)
                return subprocess.CompletedProcess(
                    argv, 128, "", f'ERROR: The process "{pid}" not found.')
            if self.unkillable:
                return subprocess.CompletedProcess(argv, 1, "", "ERROR: Access is denied.")
            for proc in targets:
                proc._end(1)
            return subprocess.CompletedProcess(argv, 0, "SUCCESS", "")
        # The pre-fix code launched Claude Code with subprocess.run.
        self.launched.append(argv)
        out = kw.get("stdout")
        if self.output and hasattr(out, "write"):
            out.write(self.output)
        if self.hang:
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        return subprocess.CompletedProcess(argv, self.exit_code or 0)


def wait_for_jobs():
    for thread in threading.enumerate():
        if thread.name.startswith(("claude-job-", "bg-")):
            thread.join(timeout=10)


@pytest.fixture
def table(tmp_path, monkeypatch):
    t = FakeTable()
    monkeypatch.setattr(devwork, "JOBS_PATH", tmp_path / "jobs.json")
    monkeypatch.setattr(devwork, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(devwork, "_git_head", lambda path: "")
    monkeypatch.setattr(devwork.subprocess, "Popen", t.popen)
    monkeypatch.setattr(devwork.subprocess, "run", t.run)
    # Per-test process bookkeeping, so no test's held fakes leak into another.
    # raising=False so the same tests can be run against older versions of
    # the module, which is how the ones below were shown to fail first.
    monkeypatch.setattr(devwork, "_PROCS", {}, raising=False)
    monkeypatch.setattr(devwork, "_CANCELLED", set(), raising=False)
    monkeypatch.setattr(devwork, "_STOPS", {}, raising=False)
    # The brief-quality gate is tested elsewhere; here every brief passes.
    from jalen.tools import handoff
    monkeypatch.setattr(handoff, "brief_or_problem", lambda brief, dest: None)
    yield t
    # Nothing a test started may outlive it.
    for proc in t.procs:
        proc._end(-9)
    wait_for_jobs()


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


@pytest.fixture
def no_conversation_tasks(monkeypatch):
    from jalen import conversation
    monkeypatch.setattr(conversation, "_tasks", {})
    return conversation


def only_a_shim(monkeypatch, tmp_path) -> Path:
    """This machine without the native install: `where claude` is the .CMD."""
    shim = tmp_path / "npm" / "claude.CMD"
    shim.parent.mkdir()
    shim.write_text("@echo off\r\n", encoding="utf-8")
    real_which = coding.shutil.which
    monkeypatch.setattr(coding.shutil, "which",
                        lambda name, *a, **k: str(shim) if name == "claude"
                        else (None if name == "claude.exe" else real_which(name, *a, **k)))
    monkeypatch.setattr(coding, "_NPM_BIN", shim.parent)
    monkeypatch.setattr(coding, "_BUNDLED_CLI", tmp_path / "no-bundled.exe")
    return shim


def a_shim_and_the_bundled_exe(monkeypatch, tmp_path) -> Path:
    """This machine as it really is: the shim on PATH, the SDK's exe bundled."""
    only_a_shim(monkeypatch, tmp_path)
    bundled = tmp_path / "bundled" / "claude.exe"
    bundled.parent.mkdir()
    bundled.write_bytes(b"MZ")
    monkeypatch.setattr(coding, "_BUNDLED_CLI", bundled)
    return bundled


def the_job() -> dict:
    jobs = devwork._load_jobs()
    assert len(jobs) == 1, jobs
    return next(iter(jobs.values()))


def wait_for_state(state: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = list(devwork._load_jobs().values())
        if len(jobs) == 1 and jobs[0].get("state") == state:
            return jobs[0]
        time.sleep(0.01)
    raise AssertionError(f"the job never reached {state!r}: {devwork._load_jobs()}")


def wait_for_proc(table):
    deadline = time.time() + 5
    while not table.procs and time.time() < deadline:
        time.sleep(0.01)
    assert table.procs, "the job never launched"
    return table.procs[0]


def claude_argv(table) -> list[str]:
    launches = [a for a in table.launched if "-p" in a]
    assert launches, f"Claude Code was never launched: {table.launched}"
    return launches[0]


def hold(table, tmp_path, jid: str, folder: str, **extra) -> LiveProc:
    """A job this Jalen started and still holds, running."""
    record = {"id": jid, "prompt": "x", "folder": str(tmp_path / folder),
              "state": "running", "started_at": time.time(), "notified": False}
    record.update(extra)
    devwork._save_jobs({**devwork._load_jobs(), jid: record})
    proc = LiveProc(table)
    devwork._PROCS[jid] = proc
    return proc


# ---------------------------------------------------------------------------
# 1. The brief and the flags arrive intact.
# ---------------------------------------------------------------------------
def test_a_real_executable_is_used_and_gets_the_brief_verbatim(
        table, project, monkeypatch, tmp_path):
    """
    On this machine the SDK-bundled claude.exe exists. It must be chosen over
    the shim that `where claude` returns, and the brief must reach it
    byte-identical.
    """
    bundled = a_shim_and_the_bundled_exe(monkeypatch, tmp_path)
    devwork.start_coding_job(PAYLOAD, str(project))
    wait_for_jobs()

    argv = claude_argv(table)
    assert argv[0] == str(bundled), (
        f"launched {argv[0]!r} - a .CMD runs through cmd.exe, which cuts the "
        "brief at its first newline"
    )
    assert argv[argv.index("-p") + 1] == PAYLOAD


def test_with_only_a_shim_the_brief_arrives_on_stdin(
        table, project, monkeypatch, tmp_path):
    """
    The machine without the native binary. No quoting rescues a newline
    through cmd.exe, so the brief cannot be an argument at all.

    The first fix parked it in %TEMP% and told the agent to read it - but
    the agent is given --add-dir for the project folder only, and headless
    -p has no one to ask for permission to read anywhere else, so the read
    could be declined and the job "finish" having done nothing. Standard
    input reaches the agent without cmd.exe touching it and with no file:
    the installed CLI takes -p input there ("Input must be provided either
    through stdin or as a prompt argument when using --print").
    """
    shim = only_a_shim(monkeypatch, tmp_path)
    parked: list = []
    real_brief = coding._brief_on_disk

    def spy(text):
        path = real_brief(text)
        parked.append(path)
        return path

    monkeypatch.setattr(coding, "_brief_on_disk", spy)
    reply = devwork.start_coding_job(PAYLOAD, str(project))
    wait_for_jobs()

    argv = claude_argv(table)
    assert argv[0] == str(shim)
    add_dirs = [Path(argv[i + 1]) for i, a in enumerate(argv) if a == "--add-dir"]
    for path in parked:
        assert path is None or any(d in Path(path).parents for d in add_dirs), (
            f"the agent was told to read {path}, outside --add-dir "
            f"{[str(d) for d in add_dirs]} - headless, it cannot ask to read "
            "there, and may finish having done nothing"
        )
    proc = table.procs[0]
    assert proc.kw.get("stdin") == subprocess.PIPE, "the brief did not go on stdin"
    assert proc.stdin.getvalue() == PAYLOAD.encode("utf-8"), (
        "the brief on stdin is not the whole brief, byte for byte"
    )
    assert proc.stdin.was_closed, "stdin was left open - the CLI reads to end of input"
    assert all(survives_cmd(a) for a in argv), f"cmd.exe would rewrite part of {argv}"
    assert "shim" in reply.lower(), "the degraded route was not mentioned"


@pytest.mark.parametrize("route", ["bundled", "shim"])
def test_the_permission_flags_survive_on_every_route(
        table, project, monkeypatch, tmp_path, route):
    """
    The flags come AFTER the brief. When cmd.exe cut the brief at its first
    newline it cut them too, so the agent ran with the default permission
    mode - and headless, the default declines every write.

    The first version of this test compared argv as Python holds it, which
    is not what a .CMD receives, so it passed on the code that had the bug.
    This one puts the shim route's argv through what cmd.exe does to it.
    """
    if route == "bundled":
        a_shim_and_the_bundled_exe(monkeypatch, tmp_path)
    else:
        only_a_shim(monkeypatch, tmp_path)
    devwork.start_coding_job(PAYLOAD, str(project))
    wait_for_jobs()

    argv = claude_argv(table)
    flags = ["--permission-mode", "acceptEdits", "--add-dir", str(project)]
    assert argv[-4:] == flags
    if coding._is_batch(argv[0]):
        handed = what_the_shim_is_handed(argv)
        assert subprocess.list2cmdline(flags) in handed, (
            f"cmd.exe hands the shim {handed!r} - the flags were cut off with "
            "the brief"
        )
        rewritten = [a for a in argv if not survives_cmd(a)]
        assert not rewritten, f"cmd.exe would rewrite {rewritten!r}"
    assert "bypass" not in " ".join(argv).lower()


def test_a_folder_cmd_exe_would_mangle_is_refused_on_the_shim_route(
        table, tmp_path, monkeypatch):
    """
    The --add-dir path is still an argument, and an ampersand in an unquoted
    argument is a command separator to cmd.exe. Refuse rather than launch
    something else.
    """
    only_a_shim(monkeypatch, tmp_path)
    folder = tmp_path / "R&D"
    folder.mkdir()
    reply = devwork.start_coding_job(PAYLOAD, str(folder))
    wait_for_jobs()
    assert table.launched == [], "launched through cmd.exe with a hostile path"
    assert "native" in reply.lower()
    assert devwork._load_jobs() == {}, "a job that never started was recorded"


# ---------------------------------------------------------------------------
# 2. A timeout ends the whole tree - and says so when it could not.
# ---------------------------------------------------------------------------
@WINDOWS_ONLY
def test_a_timeout_ends_the_whole_process_tree(table, project, monkeypatch, tmp_path):
    a_shim_and_the_bundled_exe(monkeypatch, tmp_path)
    table.exit_code = None
    table.hang = "timeout"
    devwork.start_coding_job(PAYLOAD, str(project))
    wait_for_jobs()

    assert table.taskkills, (
        "the timeout killed only the direct child - with a .CMD that is "
        "cmd.exe, and claude.exe keeps running"
    )
    argv, kw = table.taskkills[0]
    proc = table.procs[0]
    assert argv[1:] == ["/PID", str(proc.pid), "/T", "/F"]
    assert not kw.get("shell"), "taskkill must be argv, never a shell string"
    assert the_job()["state"] == "timeout"


@WINDOWS_ONLY
def test_a_timeout_whose_kill_fails_stays_stoppable(
        table, project, monkeypatch, tmp_path, no_conversation_tasks):
    """
    The tree-kill on timeout can fail. The first fix ignored that, dropped
    the handle and recorded "timeout" while claude.exe went on editing the
    repository - untracked, and unreachable by cancel_task.
    """
    from jalen.tools import tasks

    a_shim_and_the_bundled_exe(monkeypatch, tmp_path)
    table.exit_code = None
    table.hang = "timeout"
    table.unkillable = True
    devwork.start_coding_job(PAYLOAD, str(project))
    job = wait_for_state("timeout")
    proc = table.procs[0]
    assert proc.poll() is None, "the fake was supposed to survive the kill"

    assert [j["id"] for j in devwork.live_jobs()] == [job["id"]], (
        "the handle was dropped while the process was still running - nothing "
        "can stop it now"
    )
    assert str(proc.pid) in json.dumps(job), (
        "the record does not say which process is still alive"
    )

    table.unkillable = False
    reply = tasks.cancel_task("stop the coding job")
    assert reply.startswith("Stopped"), reply
    wait_for_jobs()
    assert proc.poll() is not None
    assert devwork.live_jobs() == []


# ---------------------------------------------------------------------------
# 3. A failure is announced as a failure.
# ---------------------------------------------------------------------------
def test_a_non_zero_exit_is_recorded_as_failed_with_what_it_said(
        table, project, monkeypatch, tmp_path):
    a_shim_and_the_bundled_exe(monkeypatch, tmp_path)
    table.output = "Starting up\nSomething earlier\nError: Invalid API key. Please run /login\n"
    table.exit_code = 1
    devwork.start_coding_job(PAYLOAD, str(project))
    wait_for_jobs()

    job = the_job()
    assert job["state"] == "failed", (
        "exit code 1 was recorded as finished, so Jalen announced a crash as "
        "'just finished, say review the coding job'"
    )
    assert job.get("exit_code") == 1
    assert "1" in job["error"]
    assert "Invalid API key" in job["error"], "the reason it failed was dropped"
    assert "\n" not in job["error"], "the error is spoken - one line"
    assert len(job["error"]) < 400, "the error is spoken - keep it short"
    assert [j["id"] for j in devwork.finished_unreported_jobs()] == [job["id"]]


def test_exit_zero_is_still_finished(table, project, monkeypatch, tmp_path):
    a_shim_and_the_bundled_exe(monkeypatch, tmp_path)
    table.output = "All done.\n"
    table.exit_code = 0
    devwork.start_coding_job(PAYLOAD, str(project))
    wait_for_jobs()
    job = the_job()
    assert job["state"] == "finished"
    assert not job.get("error")


# ---------------------------------------------------------------------------
# 4. "Stopped" means stopped.
# ---------------------------------------------------------------------------
@WINDOWS_ONLY
def test_cancelling_a_running_coding_job_really_stops_it(
        table, project, monkeypatch, tmp_path, no_conversation_tasks):
    from jalen.tools import tasks

    a_shim_and_the_bundled_exe(monkeypatch, tmp_path)
    table.exit_code = None
    table.hang = "until-killed"
    devwork.start_coding_job(PAYLOAD, str(project))
    proc = wait_for_proc(table)

    reply = tasks.cancel_task("stop the coding job")
    wait_for_jobs()

    assert table.taskkills, "nothing was killed, whatever the reply said"
    assert table.taskkills[0][0][1:] == ["/PID", str(proc.pid), "/T", "/F"]
    assert proc.poll() is not None
    assert reply.startswith("Stopped"), reply
    assert "proj" in reply
    job = the_job()
    assert job["state"] == "cancelled"
    assert devwork.finished_unreported_jobs() == [], (
        "a job he cancelled himself was announced as a failure"
    )


def test_cancelling_a_turn_does_not_claim_it_stopped(table, no_conversation_tasks):
    """
    A conversation task is a turn running in its own thread. Marking it
    CANCELLED changes nothing that reads it, so the reply must not say
    "Stopped".
    """
    from jalen.tools import tasks

    no_conversation_tasks.start_task("sorting your machine learning emails",
                                     no_conversation_tasks.EXECUTING)
    reply = tasks.cancel_task("cancel that")
    assert not reply.lower().startswith("stopped"), reply
    assert "marked" in reply.lower()
    assert "can't interrupt" in reply.lower() or "cannot interrupt" in reply.lower()


def test_a_coding_job_from_before_a_restart_is_not_claimed_stopped(
        table, no_conversation_tasks, tmp_path):
    """
    The job store survives a restart; the process handle does not. A job
    recorded as running that this process never started cannot be stopped
    from here, and the reply has to say so.
    """
    from jalen.tools import tasks

    devwork._save_jobs({"old1": {
        "id": "old1", "prompt": "x", "folder": str(tmp_path / "legacy"),
        "state": "running", "started_at": time.time() - 600, "notified": False,
    }})
    reply = tasks.cancel_task("stop the coding job")
    assert not reply.lower().startswith("stopped"), reply
    assert "legacy" in reply
    assert table.taskkills == []
    assert the_job()["state"] == "running", "a job that is still running was relabelled"


def test_nothing_running_is_said_plainly(table, no_conversation_tasks):
    from jalen.tools import tasks

    reply = tasks.cancel_task("")
    assert "not working on anything" in reply.lower()
    assert table.taskkills == []


@WINDOWS_ONLY
def test_two_live_coding_jobs_are_not_guessed_between(
        table, tmp_path, no_conversation_tasks):
    from jalen.tools import tasks

    alpha = hold(table, tmp_path, "aaaa1111", "alpha")
    beta = hold(table, tmp_path, "bbbb2222", "beta")

    reply = tasks.cancel_task("stop the coding job")
    assert "which" in reply.lower(), reply
    assert table.taskkills == []

    reply = tasks.cancel_task("stop the job in beta")
    assert reply.startswith("Stopped"), reply
    assert "beta" in reply
    assert beta.poll() is not None
    assert alpha.poll() is None, "the other job was ended too"


@WINDOWS_ONLY
def test_a_folder_named_like_a_job_word_does_not_decide_between_two_jobs(
        table, tmp_path, no_conversation_tasks):
    """'stop the coding job' must not pick the job whose folder is 'coding'."""
    from jalen.tools import tasks

    coding_folder = hold(table, tmp_path, "aaaa1111", "coding")
    beta = hold(table, tmp_path, "bbbb2222", "beta")
    reply = tasks.cancel_task("stop the coding job")
    assert "which" in reply.lower(), reply
    assert coding_folder.poll() is None and beta.poll() is None


# ---------------------------------------------------------------------------
# 5. Only a sentence that unambiguously means a job may end one.
# ---------------------------------------------------------------------------
ORDINARY_SENTENCES = [
    ("app", "cancel my dentist appointment"),
    ("web", "stop working on the website copy"),
    ("api", "cancel the rapid reply to Sam"),
    ("proj", "cancel the job application email"),
    ("proj", "stop the agent from emailing my landlord"),
    ("proj", "stop claude from sending that"),
    ("proj", "cancel that"),
    ("proj", ""),
]


@WINDOWS_ONLY
@pytest.mark.parametrize("folder,said", ORDINARY_SENTENCES)
def test_an_ordinary_sentence_never_ends_a_job(
        table, tmp_path, no_conversation_tasks, folder, said):
    """
    The first fix matched the folder name as a raw substring ("app" is in
    "appointment") and treated bare job/agent/claude as naming the job - so
    with one job live, "cancel the job application email" ended it. Killing
    loses up to an hour of an agent's work and cannot be undone.
    """
    from jalen.tools import tasks

    proc = hold(table, tmp_path, "c0ffee11", folder)
    reply = tasks.cancel_task(said)
    assert proc.poll() is None, f"{said!r} ended the job in {folder!r}: {reply}"
    assert table.taskkills == []
    assert not reply.lower().startswith("stopped"), reply


VAGUE = ["stop the job", "stop the agent", "stop claude code", "cancel the jobs",
         "stop the coding agent"]


@WINDOWS_ONLY
@pytest.mark.parametrize("said", VAGUE)
def test_a_vague_job_word_asks_rather_than_kills(
        table, tmp_path, no_conversation_tasks, said):
    from jalen.tools import tasks

    proc = hold(table, tmp_path, "c0ffee11", "proj")
    reply = tasks.cancel_task(said)
    assert proc.poll() is None, f"{said!r} ended the job on a guess: {reply}"
    assert "?" in reply and "proj" in reply, (
        f"{said!r} neither ended the job nor asked about it: {reply}"
    )


@WINDOWS_ONLY
@pytest.mark.parametrize("said", ["don't stop the coding job",
                                  "do not cancel the background job"])
def test_being_told_not_to_stop_it_does_not_stop_it(
        table, tmp_path, no_conversation_tasks, said):
    from jalen.tools import tasks

    proc = hold(table, tmp_path, "c0ffee11", "proj")
    reply = tasks.cancel_task(said)
    assert proc.poll() is None, reply
    assert not reply.lower().startswith("stopped"), reply


EXPLICIT = [
    ("proj", "stop the coding job"),
    ("proj", "cancel the background job"),
    ("proj", "kill the claude code job"),
    ("proj", "stop the background run"),
    ("proj", "cancel job c0ffee11"),
    ("web", "stop the job in web"),
    ("My Project", "stop the job in my project"),
]


@WINDOWS_ONLY
@pytest.mark.parametrize("folder,said", EXPLICIT)
def test_saying_which_job_ends_it(table, tmp_path, no_conversation_tasks, folder, said):
    from jalen.tools import tasks

    proc = hold(table, tmp_path, "c0ffee11", folder)
    reply = tasks.cancel_task(said)
    assert proc.poll() is not None, f"{said!r} did not end the job: {reply}"
    assert reply.startswith("Stopped"), reply


@WINDOWS_ONLY
def test_a_turn_named_with_a_job_word_is_the_one_marked(
        table, tmp_path, no_conversation_tasks):
    from jalen.tools import tasks

    proc = hold(table, tmp_path, "c0ffee11", "proj")
    conv = no_conversation_tasks
    conv.start_task("drafting the job application email", conv.EXECUTING)
    reply = tasks.cancel_task("cancel the job application email")
    assert proc.poll() is None, reply
    assert "job application email" in reply and "marked" in reply.lower(), reply


# ---------------------------------------------------------------------------
# 6. The job store keeps every ending.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("meanwhile", ["an ending", "an announcement"])
@pytest.mark.parametrize("starting", ["coding job", "background command"])
def test_starting_a_job_does_not_undo_what_another_thread_just_recorded(
        table, project, monkeypatch, tmp_path, starting, meanwhile):
    """
    Creating a job was load, insert, save - unlocked. An ending recorded in
    between (a job's own thread) was overwritten back to "running", so the
    job was never announced and later read as an orphan; an announcement
    recorded in between had its notified flag reset, so it was announced
    twice. The interleave is forced here, not hoped for.
    """
    a_shim_and_the_bundled_exe(monkeypatch, tmp_path)
    before = "running" if meanwhile == "an ending" else "finished"
    devwork._save_jobs({"old1": {
        "id": "old1", "prompt": "x", "folder": str(project), "state": before,
        "started_at": time.time() - 60, "notified": False,
    }})
    announced: list = []

    def record():
        if meanwhile == "an ending":
            devwork._update("old1", state="finished", exit_code=0, ended_at=time.time())
        else:
            announced.extend(j["id"] for j in devwork.finished_unreported_jobs())

    real_load = devwork._load_jobs
    main = threading.current_thread()
    fired: list = []

    def load_then_race():
        snapshot = real_load()
        if threading.current_thread() is main and not fired:
            fired.append(True)
            other = threading.Thread(target=record, name="bg-meanwhile")
            other.start()
            # A store that locks its read-modify-write makes this wait out.
            other.join(timeout=0.5)
        return snapshot

    monkeypatch.setattr(devwork, "_load_jobs", load_then_race)
    if starting == "coding job":
        devwork.start_coding_job(PAYLOAD, str(project))
    else:
        devwork.start_background_run(["python", "-V"], project, "my own test suite")
    wait_for_jobs()
    monkeypatch.setattr(devwork, "_load_jobs", real_load)

    assert fired, "the interleave never happened, so this proves nothing"
    jobs = devwork._load_jobs()
    assert len(jobs) == 2, jobs
    if meanwhile == "an ending":
        assert jobs["old1"]["state"] == "finished", (
            "the ending was overwritten back to 'running' by the new job's save"
        )
    else:
        assert announced == ["old1"]
        again = [j["id"] for j in devwork.finished_unreported_jobs()]
        assert "old1" not in again, "the same job was announced twice"


@WINDOWS_ONLY
def test_a_reader_holding_the_store_open_does_not_drop_an_ending(
        table, tmp_path, monkeypatch):
    """
    Measured on this machine: os.replace onto a file that another handle has
    open fails with PermissionError (winerror 5), and _save_jobs swallowed
    it. So an UNLOCKED reader - list_coding_jobs, live_jobs, cancel_task's
    lookups - caught mid-read was enough to make a job's ending vanish.
    """
    gate = {"reading": threading.Event(), "release": threading.Event()}

    class SlowToRead(type(Path())):
        def read_text(self, *args, **kwargs):
            if threading.current_thread().name == "slow-reader":
                with open(self, encoding="utf-8") as fh:
                    gate["reading"].set()
                    gate["release"].wait(timeout=5)
                    return fh.read()
            return super().read_text(*args, **kwargs)

    monkeypatch.setattr(devwork, "JOBS_PATH", SlowToRead(tmp_path / "jobs.json"))
    devwork._save_jobs({"old1": {
        "id": "old1", "prompt": "x", "folder": str(tmp_path), "state": "running",
        "started_at": time.time(), "notified": False,
    }})
    reader = threading.Thread(target=devwork.list_coding_jobs, name="slow-reader")
    reader.start()
    assert gate["reading"].wait(timeout=5)
    writer = threading.Thread(
        target=lambda: devwork._update("old1", state="finished", ended_at=time.time()),
        name="bg-writer")
    writer.start()
    writer.join(timeout=0.3)
    gate["release"].set()
    reader.join(timeout=5)
    writer.join(timeout=5)
    assert devwork._load_jobs()["old1"]["state"] == "finished", (
        "the ending was dropped because a reader had the file open"
    )


@WINDOWS_ONLY
def test_a_save_that_fails_says_so_and_leaves_no_litter(table, tmp_path):
    devwork._save_jobs({"old1": {"id": "old1", "state": "running"}})
    with open(devwork.JOBS_PATH, encoding="utf-8"):   # another program reading it
        saved = devwork._save_jobs({"new1": {"id": "new1", "state": "running"}})
    assert saved is False, "a failed save reported nothing"
    assert list(tmp_path.glob("*.tmp")) == [], "a failed save left its temp file behind"
    assert "old1" in devwork._load_jobs()


@pytest.mark.parametrize("starting", ["coding job", "background command"])
def test_a_job_that_cannot_be_recorded_is_not_started(
        table, project, monkeypatch, tmp_path, starting):
    """A job nothing can track is a job nothing will ever announce."""
    a_shim_and_the_bundled_exe(monkeypatch, tmp_path)
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(devwork, "JOBS_PATH", blocker / "jobs.json")
    if starting == "coding job":
        reply = devwork.start_coding_job(PAYLOAD, str(project))
    else:
        reply = devwork.start_background_run(["python", "-V"], project, "my own test suite")
    wait_for_jobs()
    assert table.launched == [], "started a job whose record could not be written"
    assert "couldn't" in reply.lower(), reply


# ---------------------------------------------------------------------------
# 7. "Stopped" only about a job the kill actually ended.
# ---------------------------------------------------------------------------
@WINDOWS_ONLY
@pytest.mark.parametrize("when", ["as taskkill looks", "before the kill starts"])
def test_a_job_that_finishes_on_its_own_as_he_says_stop_is_not_claimed_stopped(
        table, project, monkeypatch, tmp_path, no_conversation_tasks, when):
    a_shim_and_the_bundled_exe(monkeypatch, tmp_path)
    table.exit_code = None
    table.hang = "until-killed"
    devwork.start_coding_job(PAYLOAD, str(project))
    wait_for_proc(table)
    job_id = the_job()["id"]

    if when == "as taskkill looks":
        table.ends_on_its_own = 0
    else:
        real_kill_tree = devwork._kill_tree

        def ends_first(proc):
            proc._end(0)
            return real_kill_tree(proc)

        monkeypatch.setattr(devwork, "_kill_tree", ends_first)

    reply = devwork.stop_coding_job(job_id)
    wait_for_jobs()
    assert not reply.startswith("Stopped"), reply
    job = the_job()
    assert job["state"] == "finished", (
        f"its real ending was overwritten with {job['state']!r}"
    )
    assert [j["id"] for j in devwork.finished_unreported_jobs()] == [job_id], (
        "he was never told how it actually ended"
    )


# ---------------------------------------------------------------------------
# 8. A background command is called what it is.
# ---------------------------------------------------------------------------
@WINDOWS_ONLY
def test_a_background_command_is_named_as_itself_when_asking_which(
        table, tmp_path, no_conversation_tasks):
    from jalen.tools import tasks

    hold(table, tmp_path, "aaaa1111", "alpha")
    hold(table, tmp_path, "bbbb2222", "jarvis", kind="command", prompt="my own test suite")
    reply = tasks.cancel_task("stop the background job")
    assert "which" in reply.lower(), reply
    assert "my own test suite" in reply, reply
    assert "coding jobs" not in reply, reply


@WINDOWS_ONLY
def test_the_hint_for_a_running_command_does_not_call_it_a_coding_job(
        table, tmp_path, no_conversation_tasks):
    from jalen.tools import tasks

    proc = hold(table, tmp_path, "bbbb2222", "jarvis", kind="command",
                prompt="my own test suite")
    reply = tasks.cancel_task("cancel that")
    assert proc.poll() is None
    assert "my own test suite" in reply, reply
    assert "coding job" not in reply, reply


@WINDOWS_ONLY
def test_stopping_a_command_does_not_say_claude_code(table, tmp_path):
    hold(table, tmp_path, "bbbb2222", "jarvis", kind="command", prompt="my own test suite")
    reply = devwork.stop_coding_job("bbbb2222")
    assert reply.startswith("Stopped my own test suite"), reply
    assert "Claude Code" not in reply, reply


def test_the_announcer_names_a_command_as_itself_and_says_what_is_still_alive(
        monkeypatch):
    from jalen.app import Jalen

    said: list[str] = []
    app = Jalen.__new__(Jalen)
    app.muted = False
    app.paused = False
    app.speaker = SimpleNamespace(speaking=False)
    app._active_turns = set()
    app._awaiting_confirmation = False
    app._awaiting_stop = False
    app._awaiting_reply = False
    app.audit = SimpleNamespace(write=lambda *a, **k: None)
    app.say = lambda text, **k: said.append(text)
    now = time.time()
    monkeypatch.setattr(devwork, "finished_unreported_jobs", lambda: [
        {"id": "bbbb2222", "kind": "command", "prompt": "my own test suite",
         "folder": "C:/x/jarvis", "state": "timeout", "started_at": now - 3600,
         "ended_at": now, "alive_pid": 4242},
        {"id": "aaaa1111", "prompt": "x", "folder": "C:/x/proj", "state": "failed",
         "started_at": now - 60, "ended_at": now, "error": "it broke"},
    ])
    app._announce_finished_jobs()
    assert said[0].startswith("My own test suite ran out of time"), said
    assert "coding job" not in said[0], said
    assert "4242" in said[0], "the process that is still running was not named"
    assert said[1].startswith("The coding job in proj failed"), said
