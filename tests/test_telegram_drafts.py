"""
Saving a channel post as a draft — the thing he asked for by name.

"You will make my post and you will save it as draft format in my Machine
learning community WITHOUT sending it."

A draft is not a send. It is stored server-side, one per chat, and syncs to
his phone, so the post appears in the channel's message box for him to read
and send himself. That is why save_telegram_draft is GREEN while
send_telegram_message stays RED — the difference is whether anyone else can
see it.

Verified live against his real account before these were written: the draft
round-tripped with eleven formatting entities and no raw angle brackets in
the stored text.
"""
from __future__ import annotations

import pytest

from jalen.tools import messaging


class FakeEntity:
    def __init__(self, title="AI engineering & Machine learning"):
        self.title = title
        self.id = 1


class FakeClient:
    """Records what would have been sent to Telegram, and sends nothing."""

    def __init__(self, entity=None, fail_first=False):
        self.entity = entity if entity is not None else FakeEntity()
        self.requests = []
        self.fail_first = fail_first

    async def __call__(self, request):
        self.requests.append(request)
        if self.fail_first and len(self.requests) == 1:
            raise ValueError("ENTITY_BOUNDS_INVALID")
        return True


@pytest.fixture
def wired(monkeypatch):
    """Point the module at a fake client and a resolver that always succeeds."""
    client = FakeClient()

    async def fake_resolve(_client, name):
        return None if name == "nobody" else client.entity

    monkeypatch.setattr(messaging, "_resolve", fake_resolve)
    monkeypatch.setattr(messaging, "_enabled", lambda: None)
    monkeypatch.setattr(messaging.RUNTIME, "run", lambda work: _run(work, client))
    return client


def _run(work, client):
    import asyncio

    return asyncio.new_event_loop().run_until_complete(work(client))


POST = (
    "<b>Lab Opportunity for NLP students</b> \U0001f58a\n\n"
    "A new opening with the <b>Lee Language Lab</b>.\n\n"
    "<b>Requirements:</b>\n\n"
    "• Passion in NLP\n"
    "• Leave a reaction to this post\n\n"
    "<blockquote expandable><b>Most Asked Questions</b>\n\n"
    "<b>1</b> What will you get?\n— Real research experience\n</blockquote>\n\n"
    "Interested? Don't hesitate to dm me \U0001f447:\n@Iht_student"
)


def test_a_draft_is_saved_and_nothing_is_sent(wired):
    result = messaging.save_telegram_draft(to="ML community", text=POST)

    assert "nothing was sent" in result
    assert len(wired.requests) == 1
    request = wired.requests[0]
    assert type(request).__name__ == "SaveDraftRequest", (
        "a draft must go through SaveDraftRequest — SendMessageRequest would post it"
    )


def test_the_html_is_parsed_not_stored_literally(wired):
    """
    Telethon's DEFAULT parse mode is markdown. Left on the default, every
    tag would be stored as visible text: the post would read
    "<b>Lab Opportunity</b>" instead of being bold.
    """
    messaging.save_telegram_draft(to="ML community", text=POST)
    request = wired.requests[0]

    assert "<b>" not in request.message, "HTML tags were stored as literal text"
    assert "Lab Opportunity for NLP students" in request.message
    assert request.entities, "formatting was lost entirely"


def test_the_expandable_qa_block_stays_collapsible(wired):
    """
    <blockquote expandable> is the feature that keeps a long, useful post
    from being a wall of text in the channel feed. Without `collapsed`, it
    renders permanently open.
    """
    messaging.save_telegram_draft(to="ML community", text=POST)
    quotes = [
        e for e in wired.requests[0].entities
        if type(e).__name__ == "MessageEntityBlockquote"
    ]
    assert quotes, "the Q&A block did not become a blockquote"
    assert getattr(quotes[0], "collapsed", None) is True, (
        "the Q&A block will render permanently expanded"
    )


def test_the_spoken_confirmation_does_not_read_tags_aloud(wired):
    """
    This string is spoken by TTS. The first version previewed the RAW html,
    so Jalen said "open angle bracket b close angle bracket Lab Opportunity"
    out loud. The tags are how it is formatted, not part of what it says.
    """
    result = messaging.save_telegram_draft(to="ML community", text=POST)
    assert "<b>" not in result and "<blockquote" not in result
    assert "Lab Opportunity for NLP students" in result


def test_an_unknown_chat_saves_nothing(wired):
    """
    Guessing at a channel name is how a post ends up in the wrong place. The
    resolver refuses ambiguous names; this must not paper over that.
    """
    result = messaging.save_telegram_draft(to="nobody", text=POST)
    assert "couldn't find" in result
    assert wired.requests == [], "it tried to save somewhere despite not resolving the chat"


def test_rejected_markup_falls_back_to_plain_text_and_says_so(monkeypatch):
    """
    A post that saved without formatting is recoverable. One that vanished
    into an exception is not — and silently reporting success would be worst
    of all.
    """
    client = FakeClient(fail_first=True)

    async def fake_resolve(_client, name):
        return client.entity

    monkeypatch.setattr(messaging, "_resolve", fake_resolve)
    monkeypatch.setattr(messaging, "_enabled", lambda: None)
    monkeypatch.setattr(messaging.RUNTIME, "run", lambda work: _run(work, client))

    result = messaging.save_telegram_draft(to="ML community", text=POST)
    assert "WITHOUT formatting" in result
    assert len(client.requests) == 2, "it did not retry as plain text"


def test_draft_is_green_and_send_is_still_red():
    """
    The tier boundary IS the feature. If saving a draft ever became RED it
    would ask permission for something nobody can see; if sending ever
    became GREEN, a post would go out unreviewed.
    """
    from jalen.config import CONFIG
    from jalen.safety import SafetyEngine, Tier

    engine = SafetyEngine(CONFIG)
    assert engine.classify("save_telegram_draft", {}).tier is Tier.GREEN
    assert engine.classify("send_telegram_message", {}).tier is Tier.RED


def test_the_post_format_guide_is_readable_and_complete():
    """
    The guide is loaded by a tool, so a missing or truncated file shows up
    as a badly formatted post rather than an error. Check the load path.
    """
    from jalen.tools.voice import community_post_guide

    guide = community_post_guide()
    assert "couldn't read" not in guide
    for required in ("<blockquote expandable>", "@Iht_student", "•", "Most Asked Questions"):
        assert required in guide, f"the post format guide lost {required!r}"
