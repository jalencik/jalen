"""
"As soon as I tap on Ctrl+Alt+J, mic should start instantly ... as soon as I
tap on Ctrl+Alt+K, it should instantly shut down."     - his words, 2026-10-23

WHY THE MICROPHONE TOOK ABOUT A MINUTE
--------------------------------------
Ctrl+Alt+K ended the process. So the next Ctrl+Alt+J found nothing running and
launched one from cold: Python, every import, every model, the orb - on a
machine with about a gigabyte free. The only key press that was ever fast was
one that found Jalen already running.

NOW
---
* Ctrl+Alt+K still ends the process - everything it was doing stops, as a
  kill switch must - and then starts a fresh one in STANDBY (run.py
  --standby): loaded and warm, microphone CLOSED, nothing on screen.
* Ctrl+Alt+J on a standby Jalen only has to open the microphone, and it opens
  straight into a window for him, exactly as if he had said the wake word.
* Ctrl+Alt+K on a standby Jalen ends it for good.
* The voice stops the instant the kill key is pressed, not when the microphone
  loop next looks (Jalen.watch_for_the_kill_key), and the brain's goodbye can
  no longer hold the shutdown open (app.BRAIN_STOP_TIMEOUT_S).

The Win32 key registration is not testable here; what each key DOES is.
"""
from __future__ import annotations

import asyncio
import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from jalen import app as app_module
from jalen import runtime
from jalen.app import BRAIN_STOP_TIMEOUT_S, Jalen

from test_listening_does_not_obey_other_people_or_itself import Heard, Room, _Mic, _Vad

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# 1. STANDBY: THE MICROPHONE STAYS CLOSED UNTIL THE KEY
# --------------------------------------------------------------------------
class _WatchedMic(_Mic):
    def __init__(self, frames, log):
        super().__init__(frames)
        self.log = log

    def __enter__(self):
        self.log.append("microphone opened")
        return self


class _Quiet(_Vad):
    def probability(self, frame):
        return 0.0


def standby_room(monkeypatch, signals, *, stop_after=None):
    """run(standby=True) with `signals` arriving one per look for the key."""
    log = []
    r = Room(monkeypatch, [Heard("open chrome")])
    j = r.jalen
    j.vad = _Quiet()
    j.mic = _WatchedMic(3, log)
    j.startup_notes = lambda: []
    j.orb.start = lambda: log.append("orb shown")
    pending = list(signals)
    looks = {"n": 0}

    def take_signal():
        looks["n"] += 1
        log.append(f"look {looks['n']}")
        return pending.pop(0) if pending else None

    def stop_requested():
        return stop_after is not None and looks["n"] >= stop_after

    monkeypatch.setattr(app_module.runtime, "take_signal", take_signal)
    monkeypatch.setattr(app_module.runtime, "stop_requested", stop_requested)
    monkeypatch.setattr(app_module.runtime, "set_mode", lambda mode: log.append(f"mode {mode}"))
    monkeypatch.setattr(Jalen, "KEY_POLL_S", 0.0)
    r.log = log
    return r


def test_in_standby_nothing_listens_until_the_wake_key(monkeypatch):
    r = standby_room(monkeypatch, [None, None, "toggle"])
    r.jalen.run(standby=True)
    opened = r.log.index("microphone opened")
    assert r.log.index("look 3") < opened, "the microphone opened before the key"
    assert r.log.index("orb shown") < opened
    assert "mode voice" in r.log


def test_the_key_opens_the_microphone_straight_into_a_window_for_him(monkeypatch):
    """No "Hey Jalen" needed after the key: his first sentence is acted on, as his."""
    r = standby_room(monkeypatch, ["toggle"])
    r.jalen.muted = True
    r.jalen.run(standby=True)
    assert r.texts() == ["open chrome"]
    assert r.from_him() == [True], "the key is the wake word: the sentence is his"
    assert r.jalen.muted is False


@pytest.mark.parametrize("signal", ["wake", "toggle", "unmute"])
def test_every_wake_signal_wakes_standby(monkeypatch, signal):
    r = standby_room(monkeypatch, [signal])
    r.jalen.run(standby=True)
    assert "microphone opened" in r.log


