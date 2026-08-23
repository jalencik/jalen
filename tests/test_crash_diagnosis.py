"""
The 21 August silent exit — HANDOFF item 2.

A voice-mode instance started, wrote four prewarm lines, and vanished. The
audit log had no "Jalen stopped" for it, and there was nowhere else to look.
It was never root-caused, and these tests do not claim to root-cause it. What
they assert is that the same disappearance CANNOT happen silently again: the
three holes that swallowed the evidence are each closed and each covered here.

  1. a traceback under pythonw (sys.stderr is None) reaches disk
  2. the audit line survives a teardown step that raises
  3. the frame loop records WHY it ended, and a process that never got to
     shut down is reported at the next start

Each test names the hole it stands over. If one of these fails, the failure
mode is not "an assertion changed" — it is that Jalen can vanish quietly
again.
"""
from __future__ import annotations

import json
import sys

import pytest

from jarvis import crashlog


@pytest.fixture()
def crash_dir(tmp_path, monkeypatch):
    """Point the crash log and exit-state file at a temp directory."""
    monkeypatch.setattr(crashlog, "DATA_DIR", tmp_path)
    monkeypatch.setattr(crashlog, "CRASH_LOG", tmp_path / "crash.log")
    monkeypatch.setattr(crashlog, "EXIT_STATE", tmp_path / "last_exit.json")
    return tmp_path


# --------------------------------------------------------------------------
# Hole 1: under pythonw there is no stderr, so tracebacks go nowhere.
# --------------------------------------------------------------------------
def test_a_traceback_reaches_disk(crash_dir):
    try:
        raise ValueError("the thing that killed it")
    except ValueError as exc:
        crashlog.record_exception("main thread", exc)

    body = (crash_dir / "crash.log").read_text(encoding="utf-8")
    assert "ValueError: the thing that killed it" in body
    assert "UNHANDLED in main thread" in body
    # The traceback itself, not just the message — the line number is the
    # part that makes a crash diagnosable.
    assert "test_a_traceback_reaches_disk" in body


def test_stderr_stand_in_captures_what_would_have_been_lost(crash_dir):
    """
    The interpreter's own default excepthook writes to sys.stderr directly.
    When that is None (pythonw), the output is discarded before any hook of
    ours could see it, so the stand-in stream has to behave like a file.
    """
    stream = crashlog._StderrToCrashLog()
    stream.write("Traceback (most recent call last):\n")
    stream.write("  File \"x.py\", line 1\n")
    stream.write("RuntimeError: boom\n")
    stream.flush()

    body = (crash_dir / "crash.log").read_text(encoding="utf-8")
    assert "RuntimeError: boom" in body
    # Things that probe a stream must get answers, not AttributeError, in the
    # middle of reporting a crash.
    assert stream.isatty() is False
    assert stream.closed is False


def test_install_replaces_a_missing_stderr(crash_dir, monkeypatch):
    monkeypatch.setattr(crashlog, "_installed", False)
    monkeypatch.setattr(crashlog, "_sinks", [])
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(sys, "stdout", None)
    old_excepthook, old_threadhook = sys.excepthook, sys.threading_excepthook if hasattr(sys, "threading_excepthook") else None  # noqa: F841
    try:
        crashlog.install()
        assert sys.stderr is not None
        assert sys.stdout is not None
        assert isinstance(sys.stderr, crashlog._StderrToCrashLog)
    finally:
        sys.excepthook = old_excepthook
        crashlog._installed = False


def test_a_thread_crash_is_recorded_too(crash_dir, monkeypatch):
    """
    Every turn runs on its own thread (app.py's dispatch_turn). The default
    threading.excepthook prints to stderr, which under pythonw is nowhere.
    """
    monkeypatch.setattr(crashlog, "_installed", False)
    monkeypatch.setattr(crashlog, "_sinks", [])
    seen: list[tuple[str, str]] = []
    try:
        crashlog.install(on_crash=lambda where, exc: seen.append((where, str(exc))))

        class Args:
            exc_type = RuntimeError
            exc_value = RuntimeError("turn blew up")
            exc_traceback = None
            thread = type("T", (), {"name": "jalen-turn-7"})()

        import threading as _threading
        _threading.excepthook(Args())
    finally:
        crashlog._installed = False

    body = (crash_dir / "crash.log").read_text(encoding="utf-8")
    assert "turn blew up" in body
    assert "jalen-turn-7" in body
    # The second sink (the audit log, in the real app) is called too.
    assert seen and "turn blew up" in seen[0][1]


