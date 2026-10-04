"""
Posts to his channel went out with nobody hearing them first.

His channel ("AI engineering & Machine learning") is pre-approved: sends go
out GREEN, no question, no announcement - his own instruction, because a
spoken yes before every post is friction. The independent review of the
injection guard named the one gap left: something Jalen read earlier in the
same conversation can still be in the model's context when he later says
"post today's summary to my channel", and the post can carry words or a link
nobody chose. The gate cannot see that - the turn is clean, the destination
approved.

HIS DECISION, 2026-10-01 (asked as a poll, 'Recommended' picked): Jalen reads
the first line of the post and any links aloud before it goes, and posts
unless he says stop within the short window every AMBER action has.

So a pre-approved send to the CHANNEL is AMBER: announced, then run. His own
Saved Messages stays GREEN - it reaches nobody. A draft reaches nobody, so it
stays GREEN. Everything else that is not pre-approved is still RED, and
anything that came from read content is still BLACK (ordering unchanged:
tests/test_adversarial.py pins it).

The announcement carries what he needs to hear: the first line with the
markup taken out, and the HOSTS of the links - "links to forms.gle", not a
spoken URL - so a link he did not write is the thing that stands out.
"""
from __future__ import annotations

import pytest

from jalen.config import CONFIG
from jalen.safety import SafetyEngine, Tier

CHANNEL = "AI engineering & Machine learning"
SAVED = "Saved Messages"


@pytest.fixture
def engine():
    e = SafetyEngine(CONFIG)
    e.paranoid = False
    e.posture = "irreversible_only"
    return e


def _channel(engine, tool="send_telegram_message", **args):
    return engine.classify(tool, {"to": CHANNEL, **args})


def test_a_text_post_to_the_channel_is_announced_not_silent(engine):
    v = _channel(engine, text="<b>Lab Opportunity</b>\n\nApply: https://forms.gle/abc?x=1")
    assert v.tier is Tier.AMBER
    assert v.announce is True and v.requires_confirmation is False and v.blocked is False


def test_the_announcement_carries_the_first_line_without_markup(engine):
    v = _channel(engine, text="<b>Lab Opportunity</b> for NLP students\n\nsecond line")
    assert "Lab Opportunity for NLP students" in v.summary
    assert "<b>" not in v.summary and "second line" not in v.summary


def test_the_announcement_names_the_hosts_of_the_links_not_the_urls(engine):
    v = _channel(engine, text="Apply here https://forms.gle/abcDEF123?usp=sf_link "
                              "and <a href=\"https://bit.ly/xyz\">details</a>")
    assert "forms.gle" in v.summary and "bit.ly" in v.summary
    assert "abcDEF123" not in v.summary and "usp=sf_link" not in v.summary


def test_many_links_are_counted_not_all_read(engine):
    text = " ".join(f"https://site{i}.example/p" for i in range(6))
    v = _channel(engine, text=text)
    assert "site0.example" in v.summary
    assert "more" in v.summary and "site5.example" not in v.summary


def test_a_post_with_no_links_says_so(engine):
    v = _channel(engine, text="Hello everyone, new paper discussion tonight")
    assert "no links" in v.summary.lower()


def test_a_long_first_line_is_clipped(engine):
    v = _channel(engine, text="word " * 200)
    assert len(v.summary) < 400


def test_a_batch_of_posts_says_how_many_and_starts_with_the_first(engine):
    v = _channel(engine, "send_posts", posts=["<b>First post</b> body", "Second post"])
    assert v.tier is Tier.AMBER
    assert "2 posts" in v.summary and "First post" in v.summary


def test_a_batch_saved_as_drafts_reaches_nobody_and_stays_silent(engine):
    v = _channel(engine, "send_posts", posts=["x"], as_draft=True)
    assert v.tier is Tier.GREEN


@pytest.mark.parametrize("tool, args", [
    ("send_sticker", {"emoji": "party", "pack": "Fun pack"}),
    ("send_voice_message", {"text": "hello everyone"}),
    ("send_telegram_file", {"file": "poster.pdf", "caption": "Poster https://x.example/p"}),
])
def test_every_kind_of_send_to_the_channel_is_announced(engine, tool, args):
    assert _channel(engine, tool, **args).tier is Tier.AMBER


@pytest.mark.parametrize("tool, args", [
    ("send_telegram_message", {"text": "a note to myself"}),
    ("send_sticker", {"emoji": "party", "pack": "Fun pack"}),
    ("send_voice_message", {"text": "a note to myself"}),
])
def test_his_own_saved_messages_stays_silent(engine, tool, args):
    v = engine.classify(tool, {"to": SAVED, **args})
    assert v.tier is Tier.GREEN and v.announce is False


def test_everyone_else_still_asks(engine):
    v = engine.classify("send_telegram_message", {"to": "Rodion", "text": "hi"})
    assert v.tier is Tier.RED and v.requires_confirmation is True


def test_read_content_still_cannot_post_to_the_channel(engine):
    v = engine.classify("send_telegram_message", {"to": CHANNEL, "text": "x"}, origin="content")
    assert v.tier is Tier.BLACK


def test_under_paranoid_mode_the_channel_asks(engine):
    engine.paranoid = True
    v = _channel(engine, text="hello")
    assert v.tier is Tier.RED


def test_the_real_hook_speaks_the_summary_before_the_post_goes(monkeypatch):
    """End to end through the brain's hook: announce() gets the first line."""
    import asyncio

    from jalen.audit import AuditLog
    from jalen.brain.agent import Brain

    spoken = []
    safety = SafetyEngine(CONFIG)
    safety.paranoid = False
    safety.posture = "irreversible_only"

    async def confirm(question):
        return True

    async def announce(text):
        spoken.append(text)

    brain = Brain(CONFIG, safety, AuditLog(CONFIG, "test-channel-read-aloud"),
                  confirm=confirm, announce=announce)
    hook = brain._make_hook()
    result = asyncio.run(hook({
        "tool_name": "mcp__jalen__send_telegram_message",
        "tool_input": {"to": CHANNEL, "text": "<b>Reading group tonight</b> https://meet.example/room"},
    }, "id-read-aloud", None))

    assert result == {}, "the post was refused instead of announced"
    assert spoken, "nothing was said before the post"
    assert "Reading group tonight" in spoken[0] and "meet.example" in spoken[0]
    assert "stop" in spoken[0].lower()
