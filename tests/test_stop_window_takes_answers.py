"""
"...Say stop if you don't want that." - and he said "no, don't".

THE BUG
-------
While an AMBER announcement waits for "stop", the address gate lets his speech
in WITHOUT his name, so that it can be the answer (Jalen.should_act_on). But
process() only knew the kill phrases. "No", "don't", "wait", "hold on" - the
way people actually refuse - fell through to the router and the brain as a NEW
instruction, and the action they refused went ahead when the window closed.
"Yes" and "go ahead" became new turns too.

NOW
---
During the window a refusal stops the action and agreement lets it go at once.
Anything that is neither is a new instruction, as before. The announcement's
own words coming back through the speakers are ignored - but only an exact
TAIL of it ("want that", "don't want that"), which is what an echo is. That is
narrower than the "Confirm?" echo test on purpose: there a missed echo approves
a RED action; here it can only stop one, and "stop that" is made of the
announcement's own words and is exactly the answer the window is for.

AND THE WAIT ITSELF (Jalen._wait_for)
-------------------------------------
* The deadline is one moment: an answer that landed just before it is taken,
  and the question's id is spent, so a tap after it is refused instead of being
  told "Approved." while Jalen says "No answer, so I've cancelled it."
* A wait whose question was abandoned (the SDK cancels a hook the CLI gave up
  on) stops within half a second instead of lingering for the whole window, and
  never keeps an answer meant for a later question.
"""
from __future__ import annotations

import asyncio
import queue
import threading
import time
from types import SimpleNamespace

import pytest

from jalen.app import ConfirmAnswer, Jalen
from jalen.config import CONFIG

ANNOUNCEMENT = "edit file: notes.txt. Say stop if you don't want that."


class _Stop(Exception):
    """Raised by the router stub: the sentence went on as a NEW instruction."""


def a_jalen():
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
    j._prompt_id = ""
    j._prompt_seq = 0
    j._offer_buttons = None
    j._stop_question = ""
    j.routed = []

    def _route(text):
        j.routed.append(text)
        raise _Stop()

    j.router = SimpleNamespace(route=_route)
    return j


def stop_window_open(j=None):
    j = j or a_jalen()
    j._awaiting_stop = True
    j._stop_question = ANNOUNCEMENT
    return j


def hear(j, text):
    try:
        j.process(text)
    except _Stop:
        pass


# --------------------------------------------------------------------------
# A REFUSAL STOPS IT
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "no", "No, don't", "don't", "wait", "hold on", "not now", "stop that",
    "no thanks", "I'd rather not", "Don’t do that", "nope", "cancel that",
])
def test_a_refusal_in_the_window_stops_the_action(text):
    j = stop_window_open()
    hear(j, text)
    assert j._answer_q.get_nowait() is False, f"{text!r} did not stop it"
    assert j.routed == [], f"{text!r} was also started as a new instruction"


def test_the_kill_phrase_still_stops_it_the_way_it_always_did():
    j = stop_window_open()
    hear(j, "stop")
    assert j._answer_q.get_nowait() is False and j.kill.is_set()


# --------------------------------------------------------------------------
# AGREEMENT LETS IT GO NOW
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["yes", "go ahead", "do it", "okay", "sure, go on", "fine"])
def test_agreement_in_the_window_lets_the_action_go_at_once(text):
    j = stop_window_open()
    hear(j, text)
    assert j._answer_q.get_nowait() is True
    assert j.routed == []


# --------------------------------------------------------------------------
# ITS OWN ECHO IS NOT AN ANSWER
# --------------------------------------------------------------------------
@pytest.mark.parametrize("echo", [
    "want that", "Want that.", "don't want that", "Don’t want that.",
    "you don't want that", "if you don't want that", "stop if you don't want that",
    "Say stop if you don't want that.",
])
def test_the_announcement_coming_back_is_ignored(echo):
    j = stop_window_open()
    hear(j, echo)
    assert j._answer_q.empty(), f"its own echo {echo!r} answered the window"
    assert j.routed == [], f"its own echo {echo!r} became an instruction"


def test_only_an_exact_tail_is_echo_so_stop_that_is_still_heard():
    """Made of the announcement's words, and exactly the answer the window is for."""
    j = stop_window_open()
    assert not j._echoes_the_announcement("stop that")
    assert not j._echoes_the_announcement("don't")
    assert j._echoes_the_announcement("want that")


def test_with_no_announcement_recorded_nothing_is_echo():
    j = a_jalen()
    assert not j._echoes_the_announcement("want that")


# --------------------------------------------------------------------------
# EVERYTHING ELSE IS STILL A NEW INSTRUCTION
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["open chrome", "what time is it", "send it to Rodion instead"])
def test_a_sentence_that_is_neither_is_a_new_instruction_as_before(text):
    j = stop_window_open()
    hear(j, text)
    assert j._answer_q.empty()
    assert j.routed, f"{text!r} was swallowed by the window"


def test_with_no_window_open_a_no_is_an_ordinary_sentence():
    j = a_jalen()
    hear(j, "no")
    assert j._answer_q.empty() and j.routed == ["no"]


