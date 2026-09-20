"""
A greeting followed by a comma made his name unrecognisable.

The same bug, in the same function, on the other side of the name. router.py's
own comment at :166-172 is the post-mortem of the AFTER side:

    This was [,\\s]+ - comma or whitespace. Speech recognition punctuates with
    FULL STOPS, so "Jalen. Hey Jalen. Quit." (his words, 15:54:40) stripped
    nothing, matched nothing, went to the LLM, and did not quit.

_AFTER_NAME was widened to [.,!?;:\\s]+ and the PREFIX group was left as
r"^\\s*(?:hey |hi |yo |ok |okay )?" -- a literal space. So "Hey Jalen, play it"
matched and "Hey, Jalen, play it" did not, for exactly the reason the comment
above already explains.

VERBATIM FROM data/audit.jsonl, 2026-08-31T15:43:00Z, session 7b3bcee850cb:

    "Hey, Jalen, could you please play Hurtless Dean Lewis?"   -> discarded

He repeated himself twelve seconds later without the comma and it worked.

MEASURED against the real corpora before writing this (853 user utterances,
804 jarvis utterances, 1661 jarvis sentences, 97 gate discards):
    discards recovered ............ 1
    NEW jarvis self-echo admits ... 0   <- the hard constraint
    user utterances regressed ..... 0

The self-echo zero is structural, not luck: every pinned negative dies on
_NAME's own deny-list lookahead, which a greeting prefix cannot reach. That is
why widening the separator is safe while widening the WORD LIST is not --
adding _normalise's so|well|actually would admit third-person sentences about
him ("So Jalen is my assistant"), which the last test here pins shut.

Note "Hey Jillian, hand me that" is ALREADY True at HEAD. This change extends a
pre-existing _NOT_THE_NAME gap to punctuated forms rather than creating a new
class of error, and there are 0 such occurrences in the real log.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.app import Jalen  # noqa: E402
from jarvis.brain import router  # noqa: E402
from jarvis.brain.router import addressed_to_jalen  # noqa: E402
from jarvis.config import CONFIG  # noqa: E402


class _Gate:
    """Mirrors tests/test_address_gate.py's stub. Deliberately a copy rather
    than an import: tests/ has no __init__.py, and that file owns its own
    fixture. If Jalen grows an attribute should_act_on reads, BOTH stubs must
    gain it -- a stub that silently lacks one leaves the real path broken while
    every test here stays green."""

    def __init__(self):
        self._awaiting_confirmation = False
        self._awaiting_stop = False
        self._awaiting_reply = False
        self._pending_rating = None
        # Added when Jalen grew a question window; this stub's own
        # docstring says both stubs must gain any new attribute, or the
        # real path breaks while these stay green.
        self._expecting = None
        self._last_reply_text = ""
        self._last_user_text = ""
        self._last_user_at = 0.0
        self.cfg = CONFIG
        self.kill_phrases = set(CONFIG.get_path("safety.kill_phrases"))

    should_act_on = Jalen.should_act_on
    _continues_last_utterance = Jalen._continues_last_utterance
    _expectation_open = Jalen._expectation_open
    _sounds_like_its_own_voice = Jalen._sounds_like_its_own_voice


THE_LOGGED_DISCARD = "Hey, Jalen, could you please play Hurtless Dean Lewis?"

PUNCTUATED_GREETINGS = [
    THE_LOGGED_DISCARD,
    "Hey, Jalen, play it",
    "Hi, Jalen, play it",
    "Okay, Jalen, play it",
    "Yo, Jalen, play it",
    "OK, Jalen, play it",
    # STT punctuates with full stops -- the very finding router.py:166-172
    # records. The AFTER side already accepts this; the prefix now does too.
    "Hey. Jalen, play it",
    "Okay. Jalen, play it",
]


@pytest.mark.parametrize("text", PUNCTUATED_GREETINGS)
def test_a_greeting_may_be_punctuated(text):
    """All False at HEAD. The first entry is the verbatim logged discard."""
    assert addressed_to_jalen(text), f"{text!r} is plainly addressed to him"


def test_the_gate_and_the_router_no_longer_disagree():
    """The actual defect, and the reason this is not cosmetic: the router
    ALREADY routes these to real tools, so the gate was discarding utterances
    the rest of the system was ready to act on."""
    gate = _Gate()
    routing = router.IntentRouter(CONFIG)

    for text, expected_tool in (
        ("Hey, Jalen, quit", "jalen_quit"),
        ("Hey, Jalen, mute", "jalen_mute"),
    ):
        intent = routing.route(text)
        assert intent is not None, f"router does not route {text!r} at all"
        assert intent.tool == expected_tool, f"{text!r} -> {intent.tool}"
        # wake_initiated=False is the whole point: should_act_on returns True
        # immediately on a wake word, so the only interesting case is the one
        # where the name inside the sentence has to carry it.
        assert gate.should_act_on(text, False), (
            f"the router routes {text!r} to {expected_tool} but the gate "
            "discards it -- that disagreement is the bug"
        )


SOMEONE_ELSE_PUNCTUATED = [
    # The deny-list lookahead inside _NAME must still fire THROUGH a
    # punctuated prefix. This is the first time it is reached that way.
    "Okay, Julian said he'd call back",
    "Hey, Jolene is a good song",
    "Hi, Juliana, can you hear me",
    "Hey, jalapeno poppers are good",
]


@pytest.mark.parametrize("text", SOMEONE_ELSE_PUNCTUATED)
def test_a_punctuated_prefix_does_not_reach_past_the_deny_list(text):
    assert not addressed_to_jalen(text)


OTHER_ASSISTANTS = [
    # A television saying any of these must never drive this machine. Each
    # remainder would otherwise reach a GREEN tool: play_media at
    # config/safety.yaml:66, open_target at :129.
    "Hey, Google, play some music",
    "Hey, Siri, open Chrome",
    "Hey, Alexa, play the news",
    "OK, Google, what's the weather",
    "Hey, Cortana, open my email",
]


@pytest.mark.parametrize("text", OTHER_ASSISTANTS)
def test_another_assistants_wake_phrase_is_still_refused(text):
    assert not addressed_to_jalen(text)


MUST_NOT_MATCH_ZERO_CHARACTERS = [
    # The prefix group is optional, so it must not be satisfiable by matching
    # nothing and then finding a name that is not there.
    "history of rome",
    "hiking boots",
    "okay",
    "hey",
    "Okayjalen",
    "heyjalen stop",
    "hijalen",
]


@pytest.mark.parametrize("text", MUST_NOT_MATCH_ZERO_CHARACTERS)
def test_the_prefix_still_requires_a_real_separator(text):
    assert not addressed_to_jalen(text)


THIRD_PERSON_ABOUT_HIM = [
    # Pins the greeting WORD LIST shut. A future "make this consistent with
    # _normalise" edit would add so|well|actually|um|uh and admit these, which
    # are sentences ABOUT him rather than TO him.
    "So Jalen is my assistant",
    "Well, Jalen is a wrapper around Claude",
    "Actually, Jalen handles that for me",
    "Um, Jalen was the one who sent it",
]


@pytest.mark.parametrize("text", THIRD_PERSON_ABOUT_HIM)
def test_the_greeting_word_list_stays_frozen(text):
    """These must stay False. If one starts passing, someone widened the word
    list rather than the separator -- a different and unmeasured change."""
    assert not addressed_to_jalen(text)


def test_the_unpunctuated_forms_never_regressed():
    """The whole existing corpus shape, spot-checked here so a separator edit
    that broke the plain forms fails in this file too, not only in the
    neighbouring one."""
    for text in ("Hey Jalen, open Chrome", "hey jalen open chrome",
                 "OK Jalen, summarise my emails", "Okay Jalen stop",
                 "Jalen?", "Jalen.", "Jalen", "Jarvis, stop"):
        assert addressed_to_jalen(text), f"regressed: {text!r}"
