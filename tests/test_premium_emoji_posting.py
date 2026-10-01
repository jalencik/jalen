"""
A post with his premium emoji in it, and what he is told about how it arrived.

WHAT WAS MISSING, MEASURED. data/audit.jsonl (5,046 rows, read 2026-10-01):
he asked for premium emoji and stickers six times on four days (2026-08-21,
08-23, three times on 08-26, 08-29). Not one send in the log has a tg-emoji
tag in it. On
08-26 the brain found out how it is done - a <tg-emoji emoji-id="ID"> tag -
and on 08-29 it told him "I don't have access to premium/paid Telegram
emojis", because nothing could look an id up.

The send path already carried the tag through (messaging._TELEGRAM_TAGS has
tg-emoji). What it did not do, found by running the installed Telethon 1.44
parser on a handful of posts:

  * an EMPTY <tg-emoji emoji-id="1"></tg-emoji> produced no entity and no
    word: the premium emoji vanished and the post went out with a double
    space, "Sent".
  * <tg-emoji emoji-id="1">rocket</tg-emoji> made an entity that covers the
    WORD "rocket"; Telegram has nothing to draw there.
  * Telegram does not refuse custom emoji from an account that is not
    Premium. It accepts the message and drops them, so the send "succeeds"
    and the post is not what he approved. The only way to know is to read the
    message back.
  * an id the brain wrote from memory (a hallucinated one) reached Telegram
    unchecked.

Nothing here touches a network: tests/_telegram_fakes.py is a fake account.
"""
from __future__ import annotations

import asyncio

import pytest

from jarvis.tools import messaging
from tests._telegram_fakes import FakeAccount, FakeChannel, emoji_doc, set_info

ROCKET, PIN, HAND = 5368324170671202286, 5368324170671202287, 5368324170671202288

POST = (
    '<b>Lab Opportunity for NLP students</b> <tg-emoji emoji-id="%d">\U0001f680</tg-emoji>\n\n'
    '<b>Requirements:</b> <tg-emoji emoji-id="%d">\U0001f4cc</tg-emoji>\n\n'
    "• Passion in NLP\n\n"
    "Interested? Don't hesitate to dm me <tg-emoji emoji-id=\"%d\">\U0001f447</tg-emoji>:\n"
    "@Iht_student"
) % (ROCKET, PIN, HAND)


def _kinds(entities):
    return [type(e).__name__ for e in entities]


@pytest.fixture
def account(monkeypatch):
    acct = FakeAccount(entity=FakeChannel())
    for doc_id, alt in ((ROCKET, "\U0001f680"), (PIN, "\U0001f4cc"), (HAND, "\U0001f447")):
        acct.known_emoji[doc_id] = emoji_doc(doc_id, alt)

    async def fake_resolve(_client, _name):
        return acct.entity

    def run(work, **_):
        return asyncio.new_event_loop().run_until_complete(work(acct))

    monkeypatch.setattr(messaging, "_resolve", fake_resolve)
    monkeypatch.setattr(messaging, "_enabled", lambda: None)
    monkeypatch.setattr(messaging.RUNTIME, "run", run)
    return acct


# ------------------------------------------------- 1. the markup is checked
def test_the_tag_still_becomes_a_custom_emoji_entity_at_the_right_place():
    """This part already worked; pinned so the checks below cannot break it."""
    post = messaging._formatted(POST)
    assert not post.refusal and not post.note
    custom = [e for e in post.entities if type(e).__name__ == "MessageEntityCustomEmoji"]
    assert [e.document_id for e in custom] == [ROCKET, PIN, HAND]
    assert post.text.startswith("Lab Opportunity for NLP students \U0001f680")
    # Offsets are UTF-16 units: the rocket is two, so it starts after 33.
    assert custom[0].offset == len("Lab Opportunity for NLP students ")
    assert custom[0].length == 2


def test_an_empty_premium_emoji_tag_is_refused_not_silently_lost():
    post = messaging._formatted('Hi <tg-emoji emoji-id="1"></tg-emoji> there')
    assert post.refusal, "the emoji vanished and the post would have gone out with a gap"
    assert "tg-emoji" in post.refusal and "emoji" in post.refusal.lower()


