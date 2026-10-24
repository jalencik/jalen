"""
"... Confirm?" - he said "yes", and Jalen said "No answer, so I've cancelled it."

THE BUG
-------
A RED question waits safety.confirm_timeout_s, 20 s, for his answer, and the
answer was judged by when it ARRIVED. A sentence reaches process() 3.2 s after
the sound ends at the median (1.4 s endpoint, 1.8 s transcription), so a "yes"
begun in roughly the last three seconds arrived after the window had shut. The
AMBER "say stop" window had the same flaw (tests/test_saying_stop_stops_it.py)
and the same fix now covers both: a sentence that BEGAN before the window
closed holds it until it has been judged, for at most ANSWER_HOLD_S.

AND THE QUESTION ASKED AGAIN
----------------------------
An answer that was neither yes nor no got "Sorry - was that a yes or a no?",
and three things were wrong with it:
  * the window kept running, so an unclear answer near its end was asked about
    and then cancelled in the same breath;
  * "...a yes or a no?" coming back through the microphone is " no " to the
    yes/no parser, and the echo test only knew the first question - the
    question asked again could cancel itself;
  * it went out through say(), on a thread that waits behind a reply still
    streaming.
Now it is the question again, in full ("Sorry, I missed that. <question>"),
said like the first one, its echo recognised like the first one's, and the
whole window starts again.

Scaled down on the real clock: a 0.3 s window and a 2 s hold stand for the
shipped 20 s and 12 s.
"""
from __future__ import annotations

import asyncio
import threading
import time

from jalen.app import ConfirmAnswer
from jalen.config import CONFIG

from test_saying_stop_stops_it import HOLD, a_listening_jalen, he_says
from test_stop_window_takes_answers import run_announce

QUESTION = "close app: notepad. Confirm?"
WINDOW = 0.3


def run_confirm(j, window_s=WINDOW):
    j.cfg = type(CONFIG)({"safety": {"confirm_timeout_s": window_s}})
    result = {}

    def go():
        started = time.monotonic()
        result["answer"] = asyncio.run(j.confirm(QUESTION))
        result["took"] = time.monotonic() - started

    worker = threading.Thread(target=go)
    worker.start()
    deadline = time.monotonic() + 3
    while not j._awaiting_confirmation and time.monotonic() < deadline:
        time.sleep(0.01)
    return worker, result


# --------------------------------------------------------------------------
# 1. A YES BEGUN INSIDE THE WINDOW IS A YES
# --------------------------------------------------------------------------
def test_a_yes_begun_before_the_window_closed_approves_it():
    j = a_listening_jalen()
    worker, result = run_confirm(j)
    sentence = he_says(j, "yes")                 # begins now, arrives at 0.7 s
    worker.join(5)
    sentence.join(5)
    answer = result["answer"]
    assert answer and answer.outcome == "yes", (
        f"he said yes inside the window and it reached Jalen late: {answer!r}")
    assert result["took"] < HOLD, "it waited out the whole hold instead of his answer"
    assert "No answer, so I've cancelled it." not in j.said


def test_a_no_on_its_way_is_a_no_not_a_timeout():
    j = a_listening_jalen()
    worker, result = run_confirm(j)
    sentence = he_says(j, "no, don't")
    worker.join(5)
    sentence.join(5)
    assert result["answer"].outcome == "no"


def test_silence_still_cancels_when_the_window_ends():
    j = a_listening_jalen()
    worker, result = run_confirm(j)
    worker.join(5)
    assert result["answer"].outcome == "timeout"
    assert result["took"] < WINDOW + 0.6
    assert j.said[-1] == "No answer, so I've cancelled it."


