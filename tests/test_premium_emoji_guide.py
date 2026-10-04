"""
What the brain is TOLD about premium emoji: the post guide and the prompt.

The tools are only half of it. On 2026-08-26 the brain worked out the
<tg-emoji> tag by itself, and on 2026-08-29 it still said it had no access to
premium emoji, because nothing in what it is told connects "write the post"
to "look the ids up". The flow has to be written down where it reads it:

    guides -> compose -> find_premium_emoji -> put the tags in -> send
           -> read what the reply says about how the emoji arrived

and the guide has to list <tg-emoji> among the tags that are allowed - it
said "only these tags", and the tag was not one of them.
"""
from __future__ import annotations

import pytest

from jalen.config import CONFIG
from jalen.safety import SafetyEngine


def _flat(text: str) -> str:
    """Lower-cased with every run of whitespace as one space: a phrase that the
    file wraps onto two lines is still the phrase."""
    return " ".join(text.lower().split())


@pytest.fixture(scope="module")
def guide() -> str:
    from jalen.tools.voice import community_post_guide

    return community_post_guide()


@pytest.fixture(scope="module")
def prompt() -> str:
    from jalen.brain.agent import Brain

    async def noop(*a, **k):
        return True

    return Brain(CONFIG, SafetyEngine(CONFIG), None, confirm=noop,
                 announce=noop).system_prompt()


# ------------------------------------------------------------------- the guide
def test_the_guide_lists_the_premium_emoji_tag_as_allowed(guide):
    assert '<tg-emoji emoji-id="' in guide
    allowed = guide[guide.index("Telegram HTML: what is actually supported"):]
    assert "tg-emoji" in allowed.split("Anything else")[0]


def test_the_guide_says_the_character_inside_the_tag_stays(guide):
    low = _flat(guide)
    assert "fallback" in low
    assert "same emoji" in low or "the emoji that was there" in low


def test_the_guide_says_ids_come_from_the_lookup_and_are_never_written_from_memory(guide):
    low = _flat(guide)
    assert "find_premium_emoji" in guide
    assert "never" in low and ("invent" in low or "from memory" in low or "guess" in low)


def test_the_guide_keeps_the_one_emoji_per_slot_rule(guide):
    """Premium emoji replace the ordinary ones in the same places; they do not add more."""
    low = _flat(guide)
    assert "same place" in low or "same slot" in low or "same position" in low


def test_the_guide_names_every_house_emoji_so_the_brain_can_ask_for_them_in_one_call(guide):
    for char in ("\U0001f58a", "\U0001f680", "\U0001f6e3", "\U0001f4cc", "\U0001f44f",
                 "❓", "\U0001f447"):
        assert char in guide


def test_the_worked_example_still_has_no_premium_markup_in_it(guide):
    """
    The example is the structure. Ids in it would be copied into every post:
    a wrong id in one post is a broken emoji in his channel, in all of them.
    """
    example = guide[guide.index("Worked example"):]
    assert "tg-emoji" not in example


def test_the_sticker_rule_is_in_the_guide(guide):
    """A sticker is its own message, and only when he asked (his decision, 2026-10-01)."""
    low = _flat(guide)
    assert "sticker" in low and "own message" in low and "send_sticker" in guide
    assert "only when he asks" in low


# ------------------------------------------------------------------ the prompt
def test_the_prompt_puts_the_lookup_between_writing_and_sending(prompt):
    start = prompt.index("TURNING AN EMAIL INTO A CHANNEL POST")
    section = prompt[start: start + 6000]
    assert "find_premium_emoji" in section
    assert section.index("community_post_guide") < section.index("find_premium_emoji")
    assert section.index("find_premium_emoji") < section.index("send_telegram_message")


def test_the_prompt_says_to_read_the_reply_about_how_the_emoji_arrived(prompt):
    low = _flat(prompt)
    assert "premium emoji arrived" in low or "read it back" in low
    assert "premium" in low and "ordinary" in low


def test_the_prompt_says_to_use_the_channel_he_already_posts_in_as_the_pattern(prompt):
    assert "learn_from" in prompt


def test_the_prompt_says_a_sticker_goes_as_its_own_message_when_he_asks(prompt):
    low = _flat(prompt)
    assert "send_sticker" in prompt
    assert "own message" in low or "separate message" in low


def test_the_prompt_never_tells_the_brain_to_confirm_the_preapproved_channel(prompt):
    """The pre-approval is deliberate; the new text must not undo it."""
    start = prompt.index("TURNING AN EMAIL INTO A CHANNEL POST")
    assert "ask him before sending" not in prompt[start: start + 6000].lower()


def test_the_prompt_says_the_old_answer_was_wrong(prompt):
    """
    'I don't have access to premium emoji' was the answer on 2026-08-29.
    The prompt must say plainly that he can have them and how.
    """
    low = _flat(prompt)
    assert "premium emoji" in low
    assert "do not tell him you can't" in low or "never say you can't" in low or "never say you cannot" in low
