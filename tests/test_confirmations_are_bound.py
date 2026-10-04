"""
The spoken "Confirm?" is the last thing between a RED action and the machine
doing it. An independent audit found four ways it approved the wrong thing.

ONE: TWO PENDING ACTIONS SHARED ONE ANSWER
------------------------------------------
Two turns can be in flight, and each RED tool calls confirm(). Both waited on
the same _answer_q, and confirm()'s finally cleared _awaiting_confirmation for
BOTH when either finished. One "yes" approved whichever was waiting OLDEST -
not the one he had just heard asked. Reproduced against the real code.

TWO: JALEN APPROVED ITSELF
--------------------------
Every RED prompt ends in "Confirm?", and " confirm " is on the YES list. With
no echo cancellation on this machine, the tail of the question comes back
through the microphone as "Confirm." - and while a confirmation is pending the
address gate admits anything, name or not. The echo defence could not see it:
it only treats three words or more as echo, so that a one-word answer like
"ChatGPT" is never mistaken for Jalen's own voice.

THREE: A CORRECTION APPROVED THE ORIGINAL
-----------------------------------------
_parse_yes_no looks for agreement anywhere in the sentence, so each of these,
from the audit's reproduction, returned True and ran the action as asked:

    "send it to Rodion instead"
    "actually send it to my saved messages instead"
    "great, but first fix the typo"
    "right now I'm busy"
    "I'm good"
    "ok so what does it say"
    "Okay Google"

FOUR: A TIMEOUT WAS REPORTED AS "HE SAID NO"
-------------------------------------------
Every falsy answer became "He said no. Don't retry" to the model, which then
told him he had refused something he never heard. And an answer that could
not be parsed fell through to the router and started a SECOND brain turn while
the first was still waiting for him.
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from jalen.app import ECHO_TAIL_S, ConfirmAnswer, Jalen
from jalen.config import CONFIG

parse = Jalen._parse_yes_no


# ---------------------------------------------------------------------------
# THREE: corrections, deferrals and stray assistants are not a yes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "send it to Rodion instead",
    "actually send it to my saved messages instead",
    "great, but first fix the typo",
    "right now I'm busy",
    "I'm good",
    "ok so what does it say",
    "Okay Google",
    "hey google",
    "good question, who is Ulughbek",
    "yes but change the subject line first",
])
def test_a_correction_or_a_deferral_is_never_a_yes(text):
    assert parse(text) is not True, f"{text!r} approved the ORIGINAL action"


@pytest.mark.parametrize("text", [
    "yes", "yeah", "of course", "Of course.", "course", "You, of course.",
    "go ahead", "do it", "send it", "yes please", "sure", "okay", "confirm",
    "that's right", "absolutely", "why not",
])
def test_plain_agreement_still_confirms(text):
    """
    The logged answers that DID execute under the current policy - 'course',
    'You, of course.', 'of course.' - must keep working. His rule was 'any
    kind of agreement should confirm', and exact-match is what lost 'Of
    course' to a timeout on 23 August.
    """
    assert parse(text) is True, text


def test_im_good_is_a_refusal_not_an_agreement():
    assert parse("I'm good") is False
    assert parse("im good thanks") is False


# ---------------------------------------------------------------------------
# The answer object: truthy only on a real yes, and it says which no it was
# ---------------------------------------------------------------------------
def test_the_answer_is_truthy_only_on_yes():
    assert ConfirmAnswer("yes")
    for outcome in ("no", "timeout", "correction"):
        assert not ConfirmAnswer(outcome), outcome


# ---------------------------------------------------------------------------
# Harness for confirm() and the confirmation branch of process()
# ---------------------------------------------------------------------------
class _Stop(Exception):
    pass


def _jalen():
    j = Jalen.__new__(Jalen)
    j.cfg = CONFIG
    j.said = []
    j.say_blocking = lambda text: j.said.append(text)
    j.say = lambda text, **k: j.said.append(text)
    j.orb = SimpleNamespace(set_state=lambda *a: None)
    j.audit = SimpleNamespace(utterance=lambda *a, **k: None, write=lambda *a, **k: None,
                              error=lambda *a, **k: None)
    j.speaker = SimpleNamespace(stop=lambda: None)
    j.kill = threading.Event()
    j._answer_q = queue.Queue()
    j._reply_q = queue.Queue()
    j._awaiting_reply = False
    j._awaiting_confirmation = False
    j._awaiting_stop = False
    j._pending_rating = None
    j._last_user_text, j._last_user_at, j._plan = "", 0.0, None
    j._turn_lock, j._active_turns = threading.Lock(), set()
    j.kill_phrases = [p.lower() for p in CONFIG.get_path("safety.kill_phrases", [])]
    j.end_phrases = []
    j._confirm_lock = None
    j._confirm_question = ""
    j._confirm_asked_at = 0.0
    j._confirm_reasked = False
    j.routed = []

    def _route(text):
        j.routed.append(text)
        raise _Stop()

    j.router = SimpleNamespace(route=_route)
    return j


def _pending(j, question="close app: notepad. Confirm?", asked_ago=10.0):
    j._awaiting_confirmation = True
    j._confirm_question = question
    j._confirm_asked_at = time.monotonic() - asked_ago


def _say(j, text):
    try:
        j.process(text)
    except _Stop:
        pass


# ---------------------------------------------------------------------------
# TWO: the echo of "Confirm?" does not approve it
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("echo", ["Confirm.", "confirm", "notepad. Confirm?",
                                  "close app notepad confirm"])
def test_the_question_coming_back_through_the_microphone_is_ignored(echo):
    j = _jalen()
    _pending(j, asked_ago=0.5)
    _say(j, echo)
    assert j._answer_q.empty(), f"Jalen approved its own question: heard {echo!r}"
    assert not j.routed, "the echo was routed as a new instruction"


def test_the_same_word_from_him_later_is_a_real_yes():
    """
    Time is the gate - but the window is how long an echo can take to reach
    the gate, not the 3 s of the exact-tail rule. The echo of a question
    arrives about 3.2 s after it ends at the median (measured, see
    _sounds_like_its_own_voice), so 'confirm' 4 s later WAS the echo, and it
    approved a voice message (tests/test_confirmation_echo_round_two.py).
    Past the whole window it is a real yes.
    """
    from jalen.app import ECHO_REACHES_THE_GATE_S

    j = _jalen()
    _pending(j, asked_ago=ECHO_REACHES_THE_GATE_S + 1)
    _say(j, "confirm")
    assert j._answer_q.get_nowait()


def test_a_real_yes_inside_the_echo_window_still_counts():
    """'yes' is not in the question, so it cannot be its echo."""
    j = _jalen()
    _pending(j, asked_ago=0.5)
    _say(j, "yes")
    assert j._answer_q.get_nowait()


# ---------------------------------------------------------------------------
# THREE, end to end: a correction cancels the pending action and says why
# ---------------------------------------------------------------------------
def test_a_correction_is_delivered_as_a_correction_with_his_words():
    j = _jalen()
    _pending(j)
    _say(j, "send it to Rodion instead")
    answer = j._answer_q.get_nowait()
    assert not answer
    assert answer.outcome == "correction"
    assert answer.words == "send it to Rodion instead"
    assert not j.routed, "the correction started a second turn in parallel"


# ---------------------------------------------------------------------------
# FOUR: an unreadable answer is asked about once, not routed
# ---------------------------------------------------------------------------
def test_an_unreadable_answer_is_asked_about_once_instead_of_starting_a_second_turn():
    j = _jalen()
    _pending(j)
    _say(j, "hmm let me think about it")
    assert j._answer_q.empty()
    assert not j.routed
    assert any("yes or a no" in s.lower() for s in j.said), j.said

    # Asked once. A second unreadable thing is his to say: it goes on to be
    # handled as a new instruction, as it did before, rather than trapping
    # him inside the confirmation.
    _say(j, "open chrome")
    assert j.routed == ["open chrome"]


# ---------------------------------------------------------------------------
# ONE: confirmations are serialised, so a yes answers the question he heard
# ---------------------------------------------------------------------------
def test_two_confirmations_are_asked_one_at_a_time():
    j = _jalen()
    j._wait_for_answer = lambda timeout: j._answer_q.get(timeout=timeout)

    async def scenario():
        first = asyncio.create_task(j.confirm("send email: a@b.com. Confirm?"))
        await asyncio.sleep(0.05)
        second = asyncio.create_task(j.confirm("delete file: notes.txt. Confirm?"))
        await asyncio.sleep(0.05)
        # Only the first question has been asked.
        assert j.said == ["send email: a@b.com. Confirm?"], j.said
        j._answer_q.put(ConfirmAnswer("yes"))
        a = await first
        await asyncio.sleep(0.05)
        # NOW the second is asked, and the first yes did not answer it.
        assert j.said[-1] == "delete file: notes.txt. Confirm?"
        assert not second.done()
        j._answer_q.put(ConfirmAnswer("no"))
        b = await second
        return a, b

    a, b = asyncio.run(scenario())
    assert a and not b


def test_a_timeout_is_a_timeout_and_not_a_refusal():
    j = _jalen()
    j._wait_for_answer = lambda timeout: None        # nobody answered
    answer = asyncio.run(j.confirm("send email: a@b.com. Confirm?"))
    assert not answer
    assert answer.outcome == "timeout"


def test_the_model_is_told_the_truth_about_why():
    """
    'He said no. Don't retry' for a timeout made the model tell him he had
    refused something he never heard (2026-08-24T05:10, the recycle bin).
    """
    from jalen.brain.agent import _deny_reason

    assert "no" in _deny_reason(ConfirmAnswer("no")).lower()
    assert "answer" in _deny_reason(ConfirmAnswer("timeout")).lower()
    reason = _deny_reason(ConfirmAnswer("correction", "send it to Rodion instead"))
    assert "send it to Rodion instead" in reason


# ---------------------------------------------------------------------------
# The backlog guard must not drop the answer itself
# ---------------------------------------------------------------------------
def test_an_answer_is_never_refused_by_the_backlog_guard():
    """
    With two turns in flight - one of them the turn WAITING for this very
    answer - the guard said "I'm still on the last one" and dropped his
    'yes'. The confirmation then timed out, and was reported as a refusal.
    """
    import inspect

    source = inspect.getsource(Jalen.run)
    guard = source.index("if in_flight >= MAX_IN_FLIGHT_TURNS")
    window = source[guard - 1400:guard + 200]
    assert "answering" in window, "answers to a pending question still hit the backlog guard"
