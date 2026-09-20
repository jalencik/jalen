"""
Every way a person says "stop".

    "We need more testings of Jalen I mean wake word, that quit word should
     answer as well, Jalen stop should work."

He was right that it was broken, and the shape of the bug is worth keeping:
"jalen stop" routed to NOTHING. It worked anyway, because `safety.kill_phrases`
is checked inside process() before the router is ever consulted — so testing
the router alone said "broken" and testing the assistant said "fine".

Both paths are enumerated here, together, because the question a user asks is
"does saying this stop it", not "which of the two mechanisms handles it".

THE INTERACTION THAT NEARLY SHIPPED
-----------------------------------
The address gate (see test_address_gate.py) requires his name before Jalen
acts on anything. Added naively, that gate ran BEFORE the kill-phrase check
and silently swallowed a bare "stop" — turning the emergency brake into the
one command with an extra hurdle in front of it. Kill phrases are now
explicitly exempt, and this file is where that stays true.
"""
from __future__ import annotations

import pytest

from jarvis.app import Jalen
from jarvis.brain.router import IntentRouter
from jarvis.config import CONFIG

KILL_PHRASES = set(CONFIG.get_path("safety.kill_phrases"))


@pytest.fixture(scope="module")
def router():
    return IntentRouter(CONFIG)


class _Gate:
    """Enough of Jalen to answer should_act_on, with nothing pending."""

    def __init__(self):
        self._awaiting_confirmation = False
        self._awaiting_stop = False
        self._awaiting_reply = False
        self._pending_rating = None
        # Jalen grew a question window (tests/test_answering_a_question.py).
        # Nothing asked here: the emergency stop must work with no
        # conversational state open at all, which is the whole point.
        self._expecting = None
        self._last_reply_text = ""
        self._last_user_text = ""
        self._last_user_at = 0.0
        self.cfg = CONFIG
        self.kill_phrases = KILL_PHRASES

    should_act_on = Jalen.should_act_on
    _continues_last_utterance = Jalen._continues_last_utterance
    _expectation_open = Jalen._expectation_open
    _sounds_like_its_own_voice = Jalen._sounds_like_its_own_voice


def _reaches_something(router, phrase: str) -> str:
    """Whichever mechanism catches it, name it. '' means nothing does."""
    low = phrase.lower().strip().rstrip(".!?")
    if low in KILL_PHRASES:
        return "kill-phrase"
    intent = router.route(phrase)
    return intent.tool if intent else ""


# ---------------------------------------------------------------------------
# STOPPING: "stop what you are doing, right now"
# ---------------------------------------------------------------------------
STOP_PHRASES = [
    "stop", "stop stop", "stop it", "stop now", "stop please", "please stop",
    "okay stop", "ok stop", "stop talking", "cancel", "abort", "halt",
    "quit it", "enough", "that's enough", "that is enough", "thats enough",
    "jalen stop", "jalen stop it", "jalen stop talking", "jalen enough",
]


@pytest.mark.parametrize("phrase", STOP_PHRASES)
def test_every_stop_phrase_stops_him(router, phrase):
    assert _reaches_something(router, phrase), (
        f"{phrase!r} does nothing at all - this is the emergency brake"
    )


@pytest.mark.parametrize("phrase", STOP_PHRASES)
def test_stopping_never_needs_his_name(phrase):
    """
    THE REGRESSION THIS FILE EXISTS FOR.

    The address gate makes Jalen ignore anything not addressed to him. Applied
    to the kill phrases, it would mean the one command you need under pressure
    is the one you have to preface correctly. It must stay exempt.
    """
    assert _Gate().should_act_on(phrase, wake_initiated=False), (
        f"{phrase!r} is now swallowed by the address gate - the emergency "
        f"stop needs his name, which is exactly backwards"
    )


