"""
"Jalen asks me to confirm, I answer, and twenty seconds later he says nobody answered."

THE REPORT (2026-10-04, the owner, in his words condensed): Jalen decides a
confirmation is needed; sometimes he does not actually say the question; I
answer anyway; the orb goes yellow; it looks deaf; later it announces no
response was received although I answered.

WHAT WAS WRONG, found in the code and reproduced here:

  1. DEAF. A brain turn speaks through a SpeechStream, which keeps
     `speaker.speaking` set for the whole turn. The confirmation is asked from
     inside that turn, so `speaking` stayed set for the whole wait - and the
     microphone loop discards every frame while `speaking` is set, unless the
     sound is long and loud enough to count as barge-in, and then the frames
     that proved it are discarded too. "yes" was dropped, or arrived clipped
     and too short to transcribe.
  2. YELLOW. The orb put "a turn is in flight" (thinking, yellow) above
     "waiting for his answer", and a confirmation is always inside a turn.
  3. UNHEARD BUT WAITED ON. A question the stream could not render was
     followed by say(), which waits on the lock that stream holds for the
     rest of the turn: no question, no timeout, nothing.
  4. A STALE YES. An answer carried nothing saying which question it
     answered, so a "yes" still being transcribed when one confirmation timed
     out could approve the next one, which he had never heard.
  5. THE FIRST WORD. The frame that opened the answer window, and the quiet
     start of the word before it, never reached the collector.

Everything here runs the real Speaker, SpeechStream, collector and the real
Jalen methods; only the network voice and the sound card are faked.
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from jarvis.app import ConfirmAnswer, Jalen
from jarvis.audio.tts import HeldPrompt, Speaker
from jarvis.audio.vad import UtteranceCollector
from jarvis.config import CONFIG
from jarvis.confirmation import DELETE, TELEGRAM, Confirmation


QUESTION = "Delete these 12 files from Downloads. Confirm?"


class _Cfg:
    def __init__(self, **over):
        self._d = {"tts.sentence_streaming": True, "tts.retry_attempts": 1, **over}

    def get_path(self, key, default=None):
        return self._d.get(key, default)


@pytest.fixture
def speaker(monkeypatch):
    """A real Speaker that renders instantly and 'plays' for 20 ms a line."""
    sp = Speaker(_Cfg())
    sp.played = []
    sp.fail_render = set()

    def render(sentence):
        if sentence in sp.fail_render:
            return None
        sp.played.append(sentence)
        return np.zeros(160, dtype="float32"), 16000

    def play(pcm, rate):
        time.sleep(0.02)
        return not sp._interrupt.is_set()

    monkeypatch.setattr(sp, "_render_cached", render)
    monkeypatch.setattr(sp, "_play", play)
    return sp


def _until(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


# ---------------------------------------------------------------------------
# 1. DEAF: the speaker is silent while he answers
# ---------------------------------------------------------------------------
def test_the_speaker_is_not_speaking_while_he_answers_a_question_asked_mid_reply(speaker):
    """
    The microphone loop treats `speaker.speaking` as "this is Jalen's own
    voice" and throws the frame away. While Jalen waits for an answer it must
    be False, or his answer is thrown away with it.
    """
    stream = speaker.open_stream()
    stream.push("Give me a second. ")
    assert _until(lambda: "Give me a second." in speaker.played)

    prompt = speaker.ask(QUESTION)
    try:
        assert prompt.spoken, "the question never reached the speakers"
        assert QUESTION in speaker.played
        assert prompt.started_at and prompt.ended_at >= prompt.started_at
        assert speaker.speaking is False, (
            "the speaker still reports speaking while Jalen waits for the answer - "
            "the microphone loop discards every frame he says"
        )
        # And it stays silent: nothing of the reply talks over his answer.
        stream.push("This sentence must wait for his answer. ")
        time.sleep(0.15)
        assert speaker.speaking is False
        assert "This sentence must wait for his answer." not in speaker.played
    finally:
        prompt.release()
    # The reply carries on once he has answered.
    spoken = stream.close(timeout=5.0)
    assert "This sentence must wait for his answer." in spoken


def test_a_line_said_during_the_wait_is_spoken_and_the_silence_resumes(speaker):
    """"Sorry - was that a yes or a no?" must be heard during the wait, not after it."""
    stream = speaker.open_stream()
    stream.push("Working. ")
    assert _until(lambda: "Working." in speaker.played)
    prompt = speaker.ask(QUESTION)
    try:
        done = []
        threading.Thread(target=lambda: done.append(speaker.say_now("Sorry - was that a yes or a no?")),
                         daemon=True).start()
        assert _until(lambda: done == [True]), "the re-ask waited for the confirmation to end"
        assert "Sorry - was that a yes or a no?" in speaker.played
        assert speaker.speaking is False
    finally:
        prompt.release()
    stream.close(timeout=5.0)


def test_a_question_asked_with_no_reply_streaming_is_still_held_and_timed(speaker):
    prompt = speaker.ask(QUESTION)
    assert prompt.spoken
    assert prompt.ended_at >= prompt.started_at > 0
    assert speaker.speaking is False
    prompt.release()


# ---------------------------------------------------------------------------
# 3. A question that could not be said is not waited on - and never deadlocks
# ---------------------------------------------------------------------------
def test_say_now_returns_instead_of_deadlocking_when_the_stream_cannot_say_it(speaker):
    stream = speaker.open_stream()
    stream.push("Part of a reply. ")
    assert _until(lambda: "Part of a reply." in speaker.played)
    speaker.fail_render.add(QUESTION)
    result = []
    worker = threading.Thread(target=lambda: result.append(speaker.say_now(QUESTION)), daemon=True)
    worker.start()
    worker.join(timeout=5.0)
    assert not worker.is_alive(), "say_now() waited on the lock the stream holds - a deadlock"
    assert result == [False]
    stream.close(timeout=5.0)


def test_ask_reports_an_unrendered_question_as_unspoken(speaker):
    stream = speaker.open_stream()
    stream.push("Part of a reply. ")
    assert _until(lambda: "Part of a reply." in speaker.played)
    speaker.fail_render.add(QUESTION)
    prompt = speaker.ask(QUESTION)
    assert prompt.spoken is False
    prompt.release()
    stream.close(timeout=5.0)


# ---------------------------------------------------------------------------
# The real confirm(), end to end: asked, heard, answered, released
# ---------------------------------------------------------------------------
class _Orb:
    def __init__(self):
        self.states = []
        self.captions = []

    def set_state(self, state):
        self.states.append(state)

    def set_transcript(self, text):
        self.captions.append(text)

    def flash(self, *a, **k):
        pass


class _Stop(Exception):
    pass


def _jalen(speaker, timeout_s=5):
    j = Jalen.__new__(Jalen)
    j.cfg = SimpleNamespace(get_path=lambda key, default=None: {
        "safety.confirm_timeout_s": timeout_s}.get(key, CONFIG.get_path(key, default)))
    j.speaker = speaker
    j.orb = _Orb()
    j.rows = []
    j.audit = SimpleNamespace(utterance=lambda *a, **k: None,
                              write=lambda kind, **k: j.rows.append(k),
                              error=lambda *a, **k: None)
    j.said = []
    j.say = lambda text, **k: j.said.append(text)
    j._note_said = lambda text: None
    j.muted = False
    j.paused = False
    j.kill = threading.Event()
    j._answer_q = queue.Queue()
    j._reply_q = queue.Queue()
    j._awaiting_reply = False
    j._awaiting_confirmation = False
    j._awaiting_stop = False
    j._pending_rating = None
    j._last_user_text, j._last_user_at, j._plan = "", 0.0, None
    j._turn_lock, j._active_turns = threading.Lock(), {1}     # asked from INSIDE a turn
    j.kill_phrases = [p.lower() for p in CONFIG.get_path("safety.kill_phrases", [])]
    j.end_phrases = []
    j._confirm_lock = None
    j._confirm_question = ""
    j._confirm_asked_at = 0.0
    j._confirm_reasked = False
    j._confirmation = None
    j._reply_question_started_at = 0.0
    j._orb_listening = False
    j.routed = []

    def _route(text):
        j.routed.append(text)
        raise _Stop()

    j.router = SimpleNamespace(route=_route)
    return j


def _confirm_in_background(j, confirmation):
    out = {}

    def run():
        out["answer"] = asyncio.new_event_loop().run_until_complete(j.confirm(confirmation))

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, out


def _say(j, text, began_at=None):
    try:
        j.process(text, began_at=time.monotonic() if began_at is None else began_at)
    except _Stop:
        pass


def test_confirm_asks_aloud_holds_the_room_quiet_and_takes_his_yes(speaker):
    stream = speaker.open_stream()
    stream.push("Give me a second. ")
    assert _until(lambda: "Give me a second." in speaker.played)
    j = _jalen(speaker)
    pending = Confirmation(question=QUESTION, kind=DELETE, action="delete_file")
    thread, out = _confirm_in_background(j, pending)

    assert _until(lambda: j._awaiting_confirmation and pending.listening_started_at)
    assert QUESTION in speaker.played, "the confirmation question was never said"
    assert j.orb.captions[-1] == QUESTION, "what is shown is not what is said"
    assert speaker.speaking is False, "the microphone would discard his answer"
    assert j.orb.states[-1] == "awaiting", (
        f"the orb says {j.orb.states[-1]!r} while Jalen waits for him to answer")

    _say(j, "Yes.")
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert out["answer"] and out["answer"].outcome == "yes"
    row = [r for r in j.rows if str(r.get("summary", "")).startswith("confirmation")][-1]
    assert row["detail"]["outcome"] == "yes"
    assert row["detail"]["kind"] == DELETE
    for stamp in ("spoken_ms", "listening_started_ms", "decided_ms"):
        assert stamp in row["detail"], stamp
    stream.push("Deleted. ")
    assert "Deleted." in stream.close(timeout=5.0), "the reply after the answer was lost"


@pytest.mark.parametrize("answer, expected", [
    ("yes", "yes"), ("yeah", "yes"), ("yep", "yes"), ("do it", "yes"), ("go ahead", "yes"),
    ("YES!", "yes"), ("Yeah, do it.", "yes"), ("okay", "yes"),
    ("no", "no"), ("nope", "no"), ("cancel", "no"), ("don’t", "no"), ("No, cancel.", "no"),
])
def test_his_short_answers_settle_the_confirmation_he_heard(speaker, answer, expected):
    j = _jalen(speaker)
    pending = Confirmation(question=QUESTION, kind=DELETE)
    thread, out = _confirm_in_background(j, pending)
    assert _until(lambda: pending.listening_started_at)
    _say(j, answer)
    thread.join(timeout=5.0)
    assert out["answer"].outcome == expected, answer


def test_stop_said_during_the_wait_cancels(speaker):
    j = _jalen(speaker)
    j.speaker.stop = lambda: None
    pending = Confirmation(question=QUESTION, kind=DELETE)
    thread, out = _confirm_in_background(j, pending)
    assert _until(lambda: pending.listening_started_at)
    _say(j, "stop")
    thread.join(timeout=5.0)
    assert not out["answer"]


@pytest.mark.parametrize("echo", ["Confirm?", "confirmed", "confirm it",
                                  "from Downloads. Confirm?", "Delete these 12 files"])
def test_his_own_question_coming_back_does_not_answer_it(speaker, echo):
    j = _jalen(speaker, timeout_s=0.6)
    pending = Confirmation(question=QUESTION, kind=DELETE)
    thread, out = _confirm_in_background(j, pending)
    assert _until(lambda: pending.listening_started_at)
    # The echo begins right as the question ends.
    _say(j, echo, began_at=pending.spoken_at + 0.2)
    thread.join(timeout=5.0)
    assert out["answer"].outcome == "timeout", f"Jalen approved its own question from {echo!r}"


# ---------------------------------------------------------------------------
# 2. The orb: waiting for him beats working
# ---------------------------------------------------------------------------
def test_the_orb_says_it_is_waiting_for_his_answer_not_thinking(speaker):
    j = _jalen(speaker)
    j._awaiting_confirmation = True
    j._refresh_orb()
    assert j.orb.states[-1] == "awaiting"
    j._awaiting_confirmation = False
    j._refresh_orb()
    assert j.orb.states[-1] == "thinking", "a turn still in flight is still working"


def test_the_orb_knows_the_awaiting_state():
    from jarvis.ui.orb import STATES, Orb

    assert "awaiting" in STATES
    assert "awaiting" in Orb(CONFIG).colours
    assert Jalen.ORB_PRIORITY.index("awaiting") < Jalen.ORB_PRIORITY.index("thinking")


# ---------------------------------------------------------------------------
# 3b. An unspoken question is not waited on
# ---------------------------------------------------------------------------
def test_a_question_that_was_never_said_is_not_waited_on(speaker):
    j = _jalen(speaker, timeout_s=20)
    j._ask_aloud = lambda text: HeldPrompt(False)
    started = time.monotonic()
    answer = asyncio.new_event_loop().run_until_complete(
        j.confirm(Confirmation(question=QUESTION, kind=TELEGRAM)))
    assert answer.outcome == "unspoken"
    assert not answer
    assert time.monotonic() - started < 2.0, "it waited for an answer to a question nobody heard"


# ---------------------------------------------------------------------------
# 4. A yes answers the question he heard, and only that one
# ---------------------------------------------------------------------------
def test_a_yes_that_began_before_the_question_was_said_does_not_approve_it(speaker):
    j = _jalen(speaker, timeout_s=0.6)
    pending = Confirmation(question=QUESTION, kind=DELETE)
    thread, out = _confirm_in_background(j, pending)
    assert _until(lambda: pending.listening_started_at)
    _say(j, "yes", began_at=pending.question_started_at - 1.5)
    thread.join(timeout=5.0)
    assert out["answer"].outcome == "timeout", "a yes older than the question approved it"
    assert any("began before the question" in str(r.get("summary", "")) for r in j.rows)


def test_a_late_yes_for_an_earlier_confirmation_does_not_approve_the_next(speaker):
    j = _jalen(speaker)
    first = Confirmation(question="Send 'hi' to Ali on Telegram. Confirm?", kind=TELEGRAM)
    second = Confirmation(question=QUESTION, kind=DELETE)
    j._answer_q.put(ConfirmAnswer("yes", "yes", confirmation_id=first.id))
    assert j._wait_for_answer_to(second, 0.3) is None
    j._answer_q.put(ConfirmAnswer("yes", "yes", confirmation_id=second.id))
    assert j._wait_for_answer_to(second, 0.3).outcome == "yes"


def test_an_answer_is_stamped_with_the_confirmation_it_answers(speaker):
    j = _jalen(speaker)
    pending = Confirmation(question=QUESTION, kind=DELETE)
    pending.question_started_at = time.monotonic() - 3
    j._confirmation = pending
    j._awaiting_confirmation = True
    j._confirm_question = QUESTION
    j._confirm_asked_at = time.monotonic() - 2.5
    _say(j, "yes")
    answer = j._answer_q.get_nowait()
    assert answer.confirmation_id == pending.id and answer.outcome == "yes"


# ---------------------------------------------------------------------------
# 5. The first word is kept
# ---------------------------------------------------------------------------
class _FakeVAD:
    """Speech is any frame whose first sample is 1.0."""

    silence_ms = 1400

    def is_speech(self, frame):
        return bool(frame[0] == 1.0)

    def reset(self):
        pass


def _collector():
    cfg = SimpleNamespace(get_path=lambda key, default=None: {
        "audio.sample_rate": 16000, "audio.frame_ms": 32, "vad.min_speech_ms": 250,
        "vad.fast_silence_ms": 320, "vad.no_speech_timeout_ms": 2500,
        "vad.max_utterance_s": 30}.get(key, default))
    return UtteranceCollector(cfg, _FakeVAD())


def _frame(speech):
    f = np.zeros(512, dtype=np.float32)
    f[0] = 1.0 if speech else 0.0
    return f


def _run(collector, frames):
    for frame in frames:
        out = collector.feed(frame)
        if out is not None:
            return out
    return None


def test_a_one_word_yes_survives_when_the_window_starts_with_its_own_beginning():
    """
    "yes" is about 256 ms of voiced speech. The frame that opened the window
    is one of them; without it the collector saw 224 ms, under the 250 ms
    minimum, and gave the answer up as no speech.
    """
    quiet_start = [_frame(False)] * 3
    trigger = _frame(True)
    rest_of_yes = [_frame(True)] * 7
    silence = [_frame(False)] * 12

    old = _collector()
    assert len(_run(old, rest_of_yes + silence + [_frame(False)] * 80)) == 0, (
        "precondition: without its first frame the answer is too short")

    new = _collector()
    new.prime(quiet_start + [trigger])
    heard = _run(new, rest_of_yes + silence)
    assert heard is not None and len(heard) > 0, "his one-word answer was dropped"
    assert len(heard) >= 512 * (len(quiet_start) + 1 + len(rest_of_yes)), (
        "the start of the word was not kept")


def test_the_microphone_loop_primes_the_answer_window():
    import inspect

    source = inspect.getsource(Jalen.run)
    opened = source.index('opened_by = "follow_up" if in_follow_up else "answer"')
    assert "prime(list(preroll))" in source[opened:opened + 1500], (
        "the answer window no longer starts with the sound that opened it")
    assert "preroll.append(frame)" in source
