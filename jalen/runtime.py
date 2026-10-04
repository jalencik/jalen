"""
Single-instance ownership and clean shutdown.

Two problems this solves, both reported from real use:

  D. "I saw two Jalen instances." Nothing prevented a second launch, and a
     second launch is not harmless: two processes both open the microphone,
     both poll Telegram, both drive TTS to the same output device, and both
     write the audit database. The symptom is doubled/competing replies.

  E. "I don't know how to stop it." There was no quit path at all beyond
     Ctrl+C in whichever terminal started it — and if it was started from a
     terminal that's since been closed, there was no way at all short of
     hunting PIDs in Task Manager.

Design notes:

* The lock stores the PID *and* the process start time. A bare PID file is
  not enough: Windows reuses PIDs, so a stale lock whose PID now belongs to
  some unrelated process would otherwise make Jalen refuse to start
  forever. Matching the start time as well makes reuse effectively
  impossible to mistake for a live instance.

* Stopping is cooperative first, forceful second. `request_stop()` writes a
  sentinel the running instance polls in its own loops, so it gets to
  release the microphone, close the audit DB, and stop its threads properly.
  Only if it ignores that for `STOP_GRACE_S` do we terminate it — a hard
  kill leaves the mic device held and the sqlite file mid-write.

* Nothing here holds an OS file handle open for locking. That sounds like
  the obvious approach, but on Windows a crashed process can leave the
  handle in a state that blocks the next start, which trades one
  can't-stop-it problem for a can't-start-it problem.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = ROOT / "data"
LOCK_PATH = RUNTIME_DIR / "jalen.lock"
STOP_PATH = RUNTIME_DIR / "jalen.stop"

STOP_GRACE_S = 8.0        # how long a cooperative stop gets before we force it
STOP_POLL_S = 0.2


@dataclass
class InstanceInfo:
    pid: int
    started_at: float
    mode: str

    @property
    def process(self) -> psutil.Process | None:
        try:
            proc = psutil.Process(self.pid)
        except psutil.NoSuchProcess:
            return None
        # Guard against PID reuse: same number, different process.
        if abs(proc.create_time() - self.started_at) > 1.0:
            return None
        return proc


def _read_lock() -> InstanceInfo | None:
    try:
        raw = LOCK_PATH.read_text(encoding="utf-8").strip().split("\n")
        return InstanceInfo(pid=int(raw[0]), started_at=float(raw[1]), mode=raw[2] if len(raw) > 2 else "?")
    except (OSError, ValueError, IndexError):
        return None


def running_instance() -> InstanceInfo | None:
    """The live Jalen instance, or None. Clears the lock if it's stale."""
    info = _read_lock()
    if info is None:
        return None
    if info.process is None:
        # Stale lock from a crash or a hard kill — take ownership rather than
        # refusing to start forever.
        try:
            LOCK_PATH.unlink()
        except OSError:
            pass
        return None
    return info


def acquire(mode: str) -> InstanceInfo | None:
    """
    Claim single-instance ownership. Returns None on success, or the
    InstanceInfo of the instance already running.
    """
    existing = running_instance()
    if existing is not None:
        return existing
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    clear_stop_request()
    me = psutil.Process(os.getpid())
    LOCK_PATH.write_text(f"{os.getpid()}\n{me.create_time()}\n{mode}\n", encoding="utf-8")
    return None


def release() -> None:
    """Drop ownership — only if the lock is actually ours."""
    info = _read_lock()
    if info is not None and info.pid != os.getpid():
        return
    for path in (LOCK_PATH, STOP_PATH, SIGNAL_PATH):
        try:
            path.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ stopping
def request_stop() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    STOP_PATH.write_text(str(time.time()), encoding="utf-8")


def stop_requested() -> bool:
    return STOP_PATH.exists()


def clear_stop_request() -> None:
    try:
        STOP_PATH.unlink()
    except OSError:
        pass


# ------------------------------------------------------------------ signals
#
# One more sentinel, generalising the stop one above. The global hotkey lives
# in a SEPARATE process — deliberately, because a hotkey that dies with the
# thing it is supposed to launch cannot launch it — so it needs a way to say
# "wake up" to an instance already running.
#
# Same mechanism as request_stop() rather than a new one: a file the running
# loop polls. A socket or a named pipe would be more elegant and would bring
# a listener thread, a port or pipe name to collide on, and a failure mode
# where the hotkey silently stops working because something else took the
# port. This cannot fail in a way that leaves no trace on disk.
#
# take_signal() reads AND deletes, so a signal is consumed exactly once. It
# is written by one process and read by one process; the unlink is what makes
# that safe without a lock.
SIGNAL_PATH = RUNTIME_DIR / "jalen.signal"

VALID_SIGNALS = frozenset({"wake", "mute", "unmute", "toggle"})


def send_signal(name: str) -> None:
    """Ask the running instance to do something. No-op if nothing is running."""
    if name not in VALID_SIGNALS:
        raise ValueError(f"unknown signal {name!r} (expected one of {sorted(VALID_SIGNALS)})")
    if running_instance() is None:
        return
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SIGNAL_PATH.write_text(name, encoding="utf-8")


def take_signal() -> str | None:
    """Consume the pending signal, if any. Returns None when there isn't one."""
    try:
        name = SIGNAL_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        SIGNAL_PATH.unlink()
    except OSError:
        pass
    return name if name in VALID_SIGNALS else None


def stop_running_instance(timeout: float = STOP_GRACE_S) -> str:
    """
    Stop the running instance, cooperatively if it will cooperate. Returns a
    human-readable outcome — this is spoken/printed to the user, so it says
    what actually happened rather than just succeeding silently.
    """
    info = running_instance()
    if info is None:
        return "Jalen isn't running."

    proc = info.process
    if proc is None:
        release()
        return "Jalen isn't running (cleared a stale lock)."

    request_stop()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not proc.is_running():
            clear_stop_request()
            return f"Stopped Jalen (pid {info.pid}) cleanly."
        time.sleep(STOP_POLL_S)

    # It didn't take the hint. Terminate the tree — the venv launcher stub
    # spawns the real interpreter as a child, so stopping only the parent
    # would leave the actual assistant running and still holding the mic.
    children = []
    try:
        children = proc.children(recursive=True)
    except psutil.Error:
        pass
    for p in [*children, proc]:
        try:
            p.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs([*children, proc], timeout=4)
    for p in alive:
        try:
            p.kill()
        except psutil.Error:
            pass
    clear_stop_request()
    release()
    return f"Force-stopped Jalen (pid {info.pid}) — it didn't shut down on request."


def status() -> str:
    info = running_instance()
    if info is None:
        return "Jalen is not running."
    age = time.time() - info.started_at
    hours, rem = divmod(int(age), 3600)
    mins = rem // 60
    uptime = f"{hours}h {mins}m" if hours else f"{mins}m"
    return f"Jalen is running — pid {info.pid}, mode {info.mode}, up {uptime}."
