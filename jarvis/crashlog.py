"""
Why did Jalen stop? Somewhere on disk, always.

THE BUG THIS EXISTS FOR
-----------------------
On 21 August a voice-mode instance (session e6194039) started at 15:47:09,
wrote its four prewarm lines, and was gone by 15:47:24. `data/audit.jsonl`
has no "Jalen stopped" for it — it is the only session that day without
one. Nothing was diagnosable, because three separate holes all swallow the
same evidence:

  1. AUTOSTART HAS NO STDERR. start_jalen.vbs runs pythonw.exe with window
     mode 0. Under pythonw `sys.stderr` is None, so a traceback is not
     "hidden in a window nobody looked at" — it is discarded by the
     interpreter before it reaches anything. Every crash is invisible by
     construction.

  2. THE AUDIT LINE CAME LAST. Jalen.shutdown() wrote "Jalen stopped" only
     after speaker.stop(), orb.stop() and mic.stop(). Any one of those
     raising loses the one record that says the process went down at all.

  3. THE LOOP EXIT HAD NO REASON. run()'s frame loop can end by break, by
     the mic generator terminating, or by an exception, and all three left
     the identical (empty) trace.

WHAT THIS MODULE DOES ABOUT IT
------------------------------
Nothing clever. Two files and a hook:

  data/crash.log       every unhandled exception, from any thread, plus
                       anything written to a stderr that would otherwise
                       have gone nowhere. Append-only, plain text, capped.
  data/last_exit.json  a single record of the CURRENT run, rewritten as it
                       goes: "running" at start, "stopped" with a reason at
                       exit. It is the state machine that makes a vanished
                       process detectable at all.

The next start reads last_exit.json before overwriting it. If it still says
"running", the previous run never got to say goodbye — it was killed, or it
died somewhere that could not report. That fact goes into the audit log as
a real line with the previous pid, session and uptime, which is exactly the
line that was missing on 21 August.

DESIGN NOTES

* Nothing here may raise. A diagnostics module that crashes the program it
  is diagnosing is worse than no diagnostics at all, so every public
  function swallows its own errors. The cost of a lost crash line is one
  lost crash line.

* State is a file, not a signal handler or an atexit hook. atexit does not
  run on TerminateProcess (which is exactly how runtime.stop_running_instance
  force-stops a non-cooperative instance) and a signal handler does not run
  when the interpreter dies inside a C extension. A file that is already on
  disk survives both.

* last_exit.json is written with a temp-file-and-replace, because the
  interesting failure is "the process died mid-write" and a half-written
  JSON file would read as corrupt rather than as "running".
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CRASH_LOG = DATA_DIR / "crash.log"
EXIT_STATE = DATA_DIR / "last_exit.json"

# Keep the crash log from growing without bound on a machine that is left
# running for weeks. Rotated by truncation to the newest half rather than by
# numbered backups: the recent crash is the one being diagnosed, and a
# second file is a second thing to remember to look at.
CRASH_LOG_MAX_BYTES = 512 * 1024

_installed = False
_lock = threading.Lock()
# Extra sinks, beyond the crash log itself. run.py installs the hooks before
# anything else can fail, and Jalen.__init__ adds the audit log once it
# exists — so this is a LIST rather than a single callback. A plain
# "already installed, do nothing" guard would silently discard the second
# registration, and the audit sink is the one that puts a crash in the same
# file as everything else Jalen did that session.
_sinks: list[Callable[[str, BaseException], None]] = []


# ---------------------------------------------------------------- crash log
def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rotate_if_large() -> None:
    try:
        if CRASH_LOG.exists() and CRASH_LOG.stat().st_size > CRASH_LOG_MAX_BYTES:
            tail = CRASH_LOG.read_text(encoding="utf-8", errors="replace")
            keep = tail[len(tail) // 2 :]
            CRASH_LOG.write_text(
                f"[{_timestamp()}] --- log truncated, older half dropped ---\n{keep}",
                encoding="utf-8",
            )
    except OSError:
        pass


def write(message: str) -> None:
    """One line (or block) into data/crash.log. Never raises."""
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _rotate_if_large()
        with _lock, open(CRASH_LOG, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"[{_timestamp()}] pid={os.getpid()} {message}\n")
    except OSError:
        pass


def record_exception(where: str, exc: BaseException, *, thread: str = "") -> None:
    """A full traceback into the crash log, tagged with where it came from."""
    try:
        body = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        ).rstrip()
    except Exception:  # pragma: no cover - formatting a traceback should not fail
        body = f"{type(exc).__name__}: {exc}"
    where = f"{where} thread={thread}" if thread else where
    write(f"UNHANDLED in {where}\n{body}")


# ------------------------------------------------------------ stderr rescue
class _StderrToCrashLog:
    """
    A minimal write-only stream standing in for a stderr that does not exist.

    Under pythonw, sys.stderr is None and the interpreter's default
    excepthook has nowhere to print. Replacing it with this means that even
    code that bypasses our hooks entirely — a C extension writing to stderr,
    a library's own `traceback.print_exc()` — lands in the crash log instead
    of being dropped on the floor.

    Buffered by line rather than per-write, because a traceback arrives as
    dozens of tiny writes and one crash-log entry per line would interleave
    unreadably with anything else writing at the same moment.
    """

    def __init__(self) -> None:
        self._buf: list[str] = []

    def write(self, text: str) -> int:
        if not text:
            return 0
        self._buf.append(text)
        if "\n" in text:
            self.flush()
        return len(text)

    def flush(self) -> None:
        if not self._buf:
            return
        blob = "".join(self._buf).rstrip()
        self._buf.clear()
        if blob:
            write(f"stderr: {blob}")

    def isatty(self) -> bool:
        return False

    # Anything that probes for a real file gets an honest, harmless answer
    # rather than an AttributeError in the middle of reporting a crash.
    def fileno(self) -> int:
        raise OSError("crash-log stderr has no file descriptor")

    def close(self) -> None:
        self.flush()

    @property
    def closed(self) -> bool:
        return False

    encoding = "utf-8"
    errors = "replace"


# ------------------------------------------------------------------ install
def install(on_crash: Callable[[str, BaseException], None] | None = None) -> None:
    """
    Route every unhandled exception in this process to data/crash.log.

    `on_crash` is an optional second sink — app.py passes one that also
    writes the audit log, so a crash shows up in the same file as everything
    else Jalen did that session. It is called inside its own try/except: a
    failing audit write must not stop the crash being recorded on disk.

    Safe to call more than once. The hooks are installed once; each call's
    sink is ADDED. That matters: run.py installs before anything can fail
    (with no audit log to hand yet) and Jalen.__init__ installs again once
    there is one, and a first-call-wins guard would throw the second away.
    """
    global _installed
    if on_crash is not None and on_crash not in _sinks:
        _sinks.append(on_crash)
    if _installed:
        return
    _installed = True

    if sys.stderr is None:
        sys.stderr = _StderrToCrashLog()  # type: ignore[assignment]
    # stdout too: print() is a silent no-op when stdout is None, which is
    # fine, but code that does sys.stdout.write() directly raises.
    if sys.stdout is None:
        sys.stdout = _StderrToCrashLog()  # type: ignore[assignment]

    def _report(where: str, exc: BaseException, thread: str = "") -> None:
        # Disk first, always. The sinks are best-effort extras; the crash log
        # is the record that has to exist even when everything else is broken.
        record_exception(where, exc, thread=thread)
        for sink in list(_sinks):
            try:
                sink(where, exc)
            except Exception:
                pass

    previous_hook = sys.excepthook

    def _excepthook(exc_type, exc, tb) -> None:  # noqa: ANN001
        # KeyboardInterrupt is a person pressing Ctrl+C, not a fault. It gets
        # a plain exit note so the log still explains the stop, but no
        # traceback and no crash record.
        if issubclass(exc_type, KeyboardInterrupt):
            note_exit("keyboard-interrupt")
        else:
            exc.__traceback__ = tb
            _report("main thread", exc)
            note_exit("unhandled-exception", error=f"{exc_type.__name__}: {exc}")
        try:
            previous_hook(exc_type, exc, tb)
        except Exception:
            pass

    sys.excepthook = _excepthook

    def _threadhook(args) -> None:  # noqa: ANN001
        if args.exc_value is None or issubclass(args.exc_type, SystemExit):
            return
        args.exc_value.__traceback__ = args.exc_traceback
        _report("thread", args.exc_value, thread=getattr(args.thread, "name", "?"))

    threading.excepthook = _threadhook

    def _unraisable(args) -> None:  # noqa: ANN001
        # Exceptions the interpreter cannot propagate — inside __del__, in a
        # PortAudio callback, during interpreter shutdown. These are exactly
        # the ones that used to vanish without trace, and an audio callback
        # is one frame away from everything Jalen does.
        exc = args.exc_value
        if exc is None:
            return
        exc.__traceback__ = args.exc_traceback
        _report(f"unraisable in {args.object!r}", exc)

    sys.unraisablehook = _unraisable


# --------------------------------------------------------------- exit state
def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def previous_exit() -> dict[str, Any] | None:
    """
    How the LAST run ended, or None if there is no record.

    Read this before mark_running() overwrites it. A returned record whose
    "state" is still "running" means the previous process never got to say
    why it stopped — which is the whole point of the file.
    """
    try:
        data = json.loads(EXIT_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def mark_running(session_id: str, mode: str) -> None:
    """Claim the exit record for this run. Called once, at startup."""
    _atomic_write(
        EXIT_STATE,
        {
            "state": "running",
            "session_id": session_id,
            "mode": mode,
            "pid": os.getpid(),
            "started_at": time.time(),
            "started_iso": _timestamp(),
        },
    )


def note_exit(reason: str, **detail: Any) -> None:
    """
    Record WHY this run is ending, the moment it is known.

    Called from several places on purpose — the frame loop when it breaks,
    shutdown() when it begins, the excepthook when it fires. The first
    caller with a real reason wins: a later generic "shutdown" must not
    overwrite the specific "unhandled-exception" that caused it. That
    ordering is the difference between a log that says what happened and one
    that says the process stopped, which was already obvious.
    """
    record = previous_exit() or {}
    if record.get("state") == "stopped" and record.get("reason"):
        return
    started = record.get("started_at")
    record.update(
        {
            "state": "stopped",
            "reason": reason,
            "ended_at": time.time(),
            "ended_iso": _timestamp(),
        }
    )
    if isinstance(started, (int, float)):
        record["uptime_s"] = round(time.time() - started, 1)
    for key, value in detail.items():
        record[key] = value
    _atomic_write(EXIT_STATE, record)


def describe_previous_exit(record: dict[str, Any] | None) -> str | None:
    """
    One audit-log line about how the previous run ended, or None if there is
    nothing worth saying.

    A clean stop says nothing: it is already in the audit log twice over and
    a line every startup is noise that trains you to skip the file. Only the
    unexplained one gets a line, because that is the one nobody could
    explain on 21 August.
    """
    if not record:
        return None
    if record.get("state") != "running":
        return None
    pid = record.get("pid", "?")
    session = str(record.get("session_id", "?"))[:12]
    mode = record.get("mode", "?")
    started = record.get("started_iso", "?")
    started_at = record.get("started_at")
    lived = ""
    if isinstance(started_at, (int, float)):
        lived = f", ran {round(time.time() - started_at)}s before this start"
    return (
        f"previous run ended WITHOUT shutting down — session {session}, "
        f"pid {pid}, mode {mode}, started {started}{lived}. "
        "No reason was recorded; check data/crash.log for a traceback."
    )
