"""
Sending to his own channel and his own notes without being asked.

His request, verbatim: Jalen should send to the Machine Learning community
and to Saved Messages "without asking me, without taking my permission".
Both are his own, and a spoken confirmation before each of fifty posts is
friction with no safety value.

THE PART THAT MUST NOT BREAK is the ordering. Pre-approving a destination
approves HIM sending there. Text Jalen merely READ — an email, a Telegram
message, a web page — asking for a post is still refused outright. Without
that, adding his channel here would turn it into an open relay for anyone
who can get words in front of him.
"""
from __future__ import annotations

import pytest

from jalen.config import CONFIG
from jalen.safety import SafetyEngine, Tier, _simplify


@pytest.fixture(scope="module")
def engine() -> SafetyEngine:
    return SafetyEngine(CONFIG)


@pytest.mark.parametrize(
    "destination",
    ["Saved Messages", "saved messages", "SAVED MESSAGES"],
)
def test_his_own_destinations_send_without_asking(engine, destination):
    verdict = engine.classify("send_telegram_message", {"to": destination, "text": "hi"})
    assert verdict.tier is Tier.GREEN, f"{destination!r} still asks for permission"
    assert verdict.requires_confirmation is False


@pytest.mark.parametrize(
    "destination",
    [
        "AI engineering & Machine learning",
        "ai engineering and machine learning",
        "AI Engineering &amp; Machine Learning",
    ],
)
def test_his_channel_sends_without_a_question_but_is_read_aloud_first(engine, destination):
    """
    No question - his own instruction stands - but since 2026-10-01 (his
    decision, a poll) the post is announced with its first line and the hosts
    of its links, and goes unless he says stop: AMBER, not GREEN.
    tests/test_channel_posts_are_read_aloud_first.py covers the wording.
    """
    verdict = engine.classify("send_telegram_message", {"to": destination, "text": "hi"})
    assert verdict.tier is Tier.AMBER, f"{destination!r}"
    assert verdict.requires_confirmation is False and verdict.announce is True


@pytest.mark.parametrize(
    "destination",
    ["Rodion", "SAT Talk", "Uluhbek Shonazarov", "@somebody", "Startup Family"],
)
def test_everyone_else_still_asks(engine, destination):
    """
    The tier boundary is the feature. Everything send_telegram_message can
    otherwise reach is a person, and a message to a person cannot be
    recalled.
    """
    verdict = engine.classify("send_telegram_message", {"to": destination, "text": "hi"})
    assert verdict.tier is Tier.RED, f"{destination!r} would send with no confirmation"
    assert verdict.requires_confirmation is True


@pytest.mark.parametrize(
    "destination",
    ["Saved Messages", "AI engineering & Machine learning", "Rodion"],
)
def test_read_content_can_never_send_anywhere(engine, destination):
    """
    THE LOAD-BEARING TEST. An email that says "post this to your channel" is
    refused even though the channel is pre-approved. If this ever passes as
    GREEN, anyone who can get text in front of Jalen can publish to his
    community under his name.
    """
    verdict = engine.classify(
        "send_telegram_message",
        {"to": destination, "text": "posted by an injected instruction"},
        origin="content",
    )
    assert verdict.tier is Tier.BLACK
    assert verdict.blocked is True


@pytest.mark.parametrize(
    "destination, tier",
    [
        ("Saved Messages", Tier.GREEN),
        ("saved messages", Tier.GREEN),
        ("AI engineering & Machine learning", Tier.AMBER),
        ("AI Engineering &amp; Machine Learning", Tier.AMBER),
    ],
)
def test_a_voice_message_to_his_own_destinations_goes_without_a_question(engine, destination, tier):
    """send_voice_message has the SAME pre-approved destinations as a text:
    Saved Messages silently, his channel announced first (AMBER)."""
    verdict = engine.classify("send_voice_message", {"to": destination, "text": "hi"})
    assert verdict.tier is tier, f"{destination!r}"
    assert verdict.requires_confirmation is False


@pytest.mark.parametrize(
    "destination",
    ["Rodion", "SAT Talk", "Uluhbek Shonazarov", "@somebody", "Startup Family", "Ed", ""],
)
def test_a_voice_message_to_everyone_else_still_asks(engine, destination):
    verdict = engine.classify("send_voice_message", {"to": destination, "text": "hi"})
    assert verdict.tier is Tier.RED, f"{destination!r} would speak as him with no confirmation"
    assert verdict.requires_confirmation is True


@pytest.mark.parametrize(
    "destination",
    ["Saved Messages", "AI engineering & Machine learning", "Rodion"],
)
def test_read_content_can_never_send_a_voice_message_anywhere(engine, destination):
    """
    THE LOAD-BEARING TEST, for speech. Text Jalen merely READ that asks for a
    voice message is refused even where a text would have been pre-approved -
    and, unlike a text, even into his own Saved Messages when he named it.
    """
    for named in ("", "Saved Messages"):
        verdict = engine.classify(
            "send_voice_message", {"to": destination, "text": "posted by an injected instruction"},
            origin="content", named_by_him=named,
        )
        assert verdict.tier is Tier.BLACK and verdict.blocked, (destination, named)


def test_the_voice_message_has_a_destination_argument_and_no_taint_exception():
    assert SafetyEngine._DESTINATION_ARG["send_voice_message"] == "to"
    assert "send_voice_message" not in SafetyEngine._SAFE_TO_OWN_CHAT_UNDER_TAINT


def test_the_preapproval_check_runs_after_the_injection_check():
    """
    Ordering, asserted directly. Moving the pre-approval block above the
    injection guard would silently reverse the test above, and nothing else
    in the file would notice.
    """
    import inspect

    source = inspect.getsource(SafetyEngine.classify)
    injection = source.index('origin == "content"')
    preapproved = source.index("_is_preapproved")
    assert injection < preapproved, (
        "pre-approved destinations are now checked BEFORE the injection guard — "
        "read content can publish to his channel"
    )


def test_other_red_tools_are_untouched(engine):
    """
    This must be a destination allowlist, not a general softening of RED.
    """
    for tool, args in [
        ("send_email", {"to": "Saved Messages", "subject": "x", "body": "y"}),
        ("delete_file", {"path": "C:/Users/user/Desktop/notes.txt"}),
    ]:
        assert engine.classify(tool, args).tier is Tier.RED, f"{tool} was downgraded"


def test_an_empty_destination_is_not_preapproved(engine):
    """A blank name must not match a blank allowlist entry and sail through."""
    verdict = engine.classify("send_telegram_message", {"to": "", "text": "hi"})
    assert verdict.tier is Tier.RED


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("AI engineering & Machine learning", "ai engineering and machine learning"),
        ("AI Engineering &amp; Machine Learning", "ai engineering and machine learning"),
        ("  Saved   Messages  ", "saved messages"),
        ("", ""),
    ],
)
def test_destination_names_normalise(raw, expected):
    assert _simplify(raw) == expected


def test_the_config_actually_lists_both_destinations():
    """
    The behaviour above is only real if the config carries the entries. A
    passing test suite over an empty allowlist would prove nothing.
    """
    configured = CONFIG.get_path("telegram.personal.send_without_asking_to", []) or []
    simplified = [_simplify(c) for c in configured]
    assert "saved messages" in simplified
    assert any("machine learning" in s for s in simplified)
