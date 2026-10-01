"""
Two flaws in the confirmation echo rule, found by the independent re-check of
the Telegram work (reproduced on the real function with the real question
shape).

1. A SHORT TAIL OF THE QUESTION AFTER 3 SECONDS STILL APPROVED A SEND. The
   exact-tail rule is judged within ECHO_TAIL_S (3 s), and the "leaky" rule
   added for long questions needs four words. But the echo of a question
   reaches the gate about 3.2 s after it ends (the median measured in
   _sounds_like_its_own_voice), so "Confirm." or "at six. Confirm?" arrived
   after the window, was too short for the leaky rule, parsed as agreement
   (" confirm " is on the YES list) and approved the voice message.

2. THE LEAKY RULE DROPPED GENUINE YES ANSWERS THAT RESTATE THE ACTION: 19 of
   576 realistic question/answer pairs (3.3%) - "yes send the voice message",
   "yes send it to Ali" - were taken for the echo and ignored in silence, and
   the confirmation timed out and cancelled.

THE RULE THAT FIXES BOTH: an echo of the question cannot contain a plain yes
or no word that the question itself does not contain, and a genuine answer
usually does. So (a) a plain answer word that is not in the question means it
is an answer, whatever else it repeats; (b) if every word is the question's
own, it is the question coming back, however short, for the whole window the
echo can arrive in.

THE COST, said plainly: a bare "confirm" as the answer is now treated as the
question's own ending inside that window (he says "yes"), and a "yes" is
ignored when the question's own text contains "yes" (a voice message that says
"yes we are meeting"). Both fail safe - the confirmation times out and
cancels.
"""
from __future__ import annotations

import threading
import time

import pytest

from jarvis.app import ECHO_TAIL_S, Jalen

QUESTION = ("send Ali Karimov a voice message in Jalen's synthetic voice, saying: "
            "ok sounds good see you at six. Confirm?")


def _asked(waited_s: float, question: str = QUESTION) -> Jalen:
    j = Jalen.__new__(Jalen)
    j._confirm_question = question
    j._confirm_asked_at = time.monotonic() - waited_s
    return j


@pytest.mark.parametrize("waited", [1.0, ECHO_TAIL_S + 0.5, 5.0, 8.0, 11.0])
@pytest.mark.parametrize("heard", [
    "Confirm.",
    "at six. Confirm?",
    "see you at six confirm",
    "ok sounds good see you at six confirm",       # one word dropped: the leaky rule
])
def test_the_question_coming_back_is_not_an_answer_at_any_point_in_its_window(waited, heard):
    assert _asked(waited)._echoes_the_confirmation(heard), f"{heard!r} at {waited}s would approve"


@pytest.mark.parametrize("heard", [
    "yes",
    "yes send the voice message",
    "yes send it to Ali Karimov",
    "yes send Ali the voice message please",
    "yeah go ahead",
    "sure",
    "no",
    "no cancel that",
])
@pytest.mark.parametrize("waited", [1.0, 4.0, 9.0])
def test_a_real_answer_is_never_taken_for_the_echo(waited, heard):
    assert not _asked(waited)._echoes_the_confirmation(heard), f"{heard!r} was dropped as echo"


def test_a_late_confirm_outside_the_window_is_a_real_answer():
    """The window ends: 'confirm' said half a minute later is not an echo."""
    assert not _asked(30.0)._echoes_the_confirmation("Confirm.")


def test_a_yes_the_questions_own_words_contain_fails_safe_not_open():
    """
    The voice message says "yes", so a bare "yes" is indistinguishable from
    its echo and is ignored: the confirmation times out and CANCELS. The safe
    way to be wrong, and rare.
    """
    q = "send Ali a voice message in Jalen's synthetic voice, saying: yes we are meeting. Confirm?"
    assert _asked(4.0, q)._echoes_the_confirmation("yes")
    assert not _asked(4.0, q)._echoes_the_confirmation("yes send it")


def test_an_empty_hearing_is_not_an_echo():
    assert not _asked(2.0)._echoes_the_confirmation("")
    assert not _asked(2.0)._echoes_the_confirmation("   ")
