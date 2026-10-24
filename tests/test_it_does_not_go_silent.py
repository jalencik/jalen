"""
"When I tell it the task it should not go silent."      - his words, 2026-10-23

A brain turn that works through tools produced one line - "Give me a second."
at 1.4 s - and then nothing for as long as the tools took: a minute and more
for a delegated chat or a coding job. The recurring complaint in his own
words was "why you are silent and not speaking to me". Now a long, silent
turn says a short STILL_WORKING line at 20, 45 and 90 seconds of silence -
never while words of the answer are arriving, never while Jalen is speaking,
and never while a question of his ("... Confirm?") is open.

Scaled down on the real clock: the thresholds become tenths of a second.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from jalen.app import Jalen

SCALED = ((0.15, "first"), (0.3, "second"), (0.45, "third"))


@pytest.fixture
def company(monkeypatch):
    monkeypatch.setattr(Jalen, "STILL_WORKING", SCALED)
    j = Jalen.__new__(Jalen)
    j.speaker = SimpleNamespace(speaking=False)
    j._awaiting_confirmation = j._awaiting_stop = j._awaiting_reply = False
    j.state = {"chars": 0}
    j.heard = []
    j.done = threading.Event()
    j.thread = threading.Thread(target=j._keep_him_company,
                                args=(j.state, j.heard.append, j.done, 0.01), daemon=True)
    return j


def run_for(j, seconds, during=None):
    j.thread.start()
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if during is not None:
            during(time.monotonic() - started)
        time.sleep(0.01)
    j.done.set()
    j.thread.join(2)


def test_a_long_silent_turn_is_accompanied(company):
    run_for(company, 0.7)
    assert company.heard == ["first", "second", "third"]


def test_three_lines_at_most_however_long_it_takes(company):
    run_for(company, 1.2)
    assert len(company.heard) == 3


def test_a_turn_that_ends_in_time_says_nothing_extra(company):
    run_for(company, 0.1)
    assert company.heard == []


def test_words_of_the_answer_arriving_are_not_silence(company):
    def streaming(elapsed):
        company.state["chars"] += 1
    run_for(company, 0.7, during=streaming)
    assert company.heard == []


@pytest.mark.parametrize("question", ["_awaiting_confirmation", "_awaiting_stop", "_awaiting_reply"])
def test_never_over_a_question_he_is_being_asked(company, question):
    setattr(company, question, True)
    run_for(company, 0.7)
    assert company.heard == [], "it talked over the question it was waiting on"


def test_the_quiet_is_counted_from_when_the_question_closed(company):
    def question_until(elapsed):
        company._awaiting_confirmation = elapsed < 0.4
    run_for(company, 0.5, during=question_until)
    assert company.heard == [], "the time spent waiting on him was counted as silence"


def test_never_while_jalen_is_already_speaking(company):
    company.speaker.speaking = True
    run_for(company, 0.7)
    assert company.heard == []


def test_the_first_line_comes_after_nearly_every_ordinary_turn_is_over():
    """Brain.ask, measured: p75 8.0 s, p90 23.0 s. An ordinary turn never hears it."""
    first, _line = Jalen.STILL_WORKING[0]
    assert 8.0 < first <= 23.0
    times = [after for after, _ in Jalen.STILL_WORKING]
    assert times == sorted(times) and len(times) == 3


def test_it_runs_beside_every_streamed_brain_turn_and_stops_with_it():
    import inspect

    source = inspect.getsource(Jalen.speak_brain_reply)
    assert "target=self._keep_him_company" in source
    started = source.index("target=self._keep_him_company")
    finished = source.index("done.set()")
    assert started < source.index("self.handle_with_brain(user_text, on_text=on_text)") < finished
