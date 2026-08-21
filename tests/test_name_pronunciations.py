"""
However he says it, and however Whisper spells it, the command must work.

His words: "I am not being able to speak jalen properly and it is not
actually properly hearing that from me, man what am I going to do now? many
people speak it different could you please create 1000 versions of it...
and alongside it jarvis should work as well, Jarvis should work everywhere."

An enumerated list only covers the spellings somebody thought of, so the
name is matched by SHAPE — j + vowel + l + vowel + n. This file is the proof
that the shape is wide enough to be useful and tight enough to be safe.

The tight half matters as much as the wide half. A pattern that also
swallowed "Alan" or "Julian" would strip a real person out of a sentence and
silently send a message to the wrong human being.
"""
from __future__ import annotations

import pytest

from jarvis.brain.router import IntentRouter
from jarvis.config import CONFIG


@pytest.fixture(scope="module")
def router() -> IntentRouter:
    return IntentRouter(CONFIG)


# Every spelling Whisper plausibly produces for "Jalen", across accents.
JALEN_SPELLINGS = [
    "jalen", "jaylen", "jaylin", "jalin", "jaelen", "jailen", "jalon",
    "jaleen", "jalene", "jayleen", "jaylan", "jhalen", "jaylene", "jalyn",
    "jalan", "jalun", "jaylon", "jeylen", "jailin", "jayline", "jalens",
    "jelen", "jilen", "jolen", "julen", "jaalen", "jaylenn", "jallen",
]

# And for "Jarvis", which he still says constantly and must keep working.
JARVIS_SPELLINGS = [
    "jarvis", "jarvus", "jervis", "jarvez", "jaros", "jarvers",
    "jarves", "jorvis", "jarwis", "jarris", "jaris",
]

ALL_SPELLINGS = JALEN_SPELLINGS + JARVIS_SPELLINGS


@pytest.mark.parametrize("spelling", ALL_SPELLINGS)
def test_the_name_is_stripped_before_a_command(router, spelling):
    """"<name> quit" must reach the quit rule, not the LLM."""
    hit = router.route(f"{spelling} quit")
    assert hit is not None, f"{spelling!r} is not recognised as his name"
    assert hit.tool == "jalen_quit"


@pytest.mark.parametrize("spelling", ALL_SPELLINGS)
def test_the_name_works_with_every_lead_in(router, spelling):
    """
    "Hey", "Hi" and "OK" all precede it in real speech, and the endpointer
    punctuates with full stops — "Jalen. Hey Jalen. Quit." is a real
    transcript from his session that used to match nothing at all.
    """
    for phrase in (
        f"hey {spelling} quit",
        f"hi {spelling} quit",
        f"ok {spelling}, quit",
        f"{spelling}. quit.",
        f"{spelling}! quit",
    ):
        hit = router.route(phrase)
        assert hit is not None and hit.tool == "jalen_quit", f"{phrase!r} missed"


@pytest.mark.parametrize("spelling", ALL_SPELLINGS)
def test_the_bare_name_is_still_answered(router, spelling):
    """
    Calling his name and nothing else is a summons. It broke once already:
    the "?" in "Jalen?" counted as a separator and the whole utterance was
    stripped to nothing.
    """
    for phrase in (spelling, f"{spelling}?", f"hey {spelling}"):
        hit = router.route(phrase)
        assert hit is not None and hit.tool == "jalen_ack", f"{phrase!r} missed"


# --------------------------------------------------------------- the guard
# Real words and real names that must NEVER be treated as his name. A false
# positive here is worse than a miss: it deletes a word out of the middle of
# a real instruction.
NOT_HIS_NAME = [
    "alan", "galen", "helen", "ellen", "allen", "alien",
    "julian", "jolene", "jason", "james", "jack", "john",
    "salon", "talon", "melon", "felon", "gallon", "golden",
    "silent", "solution", "million", "italian", "javelin",
]


@pytest.mark.parametrize("word", NOT_HIS_NAME)
def test_ordinary_words_are_not_mistaken_for_his_name(word, router):
    """
    "tell Alan I'm late" must not become "tell I'm late" — that sends a
    message about being late to whoever Jalen guesses at instead.
    """
    normalised = router._normalise(f"{word} quit")
    assert normalised.startswith(word), (
        f"{word!r} was stripped as if it were his name (left {normalised!r})"
    )


@pytest.mark.parametrize("name", ["Alan", "Julian", "Helen", "Jolene"])
def test_a_real_person_survives_a_message_command(router, name):
    """The end-to-end version: the recipient must reach the tool intact."""
    hit = router.route(f"telegram {name} saying I'm running late")
    assert hit is not None and hit.tool == "send_telegram_message"
    assert hit.args["to"].lower() == name.lower(), (
        f"the recipient became {hit.args['to']!r} — the message would go to the wrong person"
    )


# ------------------------------------------------------- heard, not spoken
def test_the_old_name_is_understood_but_never_said(router):
    """
    Both halves of what he asked, which are not in conflict: "Jarvis should
    work everywhere" (input) and "I do not wanna hear about Jarvis" (output).
    """
    assert router.route("jarvis quit").tool == "jalen_quit", "the old name stopped working"

    stale = [
        reply for _p, _t, _b, reply in router._rules
        if reply and "jarvis" in reply.lower()
    ]
    assert not stale, f"a reply still says the old name out loud: {stale}"

    from jarvis.config import CONFIG as cfg

    assert "jarvis" not in str(cfg.get_path("identity.name", "")).lower()
    assert "jarvis" not in str(cfg.get_path("persona.style", "")).lower()


@pytest.mark.parametrize(
    "phrase, tool",
    [
        ("hey jarvis what time is it", "get_time"),
        ("jarvis, open chrome", "open_target"),
        ("hey jaylen mute", "jalen_mute"),
        ("jalon, screenshot", "screenshot"),
        ("jervis restart", "jalen_restart"),
    ],
)
def test_mixed_names_reach_the_same_commands(router, phrase, tool):
    hit = router.route(phrase)
    assert hit is not None and hit.tool == tool, f"{phrase!r} -> {hit.tool if hit else None}"
