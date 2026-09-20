"""
Who Jalen is allowed to obey.

His complaint, and it is a good one:

    "It should not respond to its own voice, or any other noise that is
     happening or disrupting the flow of AI, if necessary, we should make,
     it should only respond to messages starting with either Jalen or
     Hey Jalen."

WHY THE OLD DEFENCE COULD NOT WORK
----------------------------------
Everything upstream of this gate is acoustic: a voice-activity detector
asking "is that speech?". The television is speech. A podcast is speech.
Jalen's own reply, arriving back through the speakers a few hundred
milliseconds later, is speech — and with no acoustic echo cancellation on
this machine, it is speech at a volume indistinguishable from his.

So a follow-up window opened by ANY sound was a window opened by the
television, and the measured consequence was in data/audit.jsonl: "I didn't
catch that" was 30 of the 255 things Jalen said, 11.8% of its entire spoken
output, most of it addressed to nobody.

WHAT REPLACED IT
----------------
By the time this gate runs, the audio has become a SENTENCE. That moves the
question from volume to words, and words can answer it: a sentence that does
not begin with his name was not addressed to him. No model, no threshold,
and nothing a loud room can defeat.

Three ways through, and the middle one is the one he chose when asked:

    the wake word fired      the name is in the AUDIO, not the transcript
    Jalen just asked him     answering "which file?" must not require the
                             name again
    it starts with the name  everything else
"""
from __future__ import annotations

import pytest

from jarvis.app import Jalen
from jarvis.brain.router import addressed_to_jalen
from jarvis.config import CONFIG


class _Gate:
    """
    Just enough Jalen to answer the question. Constructing a real one opens
    a microphone, a speaker and a Tk window; the predicate reads three
    booleans and a string.
    """

    def __init__(self, *, confirmation=False, stop=False, reply=False):
        self._awaiting_confirmation = confirmation
        self._awaiting_stop = stop
        self._awaiting_reply = reply
        self._pending_rating = None
        self._last_user_text = ""
        self._last_user_at = 0.0
        # NOTHING ASKED. Every test in this file is about the gate with no
        # question open, which is why they can all say "the name is
        # required" and mean it. The case where Jalen HAS just asked him
        # something lives in tests/test_answering_a_question.py, and
        # leaving these unset here would make that the untested state
        # rather than the deliberate one.
        self._expecting = None
        self._last_reply_text = ""
        self._last_reply_at = 0.0
        self._answer_window_s = float(
            CONFIG.get_path("conversation.answer_window_s", 30))
        self.cfg = CONFIG
        # The real list, not a stand-in. The emergency stop is exempt from
        # the gate (see tests/test_stop_and_quit.py), and a fake list here
        # would let this file pass while the exemption was broken.
        self.kill_phrases = set(CONFIG.get_path("safety.kill_phrases"))

    should_act_on = Jalen.should_act_on
    _continues_last_utterance = Jalen._continues_last_utterance
    _expectation_open = Jalen._expectation_open
    _rating_is_pending = Jalen._rating_is_pending
    RATING_EXPIRES_S = Jalen.RATING_EXPIRES_S
    _sounds_like_its_own_voice = Jalen._sounds_like_its_own_voice


# ---------------------------------------------------------------------------
# The name itself, in every pronunciation he or anyone else might produce.
# ---------------------------------------------------------------------------
ADDRESSED = [
    "Jalen, what's the time",
    "jalen what's the time",
    "Hey Jalen, open Chrome",
    "hey jalen open chrome",
    "OK Jalen, summarise my emails",
    "Okay Jalen stop",
    "Jalen?",
    "Jalen.",
    "Jalen",
    # The stress he actually uses — ja-LEN, not JA-len — and what a
    # transcriber does with it.
    "Jaylen, quit",
    "Jaylin, what can you do",
    "Jalin open telegram",
    "Jaelen stop",
    "Jailen, read my email",
    "Jaleen what's on my calendar",
    "Jhalen quit",
    # The old name still answers.
    "Jarvis, stop",
    "hey jarvis open chrome",
    "Jervis quit",
]

NOT_ADDRESSED = [
    # Ordinary speech in the room.
    "what's the time",
    "so anyway the weather is nice",
    "yeah I think so too",
    "no I told him that already",
    "can you pass me that",
    # Jalen's own voice coming back through the speakers. THIS is the one
    # that was interrupting him mid-answer.
    "Sure, I can open that for you.",
    "I didn't catch that.",
    "You have four unread emails.",
    "I'm still on the last one, give me a second.",
    "Done. I verified the result against your requirements.",
    # Somebody else's name that merely sounds close.
    "Julian said he'd call back",
    "I was talking to Juliana about it",
    "jalapeno poppers",
    "he threw the javelin",
    "Jolene is a good song",
]


@pytest.mark.parametrize("text", ADDRESSED)
def test_his_name_in_any_pronunciation_gets_through(text):
    assert addressed_to_jalen(text), f"{text!r} is addressed to him"


@pytest.mark.parametrize("text", NOT_ADDRESSED)
def test_everything_else_is_ignored(text):
    assert not addressed_to_jalen(text), (
        f"{text!r} would wake Jalen - this is the noise complaint"
    )