@pytest.mark.parametrize("inside", ["rocket", "\U0001f680 now", "\U0001f680\n", "7"])
def test_a_premium_emoji_tag_must_wrap_one_emoji_and_nothing_else(inside):
    post = messaging._formatted(f'Hi <tg-emoji emoji-id="1">{inside}</tg-emoji>')
    assert post.refusal, f"{inside!r} would have become an entity over words"


@pytest.mark.parametrize("fallback", [
    "\U0001f680", "\U0001f58a\ufe0f", "\U0001f468\u200d\U0001f4bb",
])
def test_ordinary_fallbacks_are_accepted(fallback):
    """A variation selector or a zero-width joiner is part of an emoji."""
    post = messaging._formatted(f'Hi <tg-emoji emoji-id="1">{fallback}</tg-emoji>')
    assert not post.refusal and post.entities


@pytest.mark.parametrize("bad_id", ["0", "9223372036854775808", "99999999999999999999999"])
def test_an_id_that_cannot_be_a_telegram_id_is_refused_before_the_wire(bad_id):
    """
    Telethon packs the id into 64 signed bits. One too big raised struct.error
    AFTER the send was marked as attempted, and the reply then claimed the
    message "may already be there".
    """
    post = messaging._formatted(f'Hi <tg-emoji emoji-id="{bad_id}">\U0001f680</tg-emoji>')
    assert post.refusal and "id" in post.refusal.lower()


# ------------------------------------------------ 2. before it is sent
def test_an_id_telegram_does_not_know_is_not_sent(account):
    post = POST.replace(str(PIN), "5000000000000000001")
    reply = messaging.send_telegram_message(to="AI engineering & Machine learning", text=post)

    assert reply.startswith("Nothing sent")
    assert "5000000000000000001" in reply, "it must name the id it could not find"
    assert "find_premium_emoji" in reply, "it must say where ids come from"
    assert account.wire == [], "a post with a made-up emoji id reached the channel"


def test_an_empty_document_does_not_make_a_made_up_id_look_known(account):
    """
    For an id it has no emoji for, Telegram can answer with a DocumentEmpty
    that carries the id it was asked about. Counting anything with an id as
    "known" would let every invented id through.
    """
    account.empty_for_unknown = True
    post = POST.replace(str(PIN), "5000000000000000001")
    reply = messaging.send_telegram_message(to="AI engineering & Machine learning", text=post)
    assert reply.startswith("Nothing sent") and "5000000000000000001" in reply
    assert account.wire == []


def test_known_ids_are_checked_in_one_request(account):
    messaging.send_telegram_message(to="AI engineering & Machine learning", text=POST)
    lookups = [r for r in account.requests
               if type(r).__name__ == "GetCustomEmojiDocumentsRequest"]
    assert len(lookups) == 1, "one request for all of them, not one per emoji"
    assert sorted(lookups[0].document_id) == sorted({ROCKET, PIN, HAND})


def test_a_lookup_that_fails_does_not_stop_the_post(account):
    """The check is a safeguard. Not being able to run it is not a reason to lose the post."""
    account.fail_documents_lookup = True
    reply = messaging.send_telegram_message(to="AI engineering & Machine learning", text=POST)
    assert reply.startswith("Sent to")
    assert len(account.wire) == 1


# ---------------------------------------------- 3. after it is sent: read back
def test_the_reply_confirms_every_premium_emoji_arrived(account):
    reply = messaging.send_telegram_message(to="AI engineering & Machine learning", text=POST)

    assert reply.startswith("Sent to AI engineering & Machine learning")
    assert "all 3 premium emoji arrived" in reply
    # It really read the message back rather than trusting its own request.
    assert account.wire[0][3].id in account.stored


def test_the_read_back_asks_for_the_message_by_its_id(account, monkeypatch):
    asked = []
    original = account.get_messages

    async def spy(entity, limit=None, *, ids=None, **kw):
        asked.append(ids)
        return await original(entity, limit, ids=ids, **kw)

    monkeypatch.setattr(account, "get_messages", spy)
    messaging.send_telegram_message(to="AI engineering & Machine learning", text=POST)
    assert asked == [account.wire[0][3].id]


