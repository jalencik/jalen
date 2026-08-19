"""
Tests for jarvis/integrations/telegram_bot.py (Phase D).

TELEGRAM_ALLOWED_USER_IDS is the entire security boundary for this
integration — is_authorized() gets the most thorough coverage here, as a
pure function, independent of aiogram's Message model entirely. The
dispatcher-level tests call the registered handler directly (via aiogram's
own HandlerObject.callback) with a minimal fake message, rather than
fighting aiogram's full update-parsing machinery for behaviour that's
aiogram's job to get right, not this module's.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.integrations.telegram_bot import build_dispatcher, is_authorized  # noqa: E402


# ------------------------------------------------------------- is_authorized
def test_empty_allow_list_refuses_everyone():
    assert is_authorized(123456789, []) is False


def test_allowed_id_is_authorized():
    assert is_authorized(123456789, [123456789, 987654321]) is True


def test_unlisted_id_is_refused():
    assert is_authorized(111111111, [123456789, 987654321]) is False


def test_none_user_id_is_never_authorized():
    """A message with no attributable sender (e.g. a channel post) must
    never pass, regardless of what's in the allow-list."""
    assert is_authorized(None, [123456789]) is False


# -------------------------------------------------------------- dispatcher
class _FakeUser:
    def __init__(self, user_id):
        self.id = user_id


class _FakeMessage:
    def __init__(self, user_id, text):
        self.from_user = _FakeUser(user_id) if user_id is not None else None
        self.text = text
        self.answer = AsyncMock()


class _FakeJarvis:
    def __init__(self):
        self.say = None
        self.say_blocking = None
        self.processed = []

    def process(self, text):
        self.processed.append(text)


def _get_handler(jarvis, allowed_user_ids):
    dp = build_dispatcher(jarvis, allowed_user_ids)
    return dp.message.handlers[0].callback


def test_unauthorized_message_never_reaches_process():
    jarvis = _FakeJarvis()
    handler = _get_handler(jarvis, [123])
    message = _FakeMessage(user_id=999, text="do something")

    asyncio.run(handler(message))

    assert jarvis.processed == []
    message.answer.assert_not_called()


def test_authorized_message_dispatches_to_process():
    jarvis = _FakeJarvis()
    handler = _get_handler(jarvis, [123])
    message = _FakeMessage(user_id=123, text="what time is it")

    asyncio.run(handler(message))

    # process() runs on a worker thread (see run_turn in telegram_bot.py) —
    # give it a moment to actually execute before asserting.
    import time
    for _ in range(50):
        if jarvis.processed:
            break
        time.sleep(0.02)

    assert jarvis.processed == ["what time is it"]


def test_empty_text_is_ignored():
    jarvis = _FakeJarvis()
    handler = _get_handler(jarvis, [123])
    message = _FakeMessage(user_id=123, text="   ")

    asyncio.run(handler(message))

    assert jarvis.processed == []


def test_authorized_message_overrides_say_to_reply_on_this_chat():
    """say()'s asyncio.run_coroutine_threadsafe targets the loop captured
    when the message was handled — that loop only exists while the same
    asyncio.run() call is still active, so the override must be exercised
    inside it, not after (the exact §3 class of bug: a loop torn down out
    from under an object that still expects to use it)."""
    jarvis = _FakeJarvis()
    handler = _get_handler(jarvis, [123])
    message = _FakeMessage(user_id=123, text="hello")

    async def scenario():
        await handler(message)
        assert jarvis.say is not None
        assert jarvis.say_blocking is not None
        jarvis.say("a reply")
        await asyncio.sleep(0.05)  # let the scheduled coroutine run

    asyncio.run(scenario())

    message.answer.assert_called_with("a reply")
