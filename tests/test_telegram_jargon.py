"""
"Telegram X saying Y" — the jargon he asked for, and the line it must not cross.

Two forms that look alike and mean completely different things:

    telegram Rodion SAYING I'll be late     the words are already written
    telegram Rodion ABOUT the meeting       asks Jalen to compose something

Only the first is routed. Routing the second would deliver the literal string
"the meeting" to a real person under his name — a fast wrong answer, which
here is far worse than a slow right one. The "about" form falls through to
the brain, which calls voice_guide and writes it as him.

Every phrase below came from data/router_misses.log or from how he actually
described the feature.
"""
from __future__ import annotations

import pytest

from jalen.brain.router import IntentRouter
from jalen.config import CONFIG


@pytest.fixture(scope="module")
def router() -> IntentRouter:
    return IntentRouter(CONFIG)


@pytest.mark.parametrize(
    "phrase, to, text",
    [
        ("telegram sat talk saying hello there", "sat talk", "hello there"),
        ("telegram Rodion saying I'll be late", "Rodion", "I'll be late"),
        ("message sat talk saying the meeting moved", "sat talk", "the meeting moved"),
        ("text Rodion saying on my way", "Rodion", "on my way"),
        ("dm sat talk with the message hello", "sat talk", "hello"),
        ("telegram Rodion to say I'm outside", "Rodion", "I'm outside"),
        ("telegram saved messages that this is a test", "saved messages", "this is a test"),
        ("tell Rodion on telegram that I'm running late", "Rodion", "I'm running late"),
        ("message sat talk on telegram saying hi", "sat talk", "hi"),
        ("jalen telegram saved messages saying hello", "saved messages", "hello"),
    ],
)
def test_literal_messages_route_without_an_llm(router, phrase, to, text):
    hit = router.route(phrase)
    assert hit is not None, f"{phrase!r} fell through to Claude"
    assert hit.tool == "send_telegram_message"
    assert hit.args["to"] == to
    assert hit.args["text"] == text


def test_the_on_telegram_qualifier_is_not_swallowed_into_the_name(router):
    """
    Ordering bug, caught by testing rather than reasoning: with the generic
    rule first, "message sat talk on telegram saying hi" split at the first
    "saying" and captured the recipient as "sat talk on telegram" — a chat
    that does not exist, so the send would simply fail.
    """
    hit = router.route("message sat talk on telegram saying hi")
    assert hit.args["to"] == "sat talk"


@pytest.mark.parametrize(
    "phrase",
    [
        "telegram Rodion about the meeting tomorrow",
        "telegram sat talk about the new research lab",
        "message Rodion about the deadline",
        "telegram the ML community about the NLP opportunity",
    ],
)
def test_about_is_left_for_the_brain_to_compose(router, phrase):
    """
    "About X" is a writing task, not a send task. If this ever starts
    routing, the literal words after "about" get delivered to a person.
    """
    hit = router.route(phrase)
    assert hit is None or hit.tool != "send_telegram_message", (
        f"{phrase!r} would have sent the literal text {hit.args.get('text')!r} "
        "instead of composing a message"
    )


@pytest.mark.parametrize(
    "phrase, expected",
    [
        ("telegram Rodion saying I'll be late", "I'll be late"),
        ("telegram Rodion saying Meeting is at 3 PM in Room B", "Meeting is at 3 PM in Room B"),
        ("telegram sat talk saying I'm applying to MIT and Stanford", "I'm applying to MIT and Stanford"),
    ],
)
def test_the_message_keeps_its_capitalisation(router, phrase, expected):
    """
    The router lowercases everything before matching, and arguments used to
    be taken from that lowercased string. So a message going out under his
    name, to a real person, arrived as "i'll be late" and "mit and stanford".
    Matching is lowercase; the ARGUMENTS are not.
    """
    hit = router.route(phrase)
    assert hit is not None
    assert hit.args["text"] == expected


def test_sending_is_still_gated(router):
    """
    Making the phrasing free must not make the send unconfirmed. This
    reaches a person and cannot be recalled.
    """
    from jalen.safety import SafetyEngine, Tier

    hit = router.route("telegram Rodion saying hello")
    verdict = SafetyEngine(CONFIG).classify(hit.tool, hit.args)
    assert verdict.tier is Tier.RED


def test_normalise_case_modes_agree_on_everything_but_case():
    """
    The two passes must strip identically, or the cased match will disagree
    with the lowercase one and arguments will come from the wrong groups.
    """
    phrases = [
        "Hey Jalen, could you please open Telegram for me",
        "Jalen, telegram Rodion saying I'll be late",
        "OK Jalen open Chrome now please",
        "Telegram SAT Talk saying Hello There",
    ]
    for phrase in phrases:
        lowered = IntentRouter._normalise(phrase)
        cased = IntentRouter._normalise(phrase, lower=False)
        assert cased.lower() == lowered, (
            f"{phrase!r}: case-preserving pass produced {cased.lower()!r}, "
            f"lowercase pass produced {lowered!r}"
        )