def test_mute_does_not_wake_standby(monkeypatch):
    r = standby_room(monkeypatch, ["mute", None, None], stop_after=4)
    r.jalen.run(standby=True)
    assert "microphone opened" not in r.log


def test_a_stop_in_standby_ends_it_without_ever_opening_the_microphone(monkeypatch):
    r = standby_room(monkeypatch, [None, None, None], stop_after=2)
    r.jalen.run(standby=True)
    assert "microphone opened" not in r.log and "orb shown" not in r.log
    assert r.jalen._exit_reason == "stop-file"


def test_a_quit_in_standby_ends_it_too(monkeypatch):
    r = standby_room(monkeypatch, [None])
    r.jalen._quit = SimpleNamespace(is_set=lambda: True)
    r.jalen.run(standby=True)
    assert "microphone opened" not in r.log
    assert r.jalen._exit_reason == "quit-requested"


def test_without_standby_nothing_changes(monkeypatch):
    r = standby_room(monkeypatch, [])
    r.jalen.run()
    assert r.log[0] == "orb shown" and "microphone opened" in r.log


def test_the_moment_the_microphone_opened_is_written_down(monkeypatch):
    r = standby_room(monkeypatch, ["toggle"])
    r.jalen.run(standby=True)
    (row,) = r.rows("microphone open")
    assert "since_process_start_s" in row["detail"] and "since_wake_key_s" in row["detail"]