# --------------------------------------------------------------------------
# 2. ASKED AGAIN: IN FULL, WITH THE WHOLE WINDOW AGAIN
# --------------------------------------------------------------------------
def test_an_unclear_answer_near_the_end_is_asked_again_and_the_window_starts_again():
    j = a_listening_jalen()
    worker, result = run_confirm(j)
    time.sleep(WINDOW - 0.05)                    # near the end of the window
    j.process("hmm the blue one", from_him=False)
    assert j.said[-1] == f"Sorry, I missed that. {QUESTION}"
    sentence = he_says(j, "yes")                 # after the first window has shut
    worker.join(5)
    sentence.join(5)
    answer = result["answer"]
    assert answer and answer.outcome == "yes", (
        f"asked again, answered, and still cancelled: {answer!r}")


def test_asked_again_and_then_silence_cancels_after_the_new_window():
    j = a_listening_jalen()
    worker, result = run_confirm(j)
    j.process("hmm the blue one", from_him=False)
    worker.join(5)
    assert result["answer"].outcome == "timeout"
    assert result["took"] >= WINDOW, "the question asked again was cut short"


def test_the_question_asked_again_does_not_answer_itself():
    """Its echo is made of the question's own words; "yes" and "no" are not."""
    j = a_listening_jalen()
    j.mode = "voice"
    j._awaiting_confirmation = True
    j._confirm_question, j._confirm_asked_at = QUESTION, time.monotonic() - 30
    j.process("hmm the blue one", from_him=False)
    assert j._confirm_question == f"Sorry, I missed that. {QUESTION}"
    assert time.monotonic() - j._confirm_asked_at < 1.0

    for echo in ("I missed that. close app: notepad. Confirm?", "notepad. Confirm?",
                 "Sorry, I missed that."):
        j.process(echo, from_him=False)
        assert j._answer_q.empty(), f"its own question coming back, {echo!r}, answered it"
    j.process("no", from_him=False)
    assert j._answer_q.get_nowait().outcome == "no"


def test_the_old_wording_would_have_cancelled_itself():
    """Why the wording changed: the echo of "...a yes or a no?" is a no."""
    from jalen.app import Jalen

    assert Jalen._parse_yes_no("a yes or a no") is False


def test_asked_again_through_say_blocking_like_the_first_question():
    j = a_listening_jalen()
    spoken = []
    j.say = lambda text, **k: spoken.append(("say", text))
    j.say_blocking = lambda text: spoken.append(("say_blocking", text))
    j._awaiting_confirmation = True
    j._confirm_question = QUESTION
    j.process("hmm the blue one", from_him=False)
    assert spoken == [("say_blocking", f"Sorry, I missed that. {QUESTION}")]


# --------------------------------------------------------------------------
# 3. A NEW QUESTION STARTS CLEAN
# --------------------------------------------------------------------------
def test_an_announcement_does_not_inherit_a_confirms_window_asked_again():
    j = a_listening_jalen()
    j._reask_until = time.monotonic() + 30       # left over from a "Confirm?"
    worker, result = run_announce(j, window_s=0.2)
    worker.join(5)
    assert result["outcome"] == "went ahead" and result["took"] < 1.0


def test_a_confirm_does_not_inherit_the_last_ones_window_asked_again():
    j = a_listening_jalen()
    j._reask_until = time.monotonic() + 30
    worker, result = run_confirm(j, window_s=0.2)
    worker.join(5)
    assert result["answer"].outcome == "timeout" and result["took"] < 1.0


def test_a_window_asked_again_holds_a_sentence_begun_inside_it():
    j = a_listening_jalen()
    j._reask_until = 105.0
    j._hearing_since = 104.0          # after the first window (100) closed, inside the second
    assert j._held_for(100.0) == 104.0 + HOLD
    j._hearing_since = 106.0          # after both
    assert j._held_for(100.0) == 105.0


def test_a_tap_still_answers_a_question_asked_again():
    """The buttons under the first question stay good: same question, same id."""
    j = a_listening_jalen()
    j._prompt_id = "abc12345"
    j._awaiting_confirmation = True
    j._confirm_question = QUESTION
    j.process("hmm the blue one", from_him=False)
    assert j.answer_from_tap("jalen:abc12345:yes").accepted
    assert isinstance(j._answer_q.get_nowait(), ConfirmAnswer)