def test_the_exemption_is_read_from_config_not_hardcoded():
    """
    A second copy of the list would drift, and the copy that drifts is the
    one in the safety path.
    """
    import inspect

    source = inspect.getsource(Jalen.should_act_on)
    assert "self.kill_phrases" in source
    # Comments are stripped first: the reasoning above the check names the
    # phrases in prose, and prose cannot drift out of sync with anything.
    code = " ".join(
        line for line in source.splitlines()
        if not line.lstrip().startswith("#")
    )
    body = code.split('"""')[-1]
    for literal in ('"stop"', "'stop'", '"abort"', '"cancel"'):
        assert literal not in body, (
            "kill phrases are hardcoded in should_act_on - they will drift "
            "from config/jarvis.yaml"
        )


# ---------------------------------------------------------------------------
# QUITTING: "we are finished, shut down"
# ---------------------------------------------------------------------------
QUIT_PHRASES = [
    "quit", "exit", "shutdown", "shut down", "close", "kill", "turn off",
    "log off", "logout", "goodbye", "good bye", "bye", "bye bye",
    "see you", "see ya", "goodnight", "good night",
    "that's all", "that is all", "thats all", "that will be all",
    "we're done", "we are done", "i'm done", "i am done", "i am finished",
    "turn yourself off", "switch yourself off", "power down",
    "quit yourself", "quit for now", "bye for today",
    "jalen quit", "jalen exit", "jalen goodbye", "hey jalen bye",
    "jaylen quit", "jarvis quit",
]


@pytest.mark.parametrize("phrase", QUIT_PHRASES)
def test_every_farewell_actually_quits(router, phrase):
    """
    A quit word that does not match is the one failure with no workaround:
    if "quit" fails, the remaining exit is killing the process by hand.
    """
    assert _reaches_something(router, phrase) == "jalen_quit", (
        f"{phrase!r} does not quit - it reaches "
        f"{_reaches_something(router, phrase) or 'nothing'}"
    )


# "not now" is NOT here: _normalise() strips a trailing "now" as filler, so
# it arrives as a bare "not". See the rule in router.py.
SLEEP_PHRASES = ["go to sleep", "sleep", "stand by", "stop listening",
                 "go away", "leave me alone", "later", "maybe later"]


@pytest.mark.parametrize("phrase", SLEEP_PHRASES)
def test_dismissal_sleeps_rather_than_quits(router, phrase):
    """
    "Go away" means stop bothering me, not uninstall yourself. Sleeping keeps
    the wake word live so "hey Jalen" brings him back; quitting would mean
    finding the launcher again.
    """
    assert _reaches_something(router, phrase) == "jalen_sleep"


# ---------------------------------------------------------------------------
# The gate still does its job for everything that ISN'T a stop word.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("overheard", [
    "stop the car",              # a film
    "we are done with dinner",   # a conversation in the room
    "I said bye to him",
    "cancel my subscription",    # dictated to someone else
    "goodbye everyone thanks for watching",
])
def test_a_sentence_that_merely_contains_a_stop_word_is_ignored(overheard):
    """
    The kill-phrase exemption is an EXACT match on the whole utterance, not a
    substring search. "Stop the car" in a film must not stop Jalen, or the
    exemption becomes the noise problem it was carved out of.
    """
    assert not _Gate().should_act_on(overheard, wake_initiated=False), (
        f"{overheard!r} would stop Jalen - the exemption is matching "
        f"substrings instead of whole utterances"
    )


def test_quit_words_do_still_need_his_name():
    """
    Unlike stopping, quitting is NOT exempt — and should not be. Stopping is
    instantly reversible; quitting means finding the launcher again. A film
    saying "goodbye" must not shut him down.
    """
    gate = _Gate()
    assert not gate.should_act_on("goodbye", wake_initiated=False)
    assert not gate.should_act_on("that's all", wake_initiated=False)
    assert gate.should_act_on("jalen goodbye", wake_initiated=False)
    assert gate.should_act_on("goodbye", wake_initiated=True)
