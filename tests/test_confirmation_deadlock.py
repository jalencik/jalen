"""
The deadlock that made "send a Telegram message" do nothing at all.

WHAT HAPPENED, from data/audit.jsonl on 21 Aug:

    07:04:47  "send telegram message: SAT Talk. Confirm?"
    07:15:48  Jalen stopped

Eleven minutes, no answer recorded, no "no answer, so I've cancelled it",
and no action record either way. confirm() has a 20-second timeout, so a
missing answer should have produced a cancellation line within the minute.
It produced nothing, which means confirm() never returned.

WHY. Speaking a streamed reply and speaking a confirmation both need
Speaker._say_lock, and SpeechStream holds it for the WHOLE reply session —
deliberately, so `speaker.speaking` never drops between sentences and the
microphone doesn't hear the tail of Jalen's own voice as user speech. So:

    turn thread   opens a SpeechStream, which takes _say_lock and holds it
    model         calls send_telegram_message
    hook          calls confirm() -> say_blocking() -> Speaker.say()
    Speaker.say() blocks forever on _say_lock
    the stream    can never finish, because close() is only reached after
                  the turn returns, and the turn is stuck in the hook

Both halves wait for the other. Jalen goes silent and stays silent — which
is exactly what "it is ignoring me again" describes.

The audit line was written BEFORE the audio, which is why the log looks like
the question was asked. It never was; nobody heard it.

THE FIX. Speaker.say_now() hands urgent text to the active stream to speak
inline, and only falls back to the locking path when no stream is running.
"""
from __future__ import annotations

import threading
import time

import pytest

from jalen.audio.tts import Speaker


class Cfg:
    def __init__(self, **over):
        self._d = {
            "tts.engine": "edge",
            "tts.voice": "en-US-AndrewNeural",
            "tts.rate": "+0%",
            "tts.volume": "+0%",
            "tts.pitch": "+0Hz",
            "tts.sentence_streaming": True,
            "tts.max_spoken_chars": 320,
            "tts.retry_attempts": 1,
            **over,
        }

    def get_path(self, key, default=None):
        return self._d.get(key, default)


@pytest.fixture
def silent_speaker(monkeypatch):
    """
    A Speaker that renders and 'plays' instantly, with no network and no
    audio device. Everything about the locking and threading is real — that
    is where the bug lives.
    """
    speaker = Speaker(Cfg())

    played: list[str] = []

    def fake_render(sentence):
        import numpy as np

        played.append(sentence)
        return np.zeros(160, dtype="float32"), 16000

    def fake_play(pcm, rate):
        time.sleep(0.01)
        return True

    monkeypatch.setattr(speaker, "_render_cached", fake_render)
    monkeypatch.setattr(speaker, "_play", fake_play)
    speaker.played = played
    return speaker


def test_a_confirmation_can_be_spoken_while_a_reply_is_streaming(silent_speaker):
    """
    THE REGRESSION TEST. Before say_now(), this call never returned and the
    test would hang until pytest was killed — which is precisely what
    happened to Jalen in front of the user.
    """
    speaker = silent_speaker
    stream = speaker.open_stream()
    stream.push("I am part way through answering you. ")

    # Give the stream a moment to take the lock, exactly as a real reply does.
    time.sleep(0.05)

    result: list = []

    def ask():
        result.append(speaker.say_now("Send telegram message to SAT Talk. Confirm?"))

    asker = threading.Thread(target=ask, daemon=True)
    asker.start()
    asker.join(timeout=10.0)

    assert not asker.is_alive(), (
        "say_now() deadlocked against the streaming reply — this is the bug "
        "that made every RED confirmation silently do nothing"
    )
    assert result == [True]
    assert any("Confirm?" in s for s in speaker.played), (
        "the question was never actually spoken, only logged"
    )
    stream.close()


def test_the_reply_still_finishes_after_the_interruption(silent_speaker):
    """
    A confirmation must not destroy the answer it interrupted. Stopping the
    stream outright would have been the easy fix and would have silently
    dropped whatever the model said next.
    """
    speaker = silent_speaker
    stream = speaker.open_stream()
    stream.push("First part. ")
    time.sleep(0.05)

    speaker.say_now("Confirm?")
    stream.push("Second part. ")
    spoken = stream.close(timeout=10.0)

    assert "First part." in spoken
    assert "Second part." in spoken, "the reply was lost when the confirmation interrupted it"


def test_say_now_works_with_no_stream_running(silent_speaker):
    """The ordinary case: nothing streaming, so it is just a normal say()."""
    assert silent_speaker.say_now("Nothing else is speaking.") is True
    assert silent_speaker.played == ["Nothing else is speaking."]


def test_say_now_falls_back_when_the_stream_is_already_finished(silent_speaker):
    """
    A stream that has closed cannot speak for us. Waiting on it would hang;
    the question must still be asked.
    """
    speaker = silent_speaker
    stream = speaker.open_stream()
    stream.push("Done. ")
    stream.close(timeout=10.0)

    assert speaker.say_now("Confirm?") is True
    assert any("Confirm?" in s for s in speaker.played)


def test_the_app_asks_through_the_non_blocking_path():
    """
    say_blocking() is what confirm() and announce() both use. If it ever
    goes back to Speaker.say() directly, the deadlock returns and every RED
    action silently stops working again — with no error anywhere.
    """
    import inspect

    from jalen.app import Jalen

    source = inspect.getsource(Jalen.say_blocking)
    assert "say_now" in source, (
        "say_blocking no longer uses say_now — RED confirmations will deadlock "
        "against a streaming reply again"
    )