# ---------------------------------------------------------------------------
# The gate, with the rest of the state that can override it.
# ---------------------------------------------------------------------------
def test_the_wake_word_does_not_need_the_name_again():
    """
    "Hey Jalen" fires the wake model on the AUDIO. Whisper then transcribes
    only what came after it, so requiring the name in the transcript would
    reject every single wake-word turn — the assistant would answer nothing.
    """
    gate = _Gate()
    assert gate.should_act_on("open chrome", wake_initiated=True)
    assert gate.should_act_on("", wake_initiated=True)


@pytest.mark.parametrize("pending", ["confirmation", "stop", "reply"])
def test_answering_a_question_jalen_just_asked_needs_no_name(pending):
    """
    His choice, when asked to pick: "Name to start; answers to its own
    questions are free."

    Jalen asks "which file did you mean?" and he says "the notes one". That
    is not a new request and it must not need the name — an assistant that
    asks you a question and then ignores your answer is worse than one that
    never asks.
    """
    gate = _Gate(**{pending: True})
    assert gate.should_act_on("the notes one", wake_initiated=False)
    assert gate.should_act_on("yes", wake_initiated=False)
    assert gate.should_act_on("no, the other one", wake_initiated=False)


def test_with_nothing_pending_the_name_is_required():
    gate = _Gate()
    assert not gate.should_act_on("the notes one", wake_initiated=False)
    assert gate.should_act_on("Jalen, the notes one", wake_initiated=False)


def test_jalen_cannot_talk_itself_into_a_new_turn():
    """
    THE BUG, stated directly. Playback ends, the tail of Jalen's own reply
    is still arriving at the microphone, the follow-up window is open, and
    the VAD says "speech". Before the gate, that became a turn.
    """
    gate = _Gate()
    for own_words in (
        "Sure, I can open that for you.",
        "You have four unread emails, two of them look like opportunities.",
        "I'm still on the last one, give me a second.",
    ):
        assert not gate.should_act_on(own_words, wake_initiated=False), (
            f"Jalen would have replied to itself: {own_words!r}"
        )


def test_a_pending_question_does_not_last_forever():
    """
    The exemption is tied to state that something has to clear, not to a
    timer nobody resets. If _awaiting_reply is still true, Jalen is still
    waiting; when it is cleared the name is required again.
    """
    gate = _Gate(reply=True)
    assert gate.should_act_on("the notes one", wake_initiated=False)
    gate._awaiting_reply = False
    assert not gate.should_act_on("the notes one", wake_initiated=False)


def test_the_gate_runs_before_anything_is_dispatched():
    """
    Placement is the whole point. Downstream of the dispatch it would still
    reject the text, but the turn would already have been counted, the orb
    already yellow, and the backlog guard already consulted.
    """
    import inspect

    source = inspect.getsource(Jalen.run)
    gate = source.index("should_act_on")
    dispatch = source.index("dispatch_turn, args=(text")
    assert gate < dispatch, (
        "the address gate has moved below turn dispatch - noise now costs a "
        "turn before being thrown away"
    )


def test_rejection_is_silent():
    """
    Announcing "I didn't catch that" at a room that was not talking to him
    is the exact interruption this gate exists to end. Worse, the
    announcement is speech, the microphone hears it, and it can trip the
    window open again.
    """
    import inspect

    source = inspect.getsource(Jalen.run)
    start = source.index("should_act_on")
    after = source[start:start + 700]
    assert "self.say(" not in after, (
        "something speaks when an utterance is rejected - that is the "
        "self-inflicted interruption again"
    )
    assert "not addressed to Jalen" in after, (
        "the rejection should still be recorded, so 'it ignored me' is "
        "answerable from the audit log rather than a guess"
    )


# ---------------------------------------------------------------------------
# THE RATING QUESTION, WHICH HAD NO CLOCK ON THIS SIDE
# ---------------------------------------------------------------------------
def test_a_pending_rating_stops_being_a_free_pass_after_it_expires():
    """
    process() has always aged a pending rating out at RATING_EXPIRES_S. This
    gate only ever asked `_pending_rating is not None`, so from the moment
    Jalen said "how do you rate my work out of ten?" the name was not
    required again for as long as nobody answered. Five minutes was the
    documented bound; the real bound was "until somebody next speaks".
    """
    import time as _time

    gate = _Gate()
    gate._pending_rating = {"about": "x", "did": "", "asked_at": _time.time()}
    assert gate.should_act_on("eight out of ten", wake_initiated=False)

    gate._pending_rating["asked_at"] = _time.time() - (Jalen.RATING_EXPIRES_S + 1)
    assert not gate.should_act_on("eight out of ten", wake_initiated=False), (
        "an unanswered rating question still waives his name long after it "
        "expired"
    )
    assert not gate.should_act_on("so anyway the weather is nice", wake_initiated=False)


def test_a_rating_with_no_timestamp_is_treated_as_expired():
    """Belt and braces: a malformed entry must fail closed, not open."""
    gate = _Gate()
    gate._pending_rating = {"about": "x", "did": ""}
    assert not gate.should_act_on("eight out of ten", wake_initiated=False)