# --------------------------------------------------------------------------
# 2. THE KILL KEY
# --------------------------------------------------------------------------
@pytest.fixture
def hotkeys(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("jalen_hotkeys_keys", ROOT / "scripts" / "hotkeys.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "LOG_PATH", tmp_path / "hotkeys.log")
    module.launched = []
    module.stopped = []
    monkeypatch.setattr(module, "launch_jalen", lambda standby=False: module.launched.append(standby))

    def stop(timeout=runtime.STOP_GRACE_S):
        module.stopped.append(timeout)
        return "Stopped Jalen cleanly."

    monkeypatch.setattr(module.runtime, "stop_running_instance", stop)
    return module


def running(hotkeys, monkeypatch, mode):
    info = None if mode is None else runtime.InstanceInfo(pid=1, started_at=0.0, mode=mode)
    monkeypatch.setattr(hotkeys.runtime, "running_instance", lambda: info)


def test_the_kill_key_stops_a_listening_jalen_and_leaves_a_warm_one_waiting(hotkeys, monkeypatch):
    running(hotkeys, monkeypatch, "voice")
    hotkeys.on_kill()
    assert hotkeys.stopped == [hotkeys.KILL_GRACE_S]
    assert hotkeys.launched == [True], "no standby was started, so the next wake is a cold start"


def test_the_kill_key_on_a_standby_jalen_ends_it_for_good(hotkeys, monkeypatch):
    running(hotkeys, monkeypatch, "standby")
    hotkeys.on_kill()
    assert hotkeys.stopped and hotkeys.launched == []


@pytest.mark.parametrize("mode", ["text", "telegram", None])
def test_the_kill_key_never_starts_a_voice_jalen_in_place_of_something_else(hotkeys, monkeypatch, mode):
    running(hotkeys, monkeypatch, mode)
    hotkeys.on_kill()
    assert hotkeys.launched == []


def test_the_standby_after_the_kill_key_can_be_turned_off(hotkeys, monkeypatch):
    running(hotkeys, monkeypatch, "voice")
    monkeypatch.setattr(hotkeys.CONFIG, "get_path",
                        lambda key, default=None: False if key == "startup.standby_after_kill" else default)
    hotkeys.on_kill()
    assert hotkeys.launched == []


def test_the_kill_key_gives_a_shorter_grace_than_the_command_line_stop(hotkeys):
    assert hotkeys.KILL_GRACE_S < runtime.STOP_GRACE_S
    assert hotkeys.KILL_GRACE_S > BRAIN_STOP_TIMEOUT_S, "the brain's bounded goodbye must fit inside it"


def test_the_wake_key_launches_unmuted_and_the_kill_key_launches_standby(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("jalen_hotkeys_launch", ROOT / "scripts" / "hotkeys.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "LOG_PATH", tmp_path / "hotkeys.log")
    started = []
    monkeypatch.setattr(module.subprocess, "Popen", lambda args, **k: started.append(args[-1]))
    module.launch_jalen()
    module.launch_jalen(standby=True)
    assert started == ["--unmuted", "--standby"]


def test_the_wake_key_on_a_standby_jalen_sends_the_signal_it_wakes_on(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("jalen_hotkeys_wake", ROOT / "scripts" / "hotkeys.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sent = []
    monkeypatch.setattr(module.runtime, "running_instance",
                        lambda: runtime.InstanceInfo(pid=1, started_at=0.0, mode="standby"))
    monkeypatch.setattr(module.runtime, "send_signal", sent.append)
    module.on_wake()
    assert sent == ["toggle"]


# --------------------------------------------------------------------------
# 3. SILENT THE INSTANT THE KEY IS PRESSED, AND A SHUTDOWN THAT CANNOT HANG
# --------------------------------------------------------------------------
def test_the_voice_stops_the_moment_the_kill_key_is_pressed(monkeypatch):
    stopped = []
    j = Jalen.__new__(Jalen)
    j.speaker = SimpleNamespace(stop=lambda: stopped.append("speaker"))
    j.orb = SimpleNamespace(stop=lambda: stopped.append("orb"))
    pressed = {"at": None}
    monkeypatch.setattr(app_module.runtime, "stop_requested", lambda: pressed["at"] is not None)
    j.watch_for_the_kill_key()
    time.sleep(0.1)
    assert stopped == []
    pressed["at"] = time.monotonic()
    deadline = time.monotonic() + 2
    while len(stopped) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert stopped == ["speaker", "orb"]
    assert time.monotonic() - pressed["at"] < 0.5


def test_a_brain_that_will_not_say_goodbye_cannot_hold_the_shutdown(monkeypatch):
    j = Jalen.__new__(Jalen)
    j._loop = asyncio.new_event_loop()
    thread = threading.Thread(target=j._loop.run_forever, daemon=True)
    thread.start()
    j._loop_thread = thread

    class Stuck:
        async def stop(self):
            await asyncio.sleep(60)

    j.brain = Stuck()
    j.session_id, j.mode, j._exit_reason = "test", "voice", None
    j.running = threading.Event()
    j.audit = SimpleNamespace(write=lambda *a, **k: None, error=lambda *a, **k: None,
                              close=lambda: None)
    for name in ("speaker", "orb", "mic"):
        setattr(j, name, SimpleNamespace(stop=lambda: None))
    j._close_stt = lambda: None
    monkeypatch.setattr(app_module.crashlog, "note_exit", lambda *a, **k: None)
    started = time.monotonic()
    j.shutdown("test")
    took = time.monotonic() - started
    assert took < BRAIN_STOP_TIMEOUT_S + 3.0, f"shutdown waited {took:.1f}s on a stuck brain"


def test_run_py_passes_standby_through_and_the_lock_says_so():
    source = (ROOT / "run.py").read_text(encoding="utf-8")
    assert '"--standby"' in source
    assert 'runtime.acquire("standby" if (args.standby and mode == "voice") else mode)' in source
    assert "jalen.run(standby=args.standby)" in source
    assert "jalen.watch_for_the_kill_key()" in source


def test_set_mode_rewrites_only_our_own_lock(monkeypatch, tmp_path):
    import os

    lock = tmp_path / "jalen.lock"
    monkeypatch.setattr(runtime, "LOCK_PATH", lock)
    lock.write_text("1\n0.0\nstandby\n", encoding="utf-8")          # somebody else's
    runtime.set_mode("voice")
    assert lock.read_text(encoding="utf-8").endswith("standby\n")
    lock.write_text(f"{os.getpid()}\n12.5\nstandby\n", encoding="utf-8")
    runtime.set_mode("voice")
    assert lock.read_text(encoding="utf-8") == f"{os.getpid()}\n12.5\nvoice\n"