def test_a_failing_audit_sink_does_not_lose_the_crash(crash_dir, monkeypatch):
    """The disk record must not depend on the audit log working."""
    monkeypatch.setattr(crashlog, "_installed", False)
    monkeypatch.setattr(crashlog, "_sinks", [])

    def broken(where, exc):
        raise OSError("audit database is gone")

    try:
        crashlog.install(on_crash=broken)

        class Args:
            exc_type = RuntimeError
            exc_value = RuntimeError("still recorded")
            exc_traceback = None
            thread = type("T", (), {"name": "worker"})()

        import threading as _threading
        _threading.excepthook(Args())
    finally:
        crashlog._installed = False

    assert "still recorded" in (crash_dir / "crash.log").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Hole 3: nothing recorded WHY the process ended.
# --------------------------------------------------------------------------
def test_a_run_that_never_shuts_down_is_visible_at_the_next_start(crash_dir):
    """
    This is the 21 August shape exactly: mark_running(), then the process
    disappears without note_exit() ever being called.
    """
    crashlog.mark_running("e619403944e3", "voice")

    record = crashlog.previous_exit()
    assert record["state"] == "running"

    note = crashlog.describe_previous_exit(record)
    assert note is not None
    assert "WITHOUT shutting down" in note
    assert "e619403944e3" in note
    assert "voice" in note
    assert "crash.log" in note  # tells you where to look next


def test_a_clean_stop_says_nothing_at_the_next_start(crash_dir):
    """
    A line on every startup is noise, and noise is what stops anyone reading
    the file. Only the unexplained exit earns a line.
    """
    crashlog.mark_running("abc123", "voice")
    crashlog.note_exit("quit-requested")

    record = crashlog.previous_exit()
    assert record["state"] == "stopped"
    assert record["reason"] == "quit-requested"
    assert crashlog.describe_previous_exit(record) is None


def test_the_first_real_reason_wins(crash_dir):
    """
    note_exit() is called from several places on the way down. The specific
    cause must not be overwritten by the generic "shutdown" that follows it,
    or the log ends up saying only that the process stopped — which was
    already obvious.
    """
    crashlog.mark_running("abc123", "voice")
    crashlog.note_exit("unhandled-exception", error="ZeroDivisionError: x")
    crashlog.note_exit("shutdown")

    record = crashlog.previous_exit()
    assert record["reason"] == "unhandled-exception"
    assert record["error"] == "ZeroDivisionError: x"


def test_uptime_is_recorded(crash_dir):
    crashlog.mark_running("abc123", "voice")
    crashlog.note_exit("stop-file")
    assert "uptime_s" in crashlog.previous_exit()


def test_a_corrupt_exit_file_is_not_a_crash(crash_dir):
    """
    The interesting failure is a process dying mid-write. A half-written file
    must read as "no record", never take the next start down with it.
    """
    (crash_dir / "last_exit.json").write_text('{"state": "runn', encoding="utf-8")
    assert crashlog.previous_exit() is None
    assert crashlog.describe_previous_exit(None) is None


def test_the_exit_file_is_written_atomically(crash_dir):
    """
    Temp-file-and-replace, so a kill mid-write leaves the OLD record rather
    than a corrupt one. Checked by proving no stray .tmp survives a write.
    """
    crashlog.mark_running("abc123", "voice")
    crashlog.note_exit("stop-file")
    assert not list(crash_dir.glob("*.tmp"))
    assert json.loads((crash_dir / "last_exit.json").read_text(encoding="utf-8"))


