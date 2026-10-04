"""
THE INDEPENDENT REVIEW OF "LISTENING", ANSWERED ONE FINDING AT A TIME.

The first change on this branch (tests/test_not_pretending_to_be_deaf.py and
tests/test_a_long_answer_is_still_an_answer.py) made Jalen act on more of what
he says without the name. A reviewer then reproduced six things at the gate.
Each is a claim, and each test below was written RED against that code first.

FINDING 1 (medium) - the doors admit speech aimed at other people.
    "Sarah, could you please send me the report" was acted on in every window,
    and so were "Helen, dinner is ready" and "Janet, can you pass me the
    salt". Verdict: REAL. Two causes. The open-vocabulary vocative ("any
    word, comma, polite request") has no signal in it that the sentence is for
    Jalen - a request that names ANOTHER person is the opposite signal - and it
    rescued 1 of the 78 refused sentences meant for him, alone. And the list of
    misheard names held 9 names that never once appeared in a refused
    sentence (they were only ever seen in sentences the wake word had already
    carried), so they cost exposure and bought nothing. Fixed by cutting the
    open-vocabulary door, keeping a listed name only if it rescued a refused
    sentence, keeping the name door out of the barge-in window, and keeping
    only the two polite forms he actually uses ("could you please" 83 times,
    "I would like you to" 31) - the other four forms are 3 sightings in all.

FINDING 2 (medium) - the echo defence was armed for 3 s after a reply BEGAN.
    A reply of 17 s read back at 5 s or 10 s into it was compared against
    nothing. Verdict: REAL, and worse than reported on one point: on the
    streamed path (every brain reply) the text of the reply being spoken is not
    known to the gate at all until it has finished, so the gate compared the
    PREVIOUS reply. Fixed at both ends: the speaker says when its voice last
    left the room, the gate keeps the text it has been handed so far, and the
    doors compare against it for as long as the voice is on air and for 12 s
    after (3 s of room, 4 s of the longest endpoint silence, 5 s of
    transcription at p95 - see app.ECHO_REACHES_THE_GATE_S).

FINDING 3 (medium) - an answer put back for more audio was refused whole.
    The gate closes the answer window as it accepts; run() then put an
    unfinished-looking sentence back; the whole sentence found the window
    shut. Verdict: REAL, reproduced by driving run() itself.

FINDING 4 (low) - "Yes, Boss?" is two words, below the echo floor, and the
    window it opens left no door row. Verdict: REAL on both counts.

FINDING 5 (low) - the run() wiring of `outlived` was pinned by source
    strings only. Verdict: REAL (the mutation survived all 209 tests); the
    tests here drive run() with a scripted microphone instead.

FINDING 6 (low) - two numbers in comments were wrong. Verdict: REAL.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import inspect
import re
import time
from pathlib import Path

import numpy as np
import pytest

from jalen import app as app_module
from jalen.app import ECHO_TAIL_S, Expectation, Jalen
from jalen.brain import router
from jalen.config import CONFIG

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# A GATE WITH A VOICE. The real methods, and a stand-in for the speaker that
# can say whether Jalen is on air and how long ago he stopped.
# ---------------------------------------------------------------------------
class _Voice:
    """What the gate may ask the speaker."""

    def __init__(self):
        self.speaking = False
        self.ended_at = None

    def quiet_for(self):
        if self.speaking:
            return 0.0
        return float("inf") if self.ended_at is None else time.monotonic() - self.ended_at


class _Gate:
    def __init__(self):
        self._awaiting_confirmation = False
        self._awaiting_stop = False
        self._awaiting_reply = False
        self._pending_rating = None
        self._last_user_text = ""
        self._last_user_at = 0.0
        self._last_reply_text = ""
        self._last_reply_at = 0.0
        self._voice_so_far = ""
        self._expecting = None
        self._answer_window_s = float(CONFIG.get_path("conversation.answer_window_s", 30))
        self._answer_overrun_s = float(CONFIG.get_path("vad.max_utterance_s", 30)) + app_module.ANSWER_STT_SLACK_S
        self.cfg = CONFIG
        self.kill_phrases = set(CONFIG.get_path("safety.kill_phrases"))
        self.speaker = _Voice()

    RATING_EXPIRES_S = Jalen.RATING_EXPIRES_S
    should_act_on = Jalen.should_act_on
    _continues_last_utterance = Jalen._continues_last_utterance
    _expectation_open = Jalen._expectation_open
    _open_expectation = Jalen._open_expectation
    _window_outlived_by_the_sentence = Jalen._window_outlived_by_the_sentence
    _rating_is_pending = Jalen._rating_is_pending
    _expect_an_answer = Jalen._expect_an_answer
    _forget_expectation = Jalen._forget_expectation
    _sounds_like_its_own_voice = Jalen._sounds_like_its_own_voice

    def acts_on(self, text, opened_by="follow_up", vouched_by=None):
        return bool(self.should_act_on(text, False, opened_by=opened_by, vouched_by=vouched_by))


# ===========================================================================
# FINDING 1. THE DOORS DO NOT ADMIT SPEECH THAT IS AIMED AT OTHER PEOPLE
# ===========================================================================
AIMED_AT_SOMEONE_ELSE = [
    # (what a person says to a person in the room, the window it lands in)
    ("Sarah, could you please send me the report", "barge"),
    ("Sarah, can you please share your screen", "barge"),
    ("Hey Sarah, will you please hold on", "follow_up"),
    ("Sarah, could you please send me the report", "follow_up"),
    ("Hey Sarah, could you please send me the report", "follow_up"),
    ("Mom, could you please call me back", "follow_up"),
    # An ordinary first name that was never once heard in a refused sentence.
    ("Janet, can you pass me the salt", "follow_up"),
    ("Janet, can you pass me the salt", "barge"),
    ("Johnny, dinner is ready", "follow_up"),
    ("Galen, come here", "follow_up"),
    # A name that IS on the list, in the windows that do not vouch for a door.
    ("Helen, dinner is ready", "barge"),
    ("Dylan, what time is the game", "barge"),
    ("Helen, dinner is ready", "answer"),
]


@pytest.mark.parametrize("heard,window", AIMED_AT_SOMEONE_ELSE)
def test_a_sentence_aimed_at_another_person_is_not_acted_on(heard, window):
    assert not _Gate().acts_on(heard, window), (heard, window)


@pytest.mark.parametrize("heard", [
    "Sarah, could you please send me the report",
    "Rijal, could you please open it?",
    "Darling, could you please go to my window",
    "Hey, child, I would like you to click that",
    "Hey, gentlemen, I would like you to keep reading",
])
def test_no_open_vocabulary_word_is_a_call(heard):
    """Any word, a comma and a polite request: nothing in it says the request is for Jalen."""
    assert router.called_by_a_misheard_name(heard) is None, heard
    assert router.widened_door(heard, "follow_up") is None, heard


@pytest.mark.parametrize("heard", [
    "Can you please close the window",
    "Will you please stop doing that",
    "Would you please be quiet",
    "I'd like you to meet my friend Sam",
])
def test_only_the_polite_forms_he_actually_uses_are_a_door(heard):
    """
    "could you please" opens 76 of his 872 acted-on utterances and 7 of the
    refused ones meant for him; "I would like you to" 26 and 5. The other four
    forms ("would you please" 1 and 1, "can you please" 1 and 0, "will you
    please" and "I'd like you to" none) are 3 sightings in 950 sentences.
    """
    assert not router.opens_with_a_request(heard), heard
    assert not _Gate().acts_on(heard, "follow_up"), heard


def test_the_two_forms_he_does_use_are_still_a_door_in_the_follow_up_window():
    for heard in ("Could you please open Chrome", "I would like you to send it."):
        assert _Gate().acts_on(heard, "follow_up"), heard
        assert not _Gate().acts_on(heard, "barge"), heard


def test_a_listed_name_is_a_door_in_the_follow_up_window_only_not_in_barge_in():
    """
    Barge-in fires on Jalen's own voice and on a cough, so it vouches for
    nothing; no refused sentence in the log with a misheard name in it is
    estimated to have come out of one. A call by his name, with a greeting, is
    a different door.
    """
    assert _Gate().acts_on("Dylan, what time is it", "follow_up")
    assert not _Gate().acts_on("Dylan, what time is it", "barge")
    assert not _Gate().acts_on("Dylan, what time is it", "")
    assert _Gate().acts_on("Open the file. Hey Jalen.", "barge")


def test_the_name_list_is_only_names_that_rescued_a_refused_sentence():
    names = router.MISHEARD_NAMES
    kept = {"helen", "ellen", "alain", "dylan", "delic", "jalit", "e.j"}
    assert names == kept, sorted(names ^ kept)
    for never_refused in ("janet", "johnny", "galen", "janine", "jolly",
                          "yellen", "dallin", "jameet", "ejjalin"):
        assert never_refused not in names, never_refused


# ===========================================================================
# FINDING 2. HIS OWN VOICE, FOR THE WHOLE TIME IT IS ON AIR AND FOR ITS TAIL
# ===========================================================================
LONG_REPLY = (
    "Here is where I got to with the research you asked for. I found the three "
    "papers on the first page and read the abstracts. Dylan, could you please "
    "wire the money to the new account today. And I would like you to check "
    "the committee address before anything is sent to them. After that the "
    "table can go in as an attachment so that nobody has to ask for it again."
)
LONG_REPLY_SECONDS = 18.0     # what 400-odd characters take at 22.4 chars a second


def _on_air(gate, began_s_ago, *, spoken_s=LONG_REPLY_SECONDS, text=LONG_REPLY, streamed=False):
    """Jalen began `text` `began_s_ago` ago and speaks for `spoken_s`."""
    now = time.monotonic()
    if streamed:
        # The streamed path: the reply is not finished, so it has not been
        # filed as the last reply; what has been handed over so far is here.
        gate._last_reply_text = "Okay."
        gate._last_reply_at = now - 600
        gate._voice_so_far = text
    else:
        gate._last_reply_text = text
        gate._last_reply_at = now - began_s_ago
    on_air = began_s_ago < spoken_s
    gate.speaker.speaking = on_air
    gate.speaker.ended_at = None if on_air else now - (began_s_ago - spoken_s)
    return gate


ECHOED_SENTENCES = [
    "Dylan, could you please wire the money to the new account today.",
    "Dylan could you please wire the money to the new account today",
    "And I would like you to check the committee address before anything is sent to them.",
    "I would like you to check the committee address before anything is sent to them",
]


@pytest.mark.parametrize("window", ["follow_up", "barge"])
@pytest.mark.parametrize("began_s_ago", [5.0, 10.0])
@pytest.mark.parametrize("streamed", [False, True])
@pytest.mark.parametrize("heard", ECHOED_SENTENCES)
def test_his_own_long_reply_is_never_a_command_while_it_is_being_spoken(heard, streamed, began_s_ago, window):
    gate = _on_air(_Gate(), began_s_ago, streamed=streamed)
    assert gate.speaker.speaking
    assert not gate.acts_on(heard, window), (
        f"{heard!r} came back through the speakers {began_s_ago:g}s into an "
        f"{LONG_REPLY_SECONDS:g}s reply and was acted on in the {window} window"
    )


@pytest.mark.parametrize("window", ["follow_up", "barge"])
@pytest.mark.parametrize("quiet_s", [0.5, 2.0, 8.0, 11.0])
@pytest.mark.parametrize("heard", ECHOED_SENTENCES)
def test_and_for_the_tail_after_it_stops_which_is_longer_than_the_pipeline_is_slow(heard, quiet_s, window):
    """
    The tail is anchored at the END of playback. A sentence that is his own
    voice reaches the gate after the endpoint silence and the transcription -
    3.2s at the median, 6s and more at p95 - so a tail of 3s from the START of
    the reply was shorter than the pipeline.
    """
    gate = _on_air(_Gate(), LONG_REPLY_SECONDS + quiet_s)
    assert not gate.speaker.speaking
    assert not gate.acts_on(heard, window), (heard, quiet_s, window)


def test_it_is_a_tail_and_not_a_gag_a_person_can_say_the_same_thing_later():
    gate = _on_air(_Gate(), LONG_REPLY_SECONDS + 14.0)
    assert gate.acts_on("Dylan, could you please wire the money to the new account today.", "follow_up")


def test_the_tail_is_derived_from_what_it_is_made_of():
    tail = app_module.ECHO_REACHES_THE_GATE_S
    patient = float(CONFIG.get_path("vad.silence_ms", 4000)) / 1000.0
    assert tail == pytest.approx(ECHO_TAIL_S + patient + app_module.ANSWER_STT_SLACK_S)
    assert tail == pytest.approx(12.0)
    src = inspect.getsource(app_module)
    at = src.index("ECHO_REACHES_THE_GATE_S =")
    assert "349" in src[max(0, at - 1500):at], "a tuned constant carries its measurement"
    # Read once, in __init__, from the shipped endpoint - the file's own rule.
    init = inspect.getsource(Jalen.__init__)
    assert "_door_echo_tail_s" in init and "vad.silence_ms" in init


def test_the_answer_to_a_question_is_not_made_harder_by_the_longer_tail():
    """
    The long tail is for the doors, which have no name to vouch for them. An
    answer repeating the question's own words ("just the last message", to
    "...or just the last message?") arrives at the gate seconds after the
    question ended, and must still be an answer.
    """
    gate = _Gate()
    q = "Do you want the whole thread read out, or just the last message?"
    now = time.monotonic()
    gate._last_reply_text = q
    gate._last_reply_at = now - 7.0
    gate.speaker.ended_at = now - 5.0
    gate._expecting = Expectation(question=q, turn_id=1, opened_at=now - 5.0, expires_at=now + 25.0)
    assert gate.acts_on("just the last message", "answer", vouched_by=gate._expecting)


def test_an_answer_during_playback_is_still_never_his_own_voice():
    """The answer path too: a window left open by the PREVIOUS question while the next reply is spoken."""
    gate = _Gate()
    now = time.monotonic()
    old_q = "Which one did you mean, the first draft or the second?"
    gate._expecting = Expectation(question=old_q, turn_id=1, opened_at=now - 20.0, expires_at=now + 10.0)
    _on_air(gate, 6.0, streamed=True)
    assert not gate.acts_on("I found the three papers on the first page and read the abstracts", "answer")


def test_the_speaker_says_when_its_voice_last_left_the_room():
    from jalen.audio.tts import Speaker

    speaker = Speaker(CONFIG)
    assert speaker.quiet_for() == float("inf"), "a speaker that never spoke has been quiet forever"
    speaker._speaking.set()
    assert speaker.quiet_for() == 0.0
    speaker._speaking.clear()
    speaker._spoke_until = time.monotonic() - 4.0
    assert speaker.quiet_for() == pytest.approx(4.0, abs=0.2)
    src = inspect.getsource(__import__("jalen.audio.tts", fromlist=["x"]))
    assert src.count("_spoke_until = time.monotonic()") >= 2, (
        "both the sentence-by-sentence and the streamed path must stamp the moment playback ends"
    )


class _Stream:
    def __init__(self):
        self.pushed = []

    def push(self, piece):
        self.pushed.append(piece)

    def close(self):
        pass

    def abandon(self):
        pass


def test_a_streamed_reply_is_remembered_as_it_is_handed_to_the_speaker():
    """
    Drives the real speak_brain_reply: while the model is still writing, the
    words handed to the speaker are known to the gate, and once the reply is
    filed they are not kept twice.
    """
    jalen = Jalen.__new__(Jalen)
    gate = _Gate()
    for name in ("_awaiting_confirmation", "_awaiting_stop", "_awaiting_reply", "_pending_rating",
                 "_last_user_text", "_last_user_at", "_expecting", "kill_phrases", "_answer_window_s",
                 "_answer_overrun_s"):
        setattr(jalen, name, getattr(gate, name))
    jalen.cfg = CONFIG
    jalen.muted = False
    jalen._last_reply_text = "Okay."
    jalen._last_reply_at = time.monotonic() - 600
    jalen._voice_so_far = ""
    jalen._turn_timer = None
    jalen.audit = _Audit()
    jalen.speaker = _Voice()
    jalen.speaker.speaking = True
    jalen.speaker.max_spoken = 700
    stream = _Stream()
    jalen.speaker.open_stream = lambda: stream
    jalen._run_coro = lambda value: value
    seen = {}

    def handle_with_brain(user_text, on_text=None):
        on_text("Here is where I got to with the research you asked for. ")
        on_text("Dylan, could you please wire the money to the new account today. ")
        seen["so_far"] = jalen._voice_so_far
        seen["acted_on"] = jalen.should_act_on(
            "Dylan, could you please wire the money to the new account today.", False,
            opened_by="follow_up")
        return "Here is where I got to with the research you asked for. Dylan, could you please wire the money to the new account today."

    jalen.handle_with_brain = handle_with_brain
    jalen.speak_brain_reply("what did you find")
    assert "wire the money" in seen["so_far"], "the reply being spoken was not known to the gate"
    assert seen["acted_on"] is False, "its own words, while it was still on air, were a command"
    assert "".join(stream.pushed).count("wire the money") == 1
    assert jalen._voice_so_far == "" and "wire the money" in jalen._last_reply_text


def test_an_announcement_in_the_middle_of_a_reply_does_not_wipe_what_is_still_on_air():
    jalen = Jalen.__new__(Jalen)
    jalen._voice_so_far = "the first half of a long answer"
    jalen._note_said("I'm about to send that. Confirm?")
    assert jalen._voice_so_far == "the first half of a long answer"
    assert jalen._last_reply_text == "I'm about to send that. Confirm?"


# ===========================================================================
# FINDING 3. AN ANSWER THAT IS PUT BACK FOR MORE AUDIO MUST STILL BE AN ANSWER
#            Driven through the real Jalen.run() with a scripted microphone.
# ===========================================================================
class _Mic:
    dropped = 0

    def __init__(self, frames):
        self._n = frames

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def frames(self):
        for _ in range(self._n):
            yield np.zeros(512, dtype=np.float32)

    def queued_seconds(self):
        return 0.0

    def drain(self):
        pass


class _Vad:
    threshold = 0.5

    def load(self):
        pass

    def probability(self, frame):
        return 1.0


class _Wake:
    def __init__(self, fires=0):
        """`fires`: how many times the wake word fires, on the first frames - 0 for never."""
        self.fires = fires

    def load(self):
        pass

    def feed(self, frame):
        if self.fires > 0:
            self.fires -= 1
            return True
        return False


class _Orb:
    def __init__(self):
        self.states = []

    def start(self):
        pass

    def set_state(self, state):
        self.states.append(state)

    def set_level(self, level):
        pass

    def flash(self, *a, **k):
        pass


class _Audit:
    def __init__(self):
        self.rows = []

    def write(self, kind, **fields):
        self.rows.append((kind, fields))

    def error(self, *a, **k):
        pass

    def utterance(self, *a, **k):
        pass


class _Speaker:
    """Silent, or - with `on_air` - talking until something stops it (barge-in)."""

    def __init__(self, on_air=False):
        self.speaking = on_air
        self.ended_at = None

    def speaking_for(self):
        return 5.0 if self.speaking else 0.0

    def stop(self):
        self.speaking = False
        self.ended_at = time.monotonic()

    def quiet_for(self):
        if self.speaking:
            return 0.0
        return float("inf") if self.ended_at is None else time.monotonic() - self.ended_at


class Heard:
    """One utterance the collector hands over, and what recognition makes of it."""

    def __init__(self, text, patient=False, takes=0.0):
        """`takes`: seconds that pass while he says it, before the collector hands it over."""
        self.text = text
        self.patient = patient
        self.takes = takes


class _Collector:
    sample_rate = 16000
    patient_silence_ms = 4000
    fast_silence_ms = 1400
    was_patient = False

    def __init__(self, script, clock):
        self.script = list(script)
        self.clock = clock
        self.current = None
        self.resumed = 0

    def _reset(self):
        pass

    def feed(self, frame):
        if not self.script:
            return None
        self.current = self.script.pop(0)
        self.clock.now += self.current.takes
        self.was_patient = self.current.patient
        return np.zeros(8000, dtype=np.float32)

    def resume(self, utterance):
        self.resumed += 1


class _Stt:
    last_engine = "fake"

    def __init__(self, collector):
        self._collector = collector

    def transcribe(self, utterance):
        return self._collector.current.text


class _Clock:
    def __init__(self, start=100_000.0):
        self.now = start

    def __call__(self):
        return self.now


class Room:
    """
    Jalen.run() with everything it touches replaced by a fake: scripted
    utterances come in, and what it dispatched (and what it wrote down) goes
    out. The clock is a number the test moves.
    """

    def __init__(self, monkeypatch, script, *, barge=False, speaker=None, sync_turns=False,
                 wake_first=False):
        """
        `barge`: Jalen is mid-sentence when the microphone hears him, so the
        window that opens is the barge-in one - the only way, in this harness,
        to get a window with no question open behind it.

        `wake_first`: the wake word fires on the first frame, so the first
        utterance is one he began with "hey Jalen" - the way, with `sync_turns`,
        to get a follow-up window behind it.

        `speaker`: a speaker of the test's own, or a function that is given the
        room's clock and returns one (a speaker with a past - see
        test_his_own_voice_is_judged_when_it_began.OnAir).

        `sync_turns`: run each turn the loop dispatches straight away on this
        thread, with the turn's own work (process, playback, the rating) left
        out. The ONLY way to get a follow-up window in this harness, because
        `follow_up_until` is a local variable of run() that dispatch_turn sets
        when a turn finishes, and the fake Thread used to never run it.
        """
        frames_extra = 8 if barge else 0
        self.clock = _Clock()
        monkeypatch.setattr(time, "monotonic", self.clock)
        monkeypatch.setattr(app_module.runtime, "stop_requested", lambda: False)
        monkeypatch.setattr(app_module.runtime, "take_signal", lambda: None)
        self.dispatched = []
        self.processed = []
        room = self

        class _Thread:
            def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
                self.target = target
                self.args = args

            def start(self):
                room.dispatched.append(self.args)
                if sync_turns and self.target is not None:
                    self.target(*self.args)

        monkeypatch.setattr(app_module.threading, "Thread", _Thread)
        if sync_turns:
            import contextlib

            monkeypatch.setattr(app_module.systools, "com_initialized",
                                lambda: contextlib.nullcontext())
            frames_extra += 2 * len(script)

        jalen = Jalen.__new__(Jalen)
        self.audit = _Audit()
        self.collector = _Collector(script, self.clock)
        jalen.cfg = CONFIG
        jalen.running = type("E", (), {"set": lambda s: None})()
        jalen.orb = _Orb()
        jalen.wake = _Wake(fires=1 if wake_first else 0)
        jalen.vad = _Vad()
        jalen.mic = _Mic(len(script) + 1 + frames_extra)
        jalen.audit = self.audit
        jalen.collector = self.collector
        jalen.stt = _Stt(self.collector)
        if callable(speaker) and not hasattr(speaker, "quiet_for"):
            speaker = speaker(self.clock)
        jalen.speaker = speaker if speaker is not None else _Speaker(on_air=barge)
        jalen.session_id = "test"
        jalen.muted = False
        jalen.paused = False
        jalen.prewarm = lambda: None
        jalen._refresh_orb = lambda *a, **k: None
        jalen._announce_finished_jobs = lambda: None
        jalen._quit = type("E", (), {"is_set": lambda s: False})()
        jalen.kill = type("E", (), {"is_set": lambda s: False})()
        jalen._exit_reason = None
        jalen._turn_timer = None
        import threading as _t

        jalen._turn_lock = _t.Lock()
        jalen._turn_seq = 0
        jalen._active_turns = set()
        jalen._awaiting_confirmation = False
        jalen._awaiting_stop = False
        jalen._awaiting_reply = False
        jalen._pending_rating = None
        jalen._expecting = None
        jalen._last_user_text = ""
        jalen._last_user_at = 0.0
        jalen._last_reply_text = ""
        jalen._last_reply_at = 0.0
        jalen._voice_so_far = ""
        jalen._answer_window_s = float(CONFIG.get_path("conversation.answer_window_s", 30))
        jalen._answer_overrun_s = float(CONFIG.get_path("vad.max_utterance_s", 30)) + app_module.ANSWER_STT_SLACK_S
        jalen._follow_up_s = float(CONFIG.get_path("conversation.follow_up_timeout_s", 12))
        jalen.kill_phrases = set(CONFIG.get_path("safety.kill_phrases"))
        if sync_turns:
            jalen.process = lambda text, from_him=True, door="": self.processed.append(
                (text, from_him, door))
            jalen._await_playback = lambda *a, **k: None
            jalen._finish_timing = lambda *a, **k: None
            jalen._check_the_plan = lambda *a, **k: None
            jalen._maybe_ask_for_a_rating = lambda *a, **k: None
        self.jalen = jalen

    # -- what Jalen asked ----------------------------------------------------
    def asked(self, question, *, window_s=30.0):
        """He asked `question` and the window is open now. Returns the Expectation."""
        now = self.clock.now
        self.jalen._last_reply_text = question
        self.jalen._last_reply_at = now - 600          # long ago: not an echo
        self.jalen._expecting = Expectation(
            question=question, turn_id=1, opened_at=now - 600, expires_at=now + window_s)
        return self.jalen._expecting

    def run(self):
        self.jalen.run()
        return self

    # -- what came out ---------------------------------------------------------
    def texts(self):
        return [args[0] for args in self.dispatched]

    def from_him(self):
        return [args[3] for args in self.dispatched]

    def rows(self, starts_with):
        return [f for _, f in self.audit.rows if f.get("summary", "").startswith(starts_with)]

    def ignored(self):
        return self.rows("ignored")

    def doors(self):
        return [r["summary"] for r in self.rows("acted on without his name")]


QUESTION = "Which one did you mean, the first draft or the one you saved last night?"
UNFINISHED = "I want the first draft and"
WHOLE = "I want the first draft and send it to the committee with the table attached"


def test_the_room_itself_works_an_ordinary_answer_reaches_a_turn(monkeypatch):
    """Before anything is claimed from it: the harness drives the real loop."""
    room = Room(monkeypatch, [Heard("the first draft", patient=True)])
    room.asked(QUESTION)
    room.run()
    assert room.texts() == ["the first draft"]
    assert room.from_him() == [False], "an answer admitted without his name must not certify that it was him"
    assert room.ignored() == []


def test_an_answer_cut_at_the_fast_endpoint_is_acted_on_when_the_whole_sentence_arrives(monkeypatch):
    """
    FINDING 3. The first pass is accepted, closes the window, and is put back
    because it ends on "and"; the second pass is the whole sentence. It used to
    meet a window that the first pass had closed, and was refused.
    """
    room = Room(monkeypatch, [Heard(UNFINISHED, patient=False), Heard(WHOLE, patient=True)])
    room.asked(QUESTION)
    room.run()
    assert room.collector.resumed == 1, "the fragment should have been put back for more audio"
    assert room.texts() == [WHOLE], (
        f"the whole answer never reached a turn: dispatched {room.texts()!r}, "
        f"ignored {[r['detail']['heard'] for r in room.ignored()]!r}"
    )
    assert room.ignored() == []


def test_the_same_when_the_window_closed_while_he_was_still_talking(monkeypatch):
    """The 'outlived' case, the one the headline fix of the first change was for."""
    room = Room(monkeypatch, [
        Heard(UNFINISHED, patient=False, takes=33),     # the thirty seconds run out while he talks
        Heard(WHOLE, patient=True),
    ])
    room.asked(QUESTION, window_s=30.0)
    room.run()
    assert room.collector.resumed == 1
    assert room.texts() == [WHOLE], room.texts()


def test_a_fragment_nobody_would_have_acted_on_is_not_put_back(monkeypatch):
    """
    The put-back must not widen what the microphone holds on to: television
    that ends on "and" is refused as it always was, not extended by four
    seconds and transcribed twice first.
    """
    room = Room(monkeypatch, [Heard("so then the neighbours said and", patient=False)], barge=True)
    room.run()
    assert room.collector.resumed == 0
    assert room.texts() == []
    assert len(room.ignored()) == 1


def test_asking_the_gate_whether_he_would_be_heard_does_not_use_up_the_window():
    gate = _Gate()
    now = time.monotonic()
    gate._expecting = Expectation(question=QUESTION, turn_id=1, opened_at=now - 600, expires_at=now + 20)
    assert gate.should_act_on(UNFINISHED, False, opened_by="answer", consume=False)
    assert gate._expecting is not None, "a question that was only asked about is still open"
    assert gate.should_act_on(UNFINISHED, False, opened_by="answer")
    assert gate._expecting is None, "the real call closes it, one answer and then shut"


# ===========================================================================
# FINDING 4. "Yes, Boss?" - two words, and a window with no door row
# ===========================================================================
class _Waker:
    """_say_yes_and_listen on the real Expectation code."""

    def __init__(self):
        self.said = []
        self._turn_seq = 7
        self._last_reply_text = ""
        self._last_reply_at = 0.0
        self._expecting = None
        self._answer_window_s = 30.0
        self._follow_up_s = float(CONFIG.get_path("conversation.follow_up_timeout_s", 12))
        self.speaker = _Voice()

    def say(self, text, **_):
        self.said.append(text)
        self._last_reply_text = text
        self._last_reply_at = time.monotonic()
        self.speaker.ended_at = time.monotonic()

    def _await_playback(self, *a, **k):
        pass

    def _refresh_orb(self, *a, **k):
        pass

    _expect_an_answer = Jalen._expect_an_answer
    _say_yes_and_listen = Jalen._say_yes_and_listen


@pytest.mark.parametrize("echo", ["Yes, boss.", "Yes boss", "yes, Boss?", "Yes, Boss? Yes, Boss?"])
def test_his_own_yes_boss_coming_back_is_not_an_answer(echo):
    gate = _Gate()
    waker = _Waker()
    waker._say_yes_and_listen()
    gate._expecting = waker._expecting
    gate._last_reply_text, gate._last_reply_at = waker._last_reply_text, waker._last_reply_at
    gate.speaker.ended_at = time.monotonic() - 1.0
    assert not gate.acts_on(echo, "answer", vouched_by=gate._expecting), echo


@pytest.mark.parametrize("quiet_s", [1.0, 6.0, 11.0])
def test_yes_boss_said_back_is_refused_for_as_long_as_the_pipeline_can_take_to_deliver_it(quiet_s):
    """
    Not only within the answer path's 3s: no answer to "Yes, Boss?" is ever
    "Yes, Boss?", so the two-word rule looks back as far as the doors do.
    """
    gate = _Gate()
    now = time.monotonic()
    gate._last_reply_text, gate._last_reply_at = "Yes, Boss?", now - quiet_s - 1.0
    gate.speaker.ended_at = now - quiet_s
    gate._expecting = Expectation(question="Yes, Boss?", turn_id=7, opened_at=now - quiet_s,
                                  expires_at=now - quiet_s + 12.0, kind="bare-wake")
    assert not gate.acts_on("Yes, boss.", "answer", vouched_by=gate._expecting)


def test_a_real_answer_to_yes_boss_is_still_acted_on():
    gate = _Gate()
    waker = _Waker()
    waker._say_yes_and_listen()
    gate._expecting = waker._expecting
    gate._last_reply_text, gate._last_reply_at = waker._last_reply_text, waker._last_reply_at
    gate.speaker.ended_at = time.monotonic() - 1.0
    for heard in ("open Chrome", "what time is it", "yes", "yes please open Chrome"):
        gate._expecting = waker._expecting
        assert gate.acts_on(heard, "answer", vouched_by=gate._expecting), heard


def test_a_short_answer_to_an_option_question_is_not_taken_for_an_echo():
    """The 3-word floor stays: only a reply of one or two words can be repeated whole."""
    gate = _Gate()
    q = "ChatGPT or Gemini?"
    now = time.monotonic()
    gate._last_reply_text, gate._last_reply_at = q, now - 2
    gate.speaker.ended_at = now - 1.5
    gate._expecting = Expectation(question=q, turn_id=1, opened_at=now - 1.5, expires_at=now + 28)
    assert gate.acts_on("ChatGPT", "answer", vouched_by=gate._expecting)


def test_an_answer_through_the_bare_wake_window_is_counted_under_its_own_door(monkeypatch):
    room = Room(monkeypatch, [Heard("open the second one", patient=True)])
    waker = _Waker()
    waker._say_yes_and_listen()
    # Same clock as the room: the Expectation was stamped with the real one.
    now = room.clock.now
    room.jalen._expecting = dataclasses.replace(
        waker._expecting, opened_at=now - 600, expires_at=now + 12.0)
    room.jalen._last_reply_text = "Yes, Boss?"
    room.jalen._last_reply_at = now - 600
    room.run()
    assert room.texts() == ["open the second one"]
    assert room.doors() == ["acted on without his name - bare-wake-window"], (
        "the largest new exposure (twelve seconds with no name after a wake word "
        "that heard nothing) could not be counted from the log"
    )


def test_an_ordinary_answer_leaves_no_door_row(monkeypatch):
    """Not every sentence admitted without the name is a 'door': an answer to a real question is free."""
    room = Room(monkeypatch, [Heard("the first draft", patient=True)])
    room.asked(QUESTION)
    room.run()
    assert room.doors() == []


def test_the_rows_say_which_window_opened_the_microphone_and_what_was_open(monkeypatch):
    """
    CLAUDE.md's two-gates invariant: `opened_by` and `vouched_by` in the audit
    rows record which window opened the microphone ("wake", "follow_up",
    "answer" or "barge") and which question of Jalen's, if any, was open when
    the sound began ("" for none, "question", or "bare-wake").
    """
    # A refusal, over barge-in, with nothing open.
    room = Room(monkeypatch, [Heard("so then the neighbours said", patient=True)], barge=True)
    room.run()
    (row,) = room.ignored()
    assert (row["detail"]["opened_by"], row["detail"]["vouched_by"]) == ("barge", "")

    # Nothing came back from recognition, with a question open.
    room = Room(monkeypatch, [Heard("", patient=True)])
    room.asked(QUESTION)
    room.run()
    (row,) = room.rows("heard nothing")
    assert row["detail"]["stage"] == "empty-transcript"
    assert (row["detail"]["opened_by"], row["detail"]["vouched_by"]) == ("answer", "question")

    # A long answer, after its window closed.
    room = Room(monkeypatch, [Heard(LONG_NAMED_ANSWER, patient=True, takes=33)])
    room.asked(QUESTION, window_s=30.0)
    room.run()
    (row,) = room.rows("acted on without his name")
    assert (row["detail"]["opened_by"], row["detail"]["vouched_by"]) == ("answer", "question")


def test_the_vouching_words_are_the_ones_the_commit_and_claude_md_use():
    vouching = Jalen._vouching
    assert vouching(None) == ""
    assert vouching(Expectation(question="q", turn_id=1, opened_at=0.0, expires_at=1.0)) == "question"
    assert vouching(Expectation(question="q", turn_id=1, opened_at=0.0, expires_at=1.0,
                                kind="bare-wake")) == "bare-wake"
    claude_md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "opened_by" in claude_md and "vouched_by" in claude_md


# ===========================================================================
# FINDING 5. run() IS DRIVEN, NOT READ - so a wiring mistake fails a test
# ===========================================================================
LONG_NAMED_ANSWER = (
    "Dylan, Helen and Sam, because they were all on the thread about the "
    "budget last week and I want them to see the table before it goes to the "
    "committee, and then we can talk about who sends it"
)


def test_a_long_answer_that_began_in_the_window_reaches_the_turn_exactly_as_said(monkeypatch):
    """
    It begins with a name on the list and has outlived its window. It is an
    ANSWER, so the name is not taken off it and the row says how it got in.
    Replacing `outlived` in run() with False survives every source-string test
    of the first change; it does not survive this one.
    """
    room = Room(monkeypatch, [Heard(LONG_NAMED_ANSWER, patient=True, takes=33)])
    room.asked(QUESTION, window_s=30.0)
    room.run()
    assert room.texts() == [LONG_NAMED_ANSWER], room.texts()
    assert room.from_him() == [False]
    assert room.doors() == ["acted on without his name - after-the-window-closed"], room.doors()


def test_the_same_words_with_no_question_open_are_refused(monkeypatch):
    room = Room(monkeypatch, [Heard(LONG_NAMED_ANSWER, patient=True)], barge=True)
    room.run()
    assert room.texts() == []
    assert len(room.ignored()) == 1


READ_BACK = "Open the committee folder and then sign me in to ChatGPT. Hey Jalen."


def test_over_barge_in_his_own_long_reply_coming_back_is_not_a_command_in_the_loop(monkeypatch):
    """
    The loop form of finding 2: he is ten seconds into a long reply that quotes
    an instruction back ("Open the committee folder... Hey Jalen.") when the
    microphone hears it, barge-in cuts him off, and the sentence arrives. It
    has his name last, with a greeting, so it is a door; it is also his own
    voice, so the door is shut.
    """
    room = Room(monkeypatch, [Heard(READ_BACK, patient=True)], barge=True)
    room.jalen._last_reply_text = (
        "Here is the plan so far. You said: " + READ_BACK + ". I will start with the sign in.")
    room.jalen._last_reply_at = room.clock.now - 10.0
    room.run()
    assert room.texts() == [], "his own voice, ten seconds into a reply, was acted on"
    (row,) = room.ignored()
    assert row["detail"]["opened_by"] == "barge"


def test_the_same_words_over_barge_in_from_a_person_are_acted_on(monkeypatch):
    """The control: with nothing like it on air, a call by his name over barge-in is his."""
    room = Room(monkeypatch, [Heard(READ_BACK, patient=True)], barge=True)
    room.jalen._last_reply_text = "Your battery is at forty percent."
    room.jalen._last_reply_at = room.clock.now - 10.0
    room.run()
    assert room.texts() == [READ_BACK]
    assert room.doors() == ["acted on without his name - greeting-last"]


def test_a_listed_name_over_barge_in_is_not_a_door_in_the_loop_either(monkeypatch):
    """What the gate-level tests say, said through the real loop: barge-in vouches for nothing."""
    room = Room(monkeypatch, [Heard("Dylan, what time is it", patient=True)], barge=True)
    room.run()
    assert room.texts() == []
    (row,) = room.ignored()
    assert row["detail"]["opened_by"] == "barge"


# ===========================================================================
# FINDING 6. TWO NUMBERS IN COMMENTS
# ===========================================================================
def test_the_comments_quote_the_measured_numbers():
    config = (ROOT / "config" / "jalen.yaml").read_text(encoding="utf-8")
    assert "35 words or more" not in config
    assert "33 of the 78" not in inspect.getsource(Jalen.should_act_on)
    assert "33 of the 78" not in (ROOT / "jalen" / "brain" / "router.py").read_text(encoding="utf-8")


# ===========================================================================
# THE CORPUS. Skipped on a machine without the real log; see
# $JALEN_AUDIT_LOG. Everything here is a number from data/audit.jsonl.
# ===========================================================================
def _measure():
    path = ROOT / "scripts" / "measure_address_gate.py"
    spec = importlib.util.spec_from_file_location("measure_address_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _real_log():
    module = _measure()
    log = module.audit_log_path()
    if not log.exists():
        pytest.skip("no data/audit.jsonl on this machine")
    rows = module.load(log)
    results = module.replay(rows)
    if len(results) < len(module.LABELS):
        pytest.skip("the log has been trimmed below the 105 labelled rows")
    return module, rows, results[: len(module.LABELS)]


# ---------------------------------------------------------------------------
# THE REPLAY AND THE ECHO MEASUREMENT, on rows made up here - so they are
# tested on a machine with no log, and a change to the script cannot quietly
# change the numbers the commit quotes.
# ---------------------------------------------------------------------------
def _made_up(*events):
    """(seconds, kind, summary, detail) -> audit rows in one session, from a fixed start."""
    import json
    from datetime import datetime, timedelta, timezone

    t0 = datetime(2026, 10, 1, 6, 0, 0, tzinfo=timezone.utc)
    return [{"ts": (t0 + timedelta(seconds=at)).isoformat(), "kind": kind, "tier": None, "tool": None,
             "summary": summary, "outcome": None, "origin": "user",
             "detail": json.dumps(detail), "session_id": "s1"}
            for at, kind, summary, detail in events]


def test_the_replay_puts_a_refused_sentence_in_the_follow_up_window_when_the_estimate_cannot_place_it():
    """
    The microphone cannot have opened outside a window a sound opened, and the
    timing estimate errs LATE for a long sentence: 17 of the 105 real rows came
    out 12.7 to 20 seconds after the reply by it. Left as "no window" they were
    refused for every exemption that needs one, and the replay under-counted the
    name door by 3 rows for a reason that lived in the estimate.
    """
    module = _measure()
    rows = _made_up(
        (0, "utterance", "Done.", {"who": "jarvis"}),
        (30, "system", "ignored - not addressed to Jalen", {"heard": "Dylan, what time is it"}),
    )
    (result,) = module.replay(rows)
    assert result["opened_by"] == "follow_up" and result["accepted"], result


def test_the_replay_gives_a_sentence_that_began_with_a_barge_in_row_to_the_barge_in_window():
    module = _measure()
    rows = _made_up(
        (0, "utterance", "Here is a long answer that goes on and on.", {"who": "jarvis"}),
        (2, "system", "barge-in stopped playback after 1.5s", {}),
        (8, "system", "ignored - not addressed to Jalen", {"heard": "Helen, dinner is ready"}),
    )
    (result,) = module.replay(rows)
    assert result["opened_by"] == "barge", result
    assert not result["accepted"], "a misheard name is not a door over barge-in"


def test_a_barge_in_row_that_the_sentence_did_not_begin_with_is_not_its_window():
    module = _measure()
    rows = _made_up(
        (0, "utterance", "Here is a long answer that goes on and on.", {"who": "jarvis"}),
        (2, "system", "barge-in stopped playback after 1.5s", {}),
        (30, "system", "ignored - not addressed to Jalen", {"heard": "Helen, dinner is ready"}),
    )
    (result,) = module.replay(rows)
    assert result["opened_by"] == "follow_up", result


def test_the_echo_measurement_on_a_reply_made_up_here():
    module = _measure()
    rows = _made_up((0, "utterance", LONG_REPLY, {"who": "jarvis"}))
    spoken = module.echo_while_speaking(rows)
    assert spoken["replies"] == 1 and spoken["long"] == 1
    assert spoken["tried"] > 0 and spoken["on_air"] > 0, spoken
    assert spoken["admitted"] == 0, spoken["leaked"]


def test_the_answer_path_measurement_runs_on_a_question_made_up_here():
    module = _measure()
    rows = _made_up((0, "utterance", "Do you want the whole thread read out, or just the last message?",
                     {"who": "jarvis"}))
    # The sound began 0.5s after he stopped and reached the gate 1s after, 3.7s after: both inside the tail.
    for gate_s in (1.0, 3.7):
        asked, took = module.answer_path_echo(rows, 0.5, gate_s)
        assert asked == 3 and took == 0, (gate_s, asked, took)
    # The same words, begun after the tail: an answer (see test_his_own_voice_is_judged_when_it_began).
    asked, took = module.answer_path_echo(rows, 3.5, 6.7)
    assert asked == 3 and took == 3


def test_the_real_log_a_long_reply_read_back_at_5s_and_10s_is_never_acted_on():
    """
    The corpus form of finding 2. Every one of Jalen's 823 replies in the log
    is fed back the way the speakers return it - whole, from its middle, its
    last five words and every sentence of it - at 5s and at 10s after it began,
    at half way, and at 2s and 8s after it stopped (a reply that takes 12s or
    more to say, which is 157 of them at the measured 22.4 characters a second,
    is still being spoken at 5s and 10s; a shorter one is in its tail).
    Whatever a door would admit, the gate must refuse.
    """
    module, rows, _ = _real_log()
    from jalen.app import SPOKEN_CHARS_PER_SECOND

    Gate = module._make_gate_class()
    sentence = re.compile(r"(?<=[.!?])\s+")
    replies = [r["summary"] for r in rows
               if r["kind"] == "utterance" and module._details(r).get("who") != "user"]
    tried = admitted = still_speaking = 0
    leaked = []
    with module._Clock() as clock:
        clock.now = 1_000_000.0
        for reply in replies:
            spoken_s = len(reply) / SPOKEN_CHARS_PER_SECOND
            words = reply.split()
            forms = {reply, " ".join(words[len(words) // 2:]), " ".join(words[-5:]), *sentence.split(reply)}
            for began_s_ago in (5.0, 10.0, spoken_s / 2, spoken_s + 2.0, spoken_s + 8.0):
                for form in forms:
                    if len(form.split()) < 3:
                        continue
                    for window in ("follow_up", "barge"):
                        # Only what a DOOR would admit is at stake here: the rest
                        # is refused for lacking his name whatever the echo test says.
                        if router.widened_door(form, window) is None:
                            continue
                        gate = Gate()
                        gate.speaker = _Voice()
                        gate._voice_so_far = ""
                        gate._last_reply_text = reply
                        gate._last_reply_at = clock.now - began_s_ago
                        gate.speaker.speaking = began_s_ago < spoken_s
                        gate.speaker.ended_at = None if gate.speaker.speaking else clock.now - (began_s_ago - spoken_s)
                        tried += 1
                        still_speaking += gate.speaker.speaking
                        if gate.should_act_on(form, False, opened_by=window):
                            admitted += 1
                            leaked.append((round(began_s_ago, 1), window, form[:70]))
    assert tried > 0 and still_speaking > 0, (
        "no sentence of any reply is one a door would admit while it is being "
        "spoken - the test proves nothing")
    assert admitted == 0, f"{admitted} of {tried} door-shaped echoes acted on: {leaked[:5]}"


def test_the_real_log_a_door_admits_none_of_the_27_rows_that_were_not_for_him():
    module, rows, results = _real_log()
    other = [r["text"] for r in results if r["label"] in ("A", "B")]
    assert len(other) == 27
    for text in other:
        for window in ("follow_up", "answer", "barge", ""):
            assert router.widened_door(text, window) is None, (text, window)


def test_the_real_log_every_listed_name_rescued_a_refused_sentence_meant_for_him():
    module, rows, results = _real_log()
    rescued = set()
    for r in results:
        if r["label"] != "F":
            continue
        found = router._VOCATIVE_AT_THE_START.match(r["text"].translate(router._FLAT_QUOTES))
        if found and found.group(1).lower() in router.MISHEARD_NAMES \
                and not router.addressed_to_jalen(r["text"]):
            rescued.add(found.group(1).lower())
    assert rescued == set(router.MISHEARD_NAMES), sorted(set(router.MISHEARD_NAMES) ^ rescued)


def test_the_real_log_the_comment_about_the_five_long_answers_is_right():
    module, rows, results = _real_log()
    words = sorted(len(r["text"].split()) for r in results if r["rescued"])
    assert words and words[0] >= 36, words
    assert f"{words[0]} words" in (ROOT / "config" / "jalen.yaml").read_text(encoding="utf-8")


def test_the_real_log_every_door_that_is_kept_earns_its_place_and_lets_in_none_of_the_27():
    """
    The decision the commit reports, as numbers that fail if the doors move:
    each door rescues sentences meant for him that nothing else does, and none
    of them is the only way in for an ambiguous or a background row.
    """
    module, rows, results = _real_log()
    counts = module.door_counts(rows, results)
    assert set(counts) == {"misheard-name", "greeting-last", "polite-request", "no door at all"}
    for door in module.DOORS:
        assert len(counts[door]["alone"]) >= 3, (door, counts[door]["alone"])
        assert sum(counts[door]["false"].values()) == 0, (door, counts[door]["false"])
    # What the windows alone admit, and what the doors add to it: 43 + 13 = 56.
    assert counts["no door at all"]["F"] == 43
    for_him = sum(r["accepted"] for r in results if r["label"] == "F")
    assert for_him - counts["no door at all"]["F"] == 13


def test_the_real_log_the_script_and_the_test_agree_that_none_of_his_own_voice_gets_through():
    module, rows, _ = _real_log()
    spoken = module.echo_while_speaking(rows)
    assert spoken["replies"] > 700 and spoken["long"] >= 100
    assert spoken["on_air"] > 0, "nothing was asked while he was still on air"
    assert spoken["admitted"] == 0, spoken["leaked"][:5]


# The answer path's own tail used to be pinned HERE as an unfixed defect
# (test_the_real_log_the_answer_paths_own_tail_is_measured_and_said_to_be_unfixed:
# 718 of 718 question echoes taken for an answer once 3.5s had passed). It is
# fixed - the gate is told when the sound began - and the corpus tests that say
# so are in tests/test_his_own_voice_is_judged_when_it_began.py.
