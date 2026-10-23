"""
"It should not become deaf out of nowhere ... as the backup it should be able
to explain what is wrong with it."                        - his words, 2026-10-23

DEAF
----
A capture device that stops delivering - a headset unplugged, a driver reset,
another application taking the device - does not raise. The callback simply
stops being called. Microphone.frames() noticed after ten seconds and wrote
"Jalen is deaf but still running" to the crash log, and that was all: deaf
until someone restarted him. Now the stream is closed and opened again - the
configured device, then the default one - every ten seconds while it stays
dead, and he is told once that it is down and once that it is back.

WHAT IS WRONG
-------------
"What's wrong with you?" ran `run.py --check` and read the printout: package
versions and file paths, describing the INSTALL. What was actually wrong with
the running Jalen - an expired sign-in, a microphone gone quiet, speech
recognition on the slow offline model, a lockdown - lives in this process.
Jalen.what_is_wrong says it in plain words, from the router, without the brain.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from jalen.audio import mic as mic_module
from jalen.audio.mic import RETRY_EVERY_S, STALL_WARN_S, Microphone
from jalen.brain.router import IntentRouter
from jalen.config import CONFIG

from test_stop_window_takes_answers import a_jalen


# --------------------------------------------------------------------------
# 1. THE MICROPHONE COMES BACK
# --------------------------------------------------------------------------
class FakeSoundDevice:
    """sounddevice, with devices that open or refuse to as the test says."""

    def __init__(self, refuses=()):
        self.refuses = set(refuses)
        self.opened, self.closed = [], []
        sd = self

        class InputStream:
            def __init__(self, *, device=None, **kwargs):
                if device in sd.refuses:
                    raise OSError(f"device {device!r} is gone")
                self.device = device

            def start(self):
                sd.opened.append(self.device)

            def stop(self):
                pass

            def close(self):
                sd.closed.append(self.device)

        self.InputStream = InputStream


@pytest.fixture
def microphone(monkeypatch):
    def build(refuses=(), device="USB headset"):
        sd = FakeSoundDevice(refuses)
        monkeypatch.setattr(mic_module, "sd", sd)
        monkeypatch.setattr(mic_module.crashlog, "write", lambda *a, **k: None)
        m = Microphone(CONFIG)
        m.device = device
        m.told = []
        m.notify = m.told.append
        m.start()
        m.sd = sd
        return m

    return build


def gone_quiet(m, seconds=STALL_WARN_S + 1):
    m.last_callback_at = time.monotonic() - seconds
    m._last_reopen_at = 0.0


def test_a_healthy_stream_is_left_alone(microphone):
    m = microphone()
    m._reopen_if_stalled()
    assert m.sd.opened == ["USB headset"] and m.reopened == 0 and m.told == []


def test_a_stream_that_went_quiet_is_reopened_and_he_is_told(microphone):
    m = microphone()
    gone_quiet(m)
    m._reopen_if_stalled()
    assert m.sd.closed == ["USB headset"]
    assert m.sd.opened == ["USB headset", "USB headset"]
    assert m.reopened == 1
    assert m.told == ["My microphone dropped out for a moment. It's back now."]
    assert time.monotonic() - m.last_callback_at < 1.0, "the stall clock did not restart"


def test_an_unplugged_device_falls_back_to_the_default_one(microphone):
    m = microphone()
    m.sd.refuses.add("USB headset")
    gone_quiet(m)
    m._reopen_if_stalled()
    assert m.sd.opened[-1] is None
    assert m.device == "USB headset", "the configured device is still the one tried first"


def test_when_nothing_opens_he_is_told_once_and_it_keeps_trying(microphone):
    m = microphone()
    m.sd.refuses.update({"USB headset", None})
    gone_quiet(m)
    m._reopen_if_stalled()
    gone_quiet(m)
    m._reopen_if_stalled()
    assert m.told == ["My microphone has stopped working, so I can't hear you. "
                      "Check that it's plugged in. I'll keep trying."]
    m.sd.refuses.clear()
    gone_quiet(m)
    m._reopen_if_stalled()
    assert m.told[-1] == "My microphone is working again. I can hear you."


def test_it_does_not_reopen_more_often_than_every_ten_seconds(microphone):
    m = microphone()
    gone_quiet(m)
    m._reopen_if_stalled()
    m.last_callback_at = time.monotonic() - STALL_WARN_S - 1       # still quiet
    m._reopen_if_stalled()                                          # but just tried
    assert m.reopened == 1
    assert RETRY_EVERY_S >= STALL_WARN_S


def test_frames_reopens_when_it_finds_nothing(microphone, monkeypatch):
    m = microphone()
    calls = []
    monkeypatch.setattr(m, "_reopen_if_stalled", lambda: (calls.append(1), m._running.clear()))
    assert list(m.frames(timeout=0.01)) == []
    assert calls == [1]


def test_a_failure_to_tell_him_never_takes_the_microphone_down(microphone):
    m = microphone()

    def broken(sentence):
        raise RuntimeError("speaker gone")

    m.notify = broken
    gone_quiet(m)
    m._reopen_if_stalled()
    assert m.reopened == 1


def test_the_running_jalen_hands_the_microphone_a_voice():
    import inspect

    from jalen.app import Jalen

    source = inspect.getsource(Jalen.run)
    assert "self.mic.notify = _the_microphone_says" in source
    assert source.index("self.mic.notify = ") < source.index("with self.mic:")


# --------------------------------------------------------------------------
# 2. "WHAT'S WRONG WITH YOU?"
# --------------------------------------------------------------------------
@pytest.mark.parametrize("said", [
    "what's wrong with you", "what is wrong with you?", "is something wrong",
    "is anything wrong with you", "why aren't you working", "why can't you hear me",
    "why are you silent", "what happened to you", "tell me what's wrong",
    "what's up with you",
])
def test_the_ways_he_asks_reach_the_answer(said):
    intent = IntentRouter(CONFIG).route(said)
    assert intent is not None and intent.tool == "what_is_wrong", said


def test_the_full_check_is_still_there_by_name():
    assert IntentRouter(CONFIG).route("diagnose yourself").tool == "self_diagnose"


def healthy():
    j = a_jalen()
    j._brain_down = None
    j.mic = SimpleNamespace(_told_it_is_down=False, reopened=0)
    j.stt = SimpleNamespace(last_engine="groq", primary="groq")
    j.muted = j.paused = False
    j.safety = SimpleNamespace(governor=SimpleNamespace(locked=False, usage=lambda: []))
    return j


@pytest.fixture(autouse=True)
def no_crash_record(monkeypatch):
    from jalen import app as app_module

    monkeypatch.setattr(app_module.crashlog, "previous_exit", lambda: {"state": "stopped"})


def test_when_nothing_is_wrong_it_says_so():
    assert healthy().what_is_wrong().startswith("Nothing looks wrong")


def test_an_expired_sign_in_is_said_with_what_to_do():
    j = healthy()
    j._brain_down, j._brain_down_detail = "authentication_failed", ""
    said = j.what_is_wrong()
    assert said.startswith("One thing is wrong.") and "sign-in has expired" in said


def test_a_dead_microphone_and_the_offline_recogniser_are_both_said():
    j = healthy()
    j.mic._told_it_is_down = True
    j.stt.last_engine = "moonshine"
    said = j.what_is_wrong()
    assert said.startswith("2 things are wrong.")
    assert "microphone isn't delivering" in said and "offline model (moonshine)" in said


def test_a_microphone_that_came_back_is_reported_as_history_not_as_broken():
    j = healthy()
    j.mic.reopened = 2
    assert "dropped out 2 times since I started. It's working now." in j.what_is_wrong()


def test_lockdown_and_a_full_limit_are_said():
    j = healthy()
    j.safety.governor = SimpleNamespace(
        locked=True,
        usage=lambda: [{"name": "emails", "label": "emails", "used": 10, "max": 10},
                       {"name": "deletions", "label": "deletions", "used": 1, "max": 10}])
    said = j.what_is_wrong()
    assert "lockdown" in said and "limit on emails" in said and "deletions" not in said


def test_muted_and_paused_are_said_with_how_to_undo_them():
    j = healthy()
    j.muted = j.paused = True
    said = j.what_is_wrong()
    assert "Ctrl+Alt+J" in said and "Hey Jalen" in said


def test_a_run_that_crashed_last_time_is_said(monkeypatch):
    from jalen import app as app_module

    monkeypatch.setattr(app_module.crashlog, "previous_exit", lambda: {"state": "running"})
    assert "run.py --why" in healthy().what_is_wrong()


def test_it_is_answered_by_the_router_without_the_brain():
    """The brain may be the part that is broken."""
    j = healthy()
    j.handle_with_brain = None          # would raise if it were reached
    from jalen.brain.router import Intent

    said = j.handle_local(Intent(tool="what_is_wrong", args={}), his_own_words=True)
    assert said.startswith("Nothing looks wrong")