def test_nothing_in_crashlog_raises_when_the_directory_is_unwritable(crash_dir, monkeypatch):
    """
    A diagnostics module that crashes the program it is diagnosing is worse
    than no diagnostics. Every entry point swallows its own errors.
    """
    monkeypatch.setattr(crashlog, "DATA_DIR", crash_dir / "nope" / "deeper")
    monkeypatch.setattr(crashlog, "CRASH_LOG", crash_dir / "nope" / "deeper" / "c.log")

    def explode(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(crashlog.Path, "mkdir", explode)
    crashlog.write("this must not raise")
    crashlog.note_exit("also must not raise")
    try:
        raise ValueError("nor this")
    except ValueError as exc:
        crashlog.record_exception("somewhere", exc)


# --------------------------------------------------------------------------
# Hole 2: the audit line was written after fallible teardown steps.
# --------------------------------------------------------------------------
def test_shutdown_records_the_stop_even_when_teardown_explodes(crash_dir, monkeypatch):
    """
    THE hole. shutdown() used to call speaker.stop(), orb.stop() and
    mic.stop() and only then write "Jalen stopped" — so one raising step took
    the only record of the exit down with it, and a process that had clearly
    stopped left a log saying nothing had happened.
    """
    from jarvis.app import Jalen

    written: list[str] = []

    jalen = Jalen.__new__(Jalen)          # no mic, no orb, no network
    jalen.session_id = "deadbeef"
    jalen.mode = "voice"
    jalen._exit_reason = "stop-file"
    jalen.brain = None
    jalen._loop = None
    jalen.running = __import__("threading").Event()
    jalen.running.set()

    class Audit:
        def write(self, kind, **kw):
            written.append(kw.get("summary", ""))

        def error(self, where, exc):
            written.append(f"error {where}: {exc}")

        def close(self):
            pass

    class Exploding:
        def stop(self):
            raise RuntimeError("the audio device went away")

    class Fine:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    jalen.audit = Audit()
    jalen.speaker = Exploding()
    jalen.hands = Fine()
    jalen.orb = Fine()
    jalen.mic = Fine()
    jalen._stop_loop = lambda: None

    jalen.shutdown()

    # The record exists, and names the real reason rather than "shutdown".
    assert any("Jalen stopped (stop-file)" in line for line in written)
    # The failure is reported, not swallowed.
    assert any("the audio device went away" in line for line in written)
    # And the steps AFTER the exploding one still ran: a broken speaker must
    # not cost you the microphone release.
    assert jalen.orb.stopped and jalen.mic.stopped
    # On disk too, for the case where the audit log is what is broken.
    assert crashlog.previous_exit()["reason"] == "stop-file"


def test_shutdown_prefers_an_explicit_reason_over_the_recorded_one(crash_dir):
    from jarvis.app import Jalen

    written: list[str] = []
    jalen = Jalen.__new__(Jalen)
    jalen.session_id = "deadbeef"
    jalen.mode = "text"
    jalen._exit_reason = None
    jalen.brain = None
    jalen._loop = None
    jalen.running = __import__("threading").Event()

    class Audit:
        def write(self, kind, **kw):
            written.append(kw.get("summary", ""))

        def error(self, where, exc):
            pass

        def close(self):
            pass

    class Fine:
        def stop(self):
            pass

    jalen.audit = Audit()
    jalen.speaker, jalen.hands, jalen.orb, jalen.mic = Fine(), Fine(), Fine(), Fine()
    jalen._stop_loop = lambda: None
    jalen.shutdown("text-mode-ended")
    assert any("Jalen stopped (text-mode-ended)" in line for line in written)


# --------------------------------------------------------------------------
# The microphone going quiet without erroring.
# --------------------------------------------------------------------------
def test_a_microphone_that_stops_delivering_is_written_down(crash_dir, monkeypatch):
    """
    PortAudio does not raise when a capture device stops producing. Before
    this, a deaf Jalen and a listening Jalen left identical evidence: none.
    """
    from jarvis.audio.mic import Microphone
    from jarvis.config import CONFIG

    mic = Microphone(CONFIG)
    mic.last_callback_at = __import__("time").monotonic() - 60.0
    mic._report_stall()

    body = (crash_dir / "crash.log").read_text(encoding="utf-8")
    assert "delivered no audio" in body
    assert "deaf but still running" in body

    # Once per stall, not once per second.
    before = len(body)
    mic._report_stall()
    assert len((crash_dir / "crash.log").read_text(encoding="utf-8")) == before


def test_portaudio_status_flags_are_kept(crash_dir):
    """
    `status` was ignored entirely. It is PortAudio's only channel for
    "input overflowed" or "device error", and a microphone degrading under
    load used to look identical to one working perfectly.
    """
    import numpy as np

    from jarvis.audio.mic import Microphone
    from jarvis.config import CONFIG

    mic = Microphone(CONFIG)
    data = np.zeros((mic.blocksize, 1), dtype="float32")
    mic._callback(data, mic.blocksize, None, "input overflow")
    mic._callback(data, mic.blocksize, None, "input overflow")   # deduplicated
    mic._callback(data, mic.blocksize, None, "")                 # falsy: ignored

    assert mic.status_flags == ["input overflow"]
    assert mic.last_callback_at > 0


def test_status_flags_cannot_grow_without_bound(crash_dir):
    import numpy as np

    from jarvis.audio.mic import Microphone
    from jarvis.config import CONFIG

    mic = Microphone(CONFIG)
    data = np.zeros((mic.blocksize, 1), dtype="float32")
    for i in range(500):
        mic._callback(data, mic.blocksize, None, f"error {i}")
    assert len(mic.status_flags) <= 50
