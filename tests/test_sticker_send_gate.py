"""
send_sticker goes through the same gate as send_telegram_message.

He asked for it in those words: a sticker send has "the same tier and the
same pre-approved-destination handling as send_telegram_message". So this
file does not restate the rules; it runs BOTH tools through every
(destination, origin) pair and requires the same verdict, with exactly one
documented difference.

THE ONE DIFFERENCE, AND WHY IT IS DELIBERATE
--------------------------------------------
After Jalen has read somebody's email, a send to his OWN Saved Messages is
let through when he named it himself (safety.py, step 2). That exception is
written for TEXT: a summary into his own notebook. A sticker is a choice of
WHICH document to send, which is exactly what an injected instruction would
pick - the reason send_telegram_file is excluded from the same exception
(tests/test_preapproval_is_exact.py). send_sticker follows the file tool, not
the text tool. It costs him nothing real: a sticker to his own notebook after
reading an email is not a flow anyone has asked for.

The ordering invariant (content check above the pre-approval downgrade) is
pinned by tests/test_adversarial.py and tests/test_preapproved_sends.py and is
not re-tested here; what is tested here is that the new tool is on the right
side of it.
"""
from __future__ import annotations

import pytest
import yaml

from jarvis.config import CONFIG
from jarvis.safety import SafetyEngine, Tier

CHANNEL = "AI engineering & Machine learning"

_SAFETY_YAML = yaml.safe_load(open("config/safety.yaml", encoding="utf-8"))


@pytest.fixture
def engine():
    e = SafetyEngine(CONFIG)
    e.paranoid = False
    e.posture = "irreversible_only"
    return e


def _args(tool, to):
    if tool == "send_sticker":
        return {"to": to, "emoji": "\U0001f525", "pack": "Tech Memes"}
    return {"to": to, "text": "a post"}


def _verdict(engine, tool, to, origin="user", named_by_him=""):
    return engine.classify(tool, _args(tool, to), origin=origin, named_by_him=named_by_him)


DESTINATIONS = [
    CHANNEL, "ai engineering & machine learning", "AI Engineering &amp; Machine Learning",
    "Saved Messages", "me", "my notes",
    "Rodion", "Uluhbek", "@durov", "+998901234567",
    "Ed", "a", "Machine learning", "Saved Messages backup group", "",
]


# ----------------------------------------------------------------- the tier
def test_send_sticker_is_red_like_the_text_send():
    assert "send_sticker" in _SAFETY_YAML["red"]["tools"]
    assert "send_telegram_message" in _SAFETY_YAML["red"]["tools"]


def test_the_two_read_tools_are_green():
    for tool in ("list_sticker_packs", "find_premium_emoji"):
        assert tool in _SAFETY_YAML["green"]["tools"], tool


def test_the_read_tools_are_not_on_the_refuse_from_content_list():
    """
    That list is for GREEN tools that ACT on the world. Reading his own emoji
    sets acts on nothing, and refusing them under taint would stop a post
    that was started before he read a page from getting its emoji.
    """
    refused = _SAFETY_YAML["injection_guard"]["refuse_from_content"]
    for tool in ("list_sticker_packs", "find_premium_emoji"):
        assert tool not in refused, tool


# --------------------------------------------- the same verdict, every time
@pytest.mark.parametrize("to", DESTINATIONS)
@pytest.mark.parametrize("origin", ["user", "content"])
def test_a_sticker_gets_the_text_sends_verdict(engine, to, origin):
    sticker = _verdict(engine, "send_sticker", to, origin)
    text = _verdict(engine, "send_telegram_message", to, origin)
    assert sticker.tier is text.tier, (
        f"{to!r} / {origin}: the sticker is {sticker.tier}, the text send is {text.tier}"
    )
    assert sticker.requires_confirmation == text.requires_confirmation
    assert sticker.blocked == text.blocked


@pytest.mark.parametrize("to", [CHANNEL, "Saved Messages", "me"])
def test_his_own_destinations_go_without_asking(engine, to):
    assert _verdict(engine, "send_sticker", to).tier is Tier.GREEN


@pytest.mark.parametrize("to", ["Rodion", "Uluhbek", "@durov", "Ed", "a", ""])
def test_everyone_else_still_needs_a_yes(engine, to):
    verdict = _verdict(engine, "send_sticker", to)
    assert verdict.tier is Tier.RED and verdict.requires_confirmation is True


@pytest.mark.parametrize("to", [CHANNEL, "Saved Messages", "Rodion"])
def test_a_page_cannot_make_him_send_a_sticker_anywhere(engine, to):
    """THE LOAD-BEARING ONE, for the new tool."""
    verdict = _verdict(engine, "send_sticker", to, origin="content")
    assert verdict.tier is Tier.BLACK and verdict.blocked is True


@pytest.mark.parametrize("to", [CHANNEL, "Rodion", "Ed", "a"])
def test_naming_saved_messages_licenses_nothing_else(engine, to):
    verdict = _verdict(engine, "send_sticker", to, origin="content",
                       named_by_him="Saved Messages")
    assert verdict.tier is Tier.BLACK


def test_the_one_deliberate_difference_is_pinned(engine):
    """
    Text to his own Saved Messages survives a read when he named it; a
    sticker does not. If someone widens this, it should be on purpose.
    """
    text = _verdict(engine, "send_telegram_message", "Saved Messages",
                    origin="content", named_by_him="Saved Messages")
    sticker = _verdict(engine, "send_sticker", "Saved Messages",
                       origin="content", named_by_him="Saved Messages")
    assert text.tier is Tier.GREEN
    assert sticker.tier is Tier.BLACK
    assert "send_sticker" not in SafetyEngine._SAFE_TO_OWN_CHAT_UNDER_TAINT


def test_the_destination_argument_is_the_same_one_the_tool_takes():
    """If the gate looked at the wrong argument, 'to' would never match."""
    assert SafetyEngine._DESTINATION_ARG["send_sticker"] == "to"
    import inspect

    from jarvis.tools import stickers

    assert "to" in inspect.signature(stickers.send_sticker).parameters


def test_the_preapproval_is_still_after_the_injection_check():
    """The same ordering test as test_preapproved_sends, with this tool in it."""
    import inspect

    source = inspect.getsource(SafetyEngine.classify)
    assert source.index('origin == "content"') < source.index("_is_preapproved")


# ------------------------------------------- the rest of the system knows it
def test_a_draft_or_a_read_request_can_never_become_a_sticker_send():
    """plan.py's forbidden list: 'draft it' must not run send_sticker."""
    from jarvis import plan

    for verb in ("draft", "save", "read"):
        assert "send_sticker" in plan.ACTIONS[verb], verb


def test_a_sticker_send_counts_as_real_work_for_the_rating_prompt():
    from jarvis.tools import feedback

    assert "send_sticker" in feedback._REAL_WORK
