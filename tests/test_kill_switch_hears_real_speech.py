"""
The emergency stop only worked if he said it the way the config file spells it.

Kill phrases were compared as an exact string after lowercasing and stripping
only . ! and ? - so the comma speech recognition actually writes after a name
broke it. Replayed by an independent audit through the real checks:

    "Jalen stop"         kill switch: yes
    "Jalen, stop."       kill switch: NO    router: nothing -> the brain
    "Hey Jalen, stop."   kill switch: NO
    "Jaylen stop."       kill switch: NO
    "Jalen, enough."     kill switch: NO
    "Stop, Jalen."       kill switch: NO    and not even addressed, so the
                                            gate dropped it in silence

The stop is the one command he needs to work under pressure, and it went to
the brain - which today can only say its sign-in has expired.

The same comparison lives in TWO places - process() and the address gate's
kill-phrase exemption - so both now call one matcher. Widening one gate and
not the other is the documented two-gates bug in CLAUDE.md.

It stays a WHOLE-utterance match. "stop the car" and "cancel my
subscription" said in the room must stop nothing.
"""
from __future__ import annotations

import pytest

from jarvis.brain.router import is_kill_phrase
from jarvis.config import CONFIG

PHRASES = [p.lower() for p in CONFIG.get_path("safety.kill_phrases", [])]


@pytest.mark.parametrize("said", [
    "Jalen, stop.", "Hey Jalen, stop.", "Jaylen stop.", "Jalen, enough.",
    "Stop, Jalen.", "Stop!", "stop", "STOP.", "Jalen, stop it.",
    "Jalen - stop talking.", "Okay, stop.", "Jarvis, stop.", "Hey Jalen stop",
    "that’s enough", "Jalin, halt.", "Jalen, cancel.", "Cancel, Jalen!",
])
def test_the_stop_is_heard_however_speech_recognition_writes_it(said):
    assert is_kill_phrase(said, PHRASES), f"{said!r} did not stop Jalen"


@pytest.mark.parametrize("said", [
    "stop the car", "cancel my subscription", "don't stop believing",
    "we are done with dinner", "I said bye to him", "goodbye everyone",
    "Julian, stop.", "Jalen, stop sending emails to Rodion",
    "stop by the shop later", "", "   ", "Jalen",
])
def test_a_sentence_that_merely_contains_a_stop_word_does_not(said):
    assert not is_kill_phrase(said, PHRASES), f"{said!r} would have stopped Jalen"


def test_both_gates_use_the_one_matcher():
    import inspect

    from jarvis.app import Jalen

    assert "is_kill_phrase(" in inspect.getsource(Jalen.process)
    assert "is_kill_phrase(" in inspect.getsource(Jalen.should_act_on)


def test_the_unaddressed_form_is_admitted_by_the_gate():
    """'Stop, Jalen.' does not START with his name, so only the kill-phrase
    exemption can let it through."""
    from test_address_gate import _Gate

    assert _Gate().should_act_on("Stop, Jalen.", wake_initiated=False)
    assert not _Gate().should_act_on("stop the car", wake_initiated=False)
