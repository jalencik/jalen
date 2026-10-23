"""
When Telegram ANSWERS a send with a refusal, nothing went out - and the reply
has to say that, in words, the first time.

Two ways it did not:

1. A refusal Telegram gives AFTER the send was handed over - a 403 such as
   "you can't post in that chat", or "wait 30 seconds" - came back as "Not
   confirmed — the connection failed (ChatWriteForbiddenError) after I sent
   it". That sends him to check a chat that cannot have the message, and a
   batch counts the post as maybe-sent rather than failed. An RPC error is the
   server's answer; only a timeout or a dropped connection is uncertain.

2. A message over Telegram's 4,096 characters was "refused", so _deliver tried
   it again without its premium emoji and then without any formatting - two
   more round trips refused the same way - and answered that Telegram
   "refused it with its formatting and without", blaming the formatting. Until
   messaging._error_code learned Telegram's own codes, it was spoken as
   BAD_REQUEST.
"""
from __future__ import annotations

import pytest

from jalen.tools import messaging

from test_telegram_posting import wired  # noqa: F401 - fixture

DASH = chr(0x2014)


def send(text="A note for the group"):
    return messaging.send_telegram_message(to="ML study group", text=text)


@pytest.mark.parametrize("refusal, said", [
    ("ChatWriteForbiddenError", "you can't post in that chat"),
    ("UserIsBlockedError", "that person has blocked you"),
    ("ChannelPrivateError", "that chat is private, or you're no longer in it"),
])
def test_a_refusal_after_the_hand_over_is_nothing_sent(wired, refusal, said):
    from telethon import errors

    wired.fail_with = getattr(errors, refusal)(request=None)
    assert send() == f"Nothing sent {DASH} {said}."


def test_being_asked_to_wait_is_nothing_sent_and_says_how_long(wired):
    from telethon import errors

    wired.fail_with = errors.FloodWaitError(request=None, capture=30)
    assert send() == f"Nothing sent {DASH} Telegram asked to wait 30 seconds first."


def test_a_dropped_connection_after_the_hand_over_is_still_not_confirmed(wired):
    wired.fail_with = ConnectionError("reset by peer")
    assert send().startswith(f"Not confirmed {DASH} the connection failed (ConnectionError)")


def test_too_long_is_said_once_and_not_retried_without_its_formatting(wired):
    from telethon import errors

    wired.fail_with = errors.MessageTooLongError(request=None)
    reply = send("<b>A long post</b> with formatting")
    assert reply == (f"Nothing sent {DASH} it's longer than one Telegram message can be "
                     "(4,096 characters), so shorten it.")
    assert wired.attempts == 1, "the words were refused, and it retried the formatting"


def test_a_refused_post_in_a_batch_is_failed_not_unconfirmed(wired):
    from telethon import errors

    from jalen.tools import drafting

    wired.fail_with = errors.ChatWriteForbiddenError(request=None)
    reply = drafting.send_posts(to="ML study group", posts=["one", "two"])
    assert reply.startswith("0 of 2 sent") and "FAILED (2)" in reply
    assert "NOT CONFIRMED" not in reply
