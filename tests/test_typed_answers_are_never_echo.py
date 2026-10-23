"""
He typed "Confirm" into Telegram and nothing happened.

Jalen asks "clear temp files. Confirm?". On a phone the natural reply is to
type the word back: "Confirm". process() ran the MICROPHONE echo test on it -
the test that stops "...Confirm?" coming back through the speakers from
approving itself - and that test throws away a "confirm" heard within about ten
seconds of the question. So a typed "Confirm", "confirm" or "Confirmed" was
dropped in silence: no reply, no "was that a yes or a no?", and the question
sat out its whole five-minute window and then cancelled. Reproduced in text
and Telegram mode before the fix.

Typed text never went near a microphone, so it is never an echo. The voice
behaviour is unchanged and pinned here too: by voice, "Confirm" inside the echo
window IS the echo, and stays ignored.
"""
from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

import pytest

from jalen.app import ConfirmAnswer, Jalen
from jalen.config import CONFIG

QUESTION = "clear temp files. Confirm?"
ANNOUNCEMENT = "edit file: notes.txt. Say stop if you don't want that."


class _Stop(Exception):
    pass


def a_jalen(mode):
    j = Jalen.__new__(Jalen)
    j.cfg = CONFIG
    if mode is not None:
        j.mode = mode
    j.said = []
    j.say = lambda text, **k: j.said.append(text)
    j.say_blocking = lambda text: j.said.append(text)
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
    j._confirm_question = ""
    j._confirm_asked_at = 0.0
    j._confirm_reasked = False
    j._stop_question = ""
    j.routed = []

    def _route(text):
        j.routed.append(text)
        raise _Stop()

    j.router = SimpleNamespace(route=_route)
    return j


def ask(j, seconds_ago=4.0):
    j._awaiting_confirmation = True
    j._confirm_question = QUESTION
    j._confirm_asked_at = time.monotonic() - seconds_ago


def say(j, text):
    try:
        j.process(text)
    except _Stop:
        pass


@pytest.mark.parametrize("mode", ["telegram", "text"])
@pytest.mark.parametrize("typed", ["Confirm", "confirm", "Confirmed", "Confirm.", "CONFIRM",
                                   "confirm it", "clear temp files. Confirm?"])
@pytest.mark.parametrize("seconds_ago", [0.5, 4.0, 9.0])
def test_a_typed_confirm_is_his_yes_however_soon_he_types_it(mode, typed, seconds_ago):
    j = a_jalen(mode)
    ask(j, seconds_ago)
    say(j, typed)
    answer = j._answer_q.get_nowait() if not j._answer_q.empty() else None
    assert answer is not None, f"typed {typed!r} {seconds_ago}s after the question was dropped"
    assert answer and answer.outcome == "yes"
    assert j.routed == []


@pytest.mark.parametrize("mode", ["telegram", "text"])
def test_a_typed_no_is_still_a_no(mode):
    j = a_jalen(mode)
    ask(j, 1.0)
    say(j, "no")
    answer = j._answer_q.get_nowait()
    assert not answer and answer.outcome == "no"


@pytest.mark.parametrize("mode", ["voice", None])
@pytest.mark.parametrize("echo", ["Confirm", "Confirmed", "clear temp files. Confirm?"])
def test_by_voice_the_question_coming_back_is_still_ignored(mode, echo):
    """Unchanged: the room's echo of "...Confirm?" must never approve itself."""
    j = a_jalen(mode)
    ask(j, 1.0)
    say(j, echo)
    assert j._answer_q.empty() and j.routed == []


def test_by_voice_a_real_yes_inside_the_echo_window_is_still_heard():
    j = a_jalen("voice")
    ask(j, 1.0)
    say(j, "yes")
    assert j._answer_q.get_nowait()


@pytest.mark.parametrize("mode,typed,expected", [
    ("telegram", "don't want that", False),     # typed: a refusal, whatever it echoes
    ("text", "Don't want that.", False),
])
def test_typed_words_that_echo_the_announcement_are_still_his_answer(mode, typed, expected):
    j = a_jalen(mode)
    j._awaiting_stop = True
    j._stop_question = ANNOUNCEMENT
    say(j, typed)
    assert j._answer_q.get_nowait() is expected


def test_by_voice_the_announcement_coming_back_is_still_ignored():
    j = a_jalen("voice")
    j._awaiting_stop = True
    j._stop_question = ANNOUNCEMENT
    say(j, "don't want that")
    assert j._answer_q.empty() and j.routed == []


@pytest.mark.parametrize("mode,expected", [("voice", True), (None, True),
                                           ("text", False), ("telegram", False)])
def test_only_voice_mode_can_hear_its_own_speakers(mode, expected):
    assert a_jalen(mode)._hears_its_own_speakers() is expected


# --------------------------------------------------------------------------
# THE EXTENSION'S CHAT PANEL IS A TYPED FRONT DOOR TOO
# --------------------------------------------------------------------------
# It went straight to the brain, past every answer branch, so "yes" typed in
# the panel to a "Confirm?" Jalen had just asked started a SECOND brain turn -
# which waited behind the first one's lock while the question it was answering
# ran out of time.
@pytest.fixture
def panel(monkeypatch):
    import asyncio

    from jalen.bridge import server

    j = a_jalen("voice")          # the panel lives beside voice mode
    j.brain_calls = []
    j.shown = []

    async def handle_with_brain(text, on_text=None):
        j.brain_calls.append(text)
        return "brain reply"

    j.handle_with_brain = handle_with_brain
    j._run_coro = lambda coro: asyncio.run(coro)

    class Panel:
        def send_command(self, name, payload, timeout=10):
            j.shown.append(payload["text"])

    monkeypatch.setattr(server, "get_server", lambda: Panel())
    return j


def test_yes_typed_in_the_panel_answers_the_open_question(panel):
    ask(panel, 0.5)                   # typed within the echo window: still his
    panel._answer_panel("yes")
    answer = panel._answer_q.get_nowait()
    assert answer and answer.outcome == "yes"
    assert panel.brain_calls == [] and panel.shown == ["Got it."]


def test_confirm_typed_in_the_panel_is_his_yes_not_an_echo(panel):
    ask(panel, 0.5)
    panel._answer_panel("Confirm")
    assert panel._answer_q.get_nowait() and panel.brain_calls == []


def test_the_panel_says_what_was_done_and_never_claims_more(panel):
    ask(panel, 30)
    # Neither yes nor no. ("maybe later perhaps" was the example here until
    # "later" became a refusal on 2026-10-23: not now is no.)
    panel._answer_panel("perhaps the blue one")
    assert panel._answer_q.empty()
    assert panel.shown == ["Sorry - was that a yes or a no?"]


def test_an_answer_to_an_open_ask_user_question_typed_in_the_panel(panel):
    panel._awaiting_reply = True
    panel._answer_panel("+998 90 123 45 67")
    assert panel._reply_q.get_nowait() == "+998 90 123 45 67"
    assert panel.brain_calls == []


def test_stop_typed_in_the_panel_is_the_kill_switch(panel):
    panel._answer_panel("stop")
    assert panel.kill.is_set() and panel.shown == ["Stopped."] and panel.brain_calls == []


def test_with_nothing_asked_the_panel_still_goes_to_the_brain(panel):
    panel._answer_panel("what's on my calendar")
    assert panel.brain_calls == ["what's on my calendar"] and panel.shown == ["brain reply"]