def test_a_pending_confirmation_still_takes_the_answer_first():
    j = a_jalen()
    j._awaiting_confirmation = True
    j._confirm_question = "delete file: x. Confirm?"
    j._confirm_asked_at = time.monotonic() - 30          # well past any echo window
    hear(j, "no")
    answer = j._answer_q.get_nowait()
    assert isinstance(answer, ConfirmAnswer) and answer.outcome == "no"


# --------------------------------------------------------------------------
# END TO END THROUGH announce()
# --------------------------------------------------------------------------
def run_announce(j, window_s):
    j.cfg = type(CONFIG)({"safety": {"undo_window_s": window_s}})
    result = {}

    def go():
        started = time.monotonic()
        try:
            asyncio.run(j.announce(ANNOUNCEMENT))
            result["outcome"] = "went ahead"
        except RuntimeError as exc:
            result["outcome"] = str(exc)
        result["took"] = time.monotonic() - started

    worker = threading.Thread(target=go)
    worker.start()
    deadline = time.monotonic() + 3
    while not j._awaiting_stop and time.monotonic() < deadline:
        time.sleep(0.01)
    return worker, result


def test_no_dont_in_the_window_cancels_the_announced_action():
    j = a_jalen()
    worker, result = run_announce(j, window_s=10)
    hear(j, "no, don't")
    worker.join(5)
    assert result["outcome"] == "cancelled by user" and result["took"] < 5


def test_go_ahead_in_the_window_runs_it_now_instead_of_after_the_window():
    j = a_jalen()
    worker, result = run_announce(j, window_s=30)
    hear(j, "go ahead")
    worker.join(5)
    assert result["outcome"] == "went ahead" and result["took"] < 5


def test_announce_records_its_own_words_for_the_echo_test():
    j = a_jalen()
    worker, result = run_announce(j, window_s=0.2)
    worker.join(5)
    assert j._stop_question == ANNOUNCEMENT


# --------------------------------------------------------------------------
# THE WAIT: ONE DEADLINE, AND NO ANSWER KEPT BY A WAIT THAT WAS ABANDONED
# --------------------------------------------------------------------------
class SlowEmptyThenLate:
    """A queue that stays empty until the deadline, then has one late answer in it."""

    def __init__(self, late):
        self.late = late

    def get(self, timeout=None):
        time.sleep(timeout or 0)
        raise queue.Empty

    def get_nowait(self):
        if self.late is None:
            raise queue.Empty
        late, self.late = self.late, None
        return late

    def put(self, item):
        raise AssertionError("nothing should be put back")


def test_an_answer_that_lands_at_the_deadline_is_taken_not_lost():
    j = a_jalen()
    j._prompt_id, j._prompt_seq = "abc12345", 7
    j._answer_q = SlowEmptyThenLate(ConfirmAnswer("yes", "tapped yes"))
    answer = j._wait_for(0.05)
    assert answer and answer.outcome == "yes"


def test_at_the_deadline_the_question_is_spent_so_a_later_tap_is_refused():
    j = a_jalen()
    j._awaiting_confirmation = True
    j._prompt_id, j._prompt_seq = "abc12345", 7
    assert j._wait_for(0.05) is None
    assert j._prompt_id == ""
    tap = j.answer_from_tap("jalen:abc12345:yes")
    assert not tap.accepted and j._answer_q.empty()


def test_an_abandoned_wait_stops_promptly_instead_of_waiting_out_its_window():
    j = a_jalen()
    j._prompt_seq = 1
    result = {}

    def wait():
        started = time.monotonic()
        result["answer"] = j._wait_for(30)
        result["took"] = time.monotonic() - started

    worker = threading.Thread(target=wait)
    worker.start()
    time.sleep(0.1)
    j._prompt_seq = 2                         # the question is over (cancelled)
    worker.join(5)
    assert result["answer"] is None and result["took"] < 2


def test_an_abandoned_wait_leaves_a_newer_questions_id_alone():
    j = a_jalen()
    j._prompt_seq = 1
    worker = threading.Thread(target=lambda: j._wait_for(30))
    worker.start()
    time.sleep(0.05)
    j._prompt_seq, j._prompt_id = 2, "newer999"
    worker.join(5)
    assert j._prompt_id == "newer999"


@pytest.mark.parametrize("attempt", range(15))
def test_an_abandoned_wait_never_keeps_the_next_questions_answer(attempt):
    j = a_jalen()
    j._prompt_seq = 1
    results = {}
    old = threading.Thread(target=lambda: results.__setitem__("old", j._wait_for(30)))
    old.start()
    time.sleep(0.02)
    j._prompt_seq = 2                         # abandoned; the next question is open
    new = threading.Thread(target=lambda: results.__setitem__("new", j._wait_for(30)))
    new.start()
    time.sleep(0.02)
    j._answer_q.put(ConfirmAnswer("yes", "his answer to the NEW question"))
    old.join(5)
    new.join(5)
    assert results["old"] is None
    assert results["new"] and results["new"].words == "his answer to the NEW question"


def test_an_ordinary_answer_is_returned_at_once():
    j = a_jalen()
    j._prompt_seq = 3
    j._answer_q.put(ConfirmAnswer("no", "no"))
    started = time.monotonic()
    answer = j._wait_for(30)
    assert answer.outcome == "no" and time.monotonic() - started < 0.5