def test_a_non_premium_account_is_told_what_happened(account):
    """
    Telegram accepts the post and drops the custom emoji. "Sent" with no
    caveat is the exact failure this exists to catch.
    """
    account.keeps_premium = False
    reply = messaging.send_telegram_message(to="AI engineering & Machine learning", text=POST)

    assert reply.startswith("Sent to"), "drafting.send_posts reads the opening words"
    assert "0 of 3 premium emoji" in reply
    assert "premium" in reply.lower() and "ordinary" in reply.lower()
    for word in ("nothing sent", "couldn't"):
        assert word not in reply.lower(), "a delivered post must not read as a failure"


def test_some_arriving_and_some_not_is_counted(account, monkeypatch):
    original = account.send_message

    async def drop_the_pin(entity, message="", **kw):
        kept = [e for e in kw["formatting_entities"]
                if getattr(e, "document_id", None) != PIN]
        return await original(entity, message, **{**kw, "formatting_entities": kept})

    monkeypatch.setattr(account, "send_message", drop_the_pin)
    reply = messaging.send_telegram_message(to="AI engineering & Machine learning", text=POST)
    assert "2 of 3 premium emoji" in reply


def test_a_read_back_that_fails_says_it_could_not_confirm(account):
    account.fail_read_back = True
    reply = messaging.send_telegram_message(to="AI engineering & Machine learning", text=POST)
    assert reply.startswith("Sent to")
    assert "couldn't read it back" in reply.lower()
    assert len(account.wire) == 1, "a failed read-back must never trigger a second send"


def test_telegram_rejecting_the_premium_emoji_keeps_the_rest_of_the_formatting(account):
    """
    The old path went straight to plain text: bold, the blockquote and every
    bullet lost because ONE emoji id was refused.
    """
    account.reject_custom_emoji = True
    reply = messaging.send_telegram_message(to="AI engineering & Machine learning", text=POST)

    assert len(account.wire) == 1
    _, text, entities, _ = account.wire[0]
    assert "MessageEntityBold" in _kinds(entities), "the bold went with the emoji"
    assert "MessageEntityCustomEmoji" not in _kinds(entities)
    assert text.startswith("Lab Opportunity for NLP students \U0001f680"), "fallback emoji kept"
    assert reply.startswith("Sent to")
    assert "premium emoji" in reply.lower() and "ordinary" in reply.lower()


def test_a_post_without_premium_emoji_is_unchanged(account):
    """No extra requests, and the reply is the one it always was."""
    reply = messaging.send_telegram_message(
        to="AI engineering & Machine learning", text="<b>Hello</b> there"
    )
    assert reply == "Sent to AI engineering & Machine learning: 'Hello there'"
    assert account.requests == [], "a plain post paid for emoji checks it did not need"


def test_send_posts_counts_a_premium_post_as_sent(account):
    from jarvis.tools import drafting

    reply = drafting.send_posts(to="AI engineering & Machine learning", posts=[POST])
    assert reply.startswith("1 of 1 sent")


def test_a_timeout_after_a_premium_post_is_still_not_confirmed(account, monkeypatch):
    """The read-back must not turn the 'may already be there' answer into a failure."""
    def run(work, **_):
        raise TimeoutError()

    monkeypatch.setattr(messaging.RUNTIME, "run", run)
    reply = messaging.send_telegram_message(to="AI engineering & Machine learning", text=POST)
    assert reply.startswith("Not confirmed")


# ---------------------------------------------------- 4. the drafts path
def test_a_draft_keeps_the_premium_emoji_it_was_given(account):
    """A draft is saved with its entities; nothing about that changed."""
    reply = messaging.save_telegram_draft(to="AI engineering & Machine learning", text=POST)
    assert reply.startswith("Saved as a draft")
    request = account.requests[-1]
    assert [e.document_id for e in request.entities
            if type(e).__name__ == "MessageEntityCustomEmoji"] == [ROCKET, PIN, HAND]


def test_a_draft_with_an_id_telegram_does_not_know_is_not_saved(account):
    """Same safeguard as the send: a draft is what he reads and presses send on."""
    post = POST.replace(str(PIN), "5000000000000000001")
    reply = messaging.save_telegram_draft(to="AI engineering & Machine learning", text=post)
    assert reply.startswith("Nothing saved") and "5000000000000000001" in reply
    assert not any(type(r).__name__ == "SaveDraftRequest" for r in account.requests)
