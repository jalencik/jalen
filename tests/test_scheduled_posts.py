"""
"Add the scheduled posts too."                          - his words, 2026-10-24

A post can now wait in Telegram's own queue and go out later:

  send_telegram_message(schedule_at=...)   schedules it, gated as a send now
  scheduled_telegram_posts                 lists the queue (fenced)
  cancel_scheduled_post                    takes one out (AMBER)
  reschedule_telegram_post                 moves one, or sends it now (AMBER)

What these tests hold them to:
  * the time is read strictly (jalen/when.py) and said back as a day and a
    clock - in the question, in the announcement and in the reply - so a wrong
    date the model wrote is something he can hear;
  * a time that has passed, is too close, too far or unreadable sends nothing;
  * a scheduled post gets the same format check, premium emoji and gate as a
    post now;
  * scheduled posts have their own numbering: the premium-emoji read-back, a
    cancel and a move look only among the scheduled ones, never at the chat
    message that happens to share the number;
  * a move changes only the time - no words, no formatting re-sent;
  * nothing the brain has read can schedule, cancel or move a post.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from jalen import when
from jalen.config import CONFIG
from jalen.safety import SafetyEngine, Tier
from jalen.tools import messaging

from test_any_action_in_telegram import CHANNEL, DASH, Account, Msg
from test_channel_posts_follow_his_format import BROKEN, LEE_LAB

PREMIUM_ID = 5368324170671202288


def at(days=1, clock="09:00") -> str:
    """A time `days` ahead, as the brain is told to write it."""
    return (datetime.now() + timedelta(days=days)).strftime(f"%Y-%m-%d {clock}")


def said(text: str) -> str:
    moment, why = when.parse(text)
    assert moment is not None, why
    return when.say(moment)


# --------------------------------------------------------------- the fake
class Queue(Account):
    """An account with Telegram's scheduled queue, in its own numbering."""

    def __init__(self):
        super().__init__()
        self.waiting = {}           # chat id -> {scheduled id: Msg}
        self.next_id = 1

    def _queue(self, chat_id):
        return self.waiting.setdefault(chat_id, {})

    def add(self, chat_id, text, when_, entities=None):
        message = Msg(self.next_id, text, out=True)
        message.date, message.entities = when_, list(entities or [])
        self._queue(chat_id)[self.next_id] = message
        self.next_id += 1
        return message

    async def send_message(self, entity, message="", *, parse_mode=(), formatting_entities=None,
                           schedule=None, reply_to=None, **_):
        self.calls.append(("send", entity.id, message, schedule))
        self._raise("send")
        if schedule is None:
            sent = Msg(900, message, out=True)
            self.history[entity.id].insert(0, sent)
            return sent
        return self.add(entity.id, message, schedule, formatting_entities)

    async def edit_message(self, entity, message, text=None, *, parse_mode=(),
                           formatting_entities=None, schedule=None, **_):
        self.calls.append(("edit", entity.id, message, text, parse_mode, schedule))
        self._raise("edit")
        queued = self._queue(entity.id).get(message)
        if schedule is not None and queued is not None:
            queued.date = schedule
        return queued

    async def __call__(self, request):
        from telethon.tl import functions

        name = type(request).__name__
        self.calls.append(("request", name, getattr(request, "id", None)))
        self._raise(name)
        queue = self._queue(request.peer.id)
        if isinstance(request, functions.messages.GetScheduledHistoryRequest):
            return SimpleNamespace(messages=list(queue.values()))
        if isinstance(request, functions.messages.GetScheduledMessagesRequest):
            return SimpleNamespace(messages=[queue[i] for i in request.id if i in queue])
        if isinstance(request, functions.messages.DeleteScheduledMessagesRequest):
            for i in request.id:
                queue.pop(i, None)
            return True
        if isinstance(request, functions.messages.SendScheduledMessagesRequest):
            for i in request.id:
                self.history[request.peer.id].insert(0, queue.pop(i))
            return True
        return await super().__call__(request)


@pytest.fixture
def tg(monkeypatch):
    account = Queue()

    async def resolve(_client, name):
        return account.named(name)

    def run(work, **_):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(work(account))
        finally:
            loop.close()

    monkeypatch.setattr(messaging, "_resolve", resolve)
    monkeypatch.setattr(messaging, "_enabled", lambda: None)
    monkeypatch.setattr(messaging.RUNTIME, "run", run)
    return account


def engine():
    e = SafetyEngine(CONFIG)
    e.paranoid = False
    e.posture = "irreversible_only"
    return e


# --------------------------------------------------------------------------
# 1. THE TIME IS READ STRICTLY AND SAID BACK IN WORDS
# --------------------------------------------------------------------------
NOW = datetime(2026, 10, 24, 14, 5).astimezone()


@pytest.mark.parametrize("given, heard", [
    ("2026-10-25 09:00", "tomorrow at 09:00"),
    ("2026-10-25T09:00", "tomorrow at 09:00"),
    ("tomorrow 9am", "tomorrow at 09:00"),
    ("tomorrow at 9:30 pm", "tomorrow at 21:30"),
    ("today 18:00", "today at 18:00"),
    ("tonight 9", "today at 21:00"),
    ("tomorrow 12am", "tomorrow at 00:00"),
    ("2026-10-30 09:00", "Friday 30 October at 09:00"),
    ("2027-01-04 08:15", "Monday 4 January 2027 at 08:15"),
])
def test_a_time_is_read_and_said_back_as_a_day_and_a_clock(given, heard):
    moment, why = when.parse(given, NOW)
    assert why == "" and when.say(moment, NOW) == heard


@pytest.mark.parametrize("given, why", [
    ("today 13:00", "has already passed"),
    ("today 14:05", "less than a minute away"),
    ("2026-10-24 14:05:30", "less than a minute away"),
    ("2027-12-01 10:00", "more than a year away"),
    ("in the morning", "couldn't read"),
    ("tomorrow 25:00", "couldn't read"),
    ("tomorrow 13pm", "couldn't read"),
    ("", "no time"),
])
def test_a_time_that_cannot_be_kept_is_refused_in_words(given, why):
    moment, problem = when.parse(given, NOW)
    assert moment is None and why in problem


def test_a_time_with_a_zone_is_said_in_his_own_clock():
    moment, _ = when.parse("2026-10-25T04:00:00+00:00", NOW)
    assert moment.utcoffset() == NOW.utcoffset()
    assert moment == datetime.fromisoformat("2026-10-25T04:00:00+00:00")


# --------------------------------------------------------------------------
# 2. SCHEDULING A POST
# --------------------------------------------------------------------------
def test_a_scheduled_post_goes_into_the_queue_not_the_chat(tg):
    reply = messaging.send_telegram_message(to="Ali", text="Happy birthday!",
                                            schedule_at=at(1, "09:00"))
    kind, chat, words, schedule = tg.calls[0]
    assert (kind, chat, words) == ("send", 7, "Happy birthday!")
    assert schedule == when.parse(at(1, "09:00"))[0] and schedule.tzinfo is not None
    assert tg.history[7][0].text != "Happy birthday!", "it went out now"
    assert reply == f"Scheduled for {said(at(1, '09:00'))} in Ali: 'Happy birthday!'"
    assert reply.startswith("Scheduled for tomorrow at 09:00")


@pytest.mark.parametrize("bad", ["yesterday", "in the morning", at(-1, "09:00"), at(400, "09:00")])
def test_a_time_that_cannot_be_kept_schedules_nothing(tg, bad):
    reply = messaging.send_telegram_message(to="Ali", text="hi", schedule_at=bad)
    assert reply.startswith(f"Nothing sent {DASH}")
    assert tg.calls == []


def test_a_scheduled_post_in_his_format_is_checked_like_one_sent_now(tg):
    broken = BROKEN["no requirements"][0](LEE_LAB)
    reply = messaging.send_telegram_message(to=CHANNEL, text=broken, schedule_at=at(1))
    assert reply.startswith(f"Nothing sent {DASH} the post doesn't follow your channel's format")
    assert tg.calls == []
    assert messaging.send_telegram_message(
        to=CHANNEL, text=LEE_LAB, schedule_at=at(1)).startswith("Scheduled for")


def test_premium_emoji_are_read_back_from_the_queue_not_from_a_chat_message(tg):
    """Scheduled post 1 and chat message 1 are different messages."""
    tg.history[7].append(Msg(1, "an old message with no premium emoji", minute=1))
    post = f'see you <tg-emoji emoji-id="{PREMIUM_ID}">{chr(0x1F44B)}</tg-emoji>'

    async def known(client, ids):
        return []

    messaging._unknown_premium_emoji, saved = known, messaging._unknown_premium_emoji
    try:
        reply = messaging.send_telegram_message(to="Ali", text=post, schedule_at=at(1))
    finally:
        messaging._unknown_premium_emoji = saved
    assert ("request", "GetScheduledMessagesRequest", [1]) in tg.calls
    assert "Read it back: all 1 premium emoji arrived." in reply


# --------------------------------------------------------------------------
# 3. SEEING, CANCELLING AND MOVING WHAT IS SCHEDULED
# --------------------------------------------------------------------------
def _two_waiting(tg):
    first = tg.add(1, "<b>Lab</b> opportunity", when.parse(at(1, "09:00"))[0])
    second = tg.add(1, "Reminder: forms close tonight", when.parse(at(2, "18:00"))[0])
    return first, second


def test_the_queue_is_listed_soonest_first_with_numbers_and_fenced(tg):
    from jalen import taint

    second, first = reversed(_two_waiting(tg))      # queued in the other order
    tg.waiting[1] = {second.id: second, first.id: first}
    reply = messaging.scheduled_telegram_posts(chat=CHANNEL)
    assert reply.splitlines()[0].endswith(f"scheduled in {CHANNEL}, soonest first:")
    assert f"#{first.id} {said(at(1, '09:00'))}" in reply
    assert reply.index(f"#{first.id} ") < reply.index(f"#{second.id} ")
    assert "UNTRUSTED CONTENT" in reply and taint.origin_now() == "content"


def test_an_empty_queue_says_so(tg):
    assert messaging.scheduled_telegram_posts(chat="Ali") == "Nothing is scheduled in Ali."


def test_cancelling_by_number_takes_only_that_post_out(tg):
    first, second = _two_waiting(tg)
    reply = messaging.cancel_scheduled_post(chat=CHANNEL, message=f"#{second.id}")
    assert ("request", "DeleteScheduledMessagesRequest", [second.id]) in tg.calls
    assert list(tg.waiting[1]) == [first.id]
    assert reply.startswith("Cancelled your message 'Reminder: forms close tonight', which "
                            f"was scheduled for {said(at(2, '18:00'))} in {CHANNEL}")


def test_cancelling_the_next_one_takes_the_soonest(tg):
    first, _second = _two_waiting(tg)
    messaging.cancel_scheduled_post(chat=CHANNEL, message="next")
    assert first.id not in tg.waiting[1]


def test_by_its_words_it_finds_the_one_post(tg):
    _first, second = _two_waiting(tg)
    messaging.cancel_scheduled_post(chat=CHANNEL, message="forms close")
    assert second.id not in tg.waiting[1]


def test_with_two_waiting_it_or_nothing_is_a_question(tg):
    first, second = _two_waiting(tg)
    reply = messaging.cancel_scheduled_post(chat=CHANNEL)
    assert reply.startswith(f"Nothing cancelled {DASH} 2 posts are scheduled there")
    assert reply.endswith("Which one do you mean?")
    assert f"#{first.id} {said(at(1, '09:00'))}" in reply
    assert len(tg.waiting[1]) == 2


def test_the_only_one_needs_no_name(tg):
    only = tg.add(7, "Happy birthday!", when.parse(at(1))[0])
    messaging.cancel_scheduled_post(chat="Ali")
    assert only.id not in tg.waiting[7]


def test_a_scheduled_number_is_never_a_chat_message(tg):
    """Chat message 12 exists in Ali's chat; scheduled post 12 does not."""
    tg.add(7, "Happy birthday!", when.parse(at(1))[0])
    reply = messaging.cancel_scheduled_post(chat="Ali", message="#12")
    assert reply == f"Nothing cancelled {DASH} no scheduled post there is number 12."


def test_nothing_waiting_is_said(tg):
    assert messaging.cancel_scheduled_post(chat="Ali") == (
        f"Nothing cancelled {DASH} nothing is scheduled there.")


def test_moving_a_post_changes_only_its_time(tg):
    first, _second = _two_waiting(tg)
    reply = messaging.reschedule_telegram_post(chat=CHANNEL, schedule_at=at(1, "10:30"),
                                               message="next")
    edit = next(c for c in tg.calls if c[0] == "edit")
    assert edit[1:5] == (1, first.id, None, None), "words or a parse mode were re-sent"
    assert edit[5] == when.parse(at(1, "10:30"))[0]
    assert reply == (f"Moved your message '<b>Lab</b> opportunity' in {CHANNEL} from "
                     f"{said(at(1, '09:00'))} to {said(at(1, '10:30'))}.")


def test_now_sends_the_scheduled_post_at_once(tg):
    only = tg.add(7, "Happy birthday!", when.parse(at(1))[0])
    reply = messaging.reschedule_telegram_post(chat="Ali", schedule_at="now")
    assert ("request", "SendScheduledMessagesRequest", [only.id]) in tg.calls
    assert reply.startswith("Sent your message 'Happy birthday!' to Ali now, instead of")


def test_a_move_to_a_time_that_has_passed_moves_nothing(tg):
    tg.add(7, "Happy birthday!", when.parse(at(1))[0])
    reply = messaging.reschedule_telegram_post(chat="Ali", schedule_at=at(-1))
    assert reply.startswith(f"Nothing moved {DASH}") and tg.calls == []


# --------------------------------------------------------------------------
# 4. THE GATE SAYS WHEN
# --------------------------------------------------------------------------
def test_a_scheduled_message_to_a_person_is_asked_with_its_time_and_words():
    verdict = engine().classify("send_telegram_message", {
        "to": "Ali", "text": "Happy birthday!", "schedule_at": at(1, "09:00")})
    assert verdict.tier is Tier.RED
    assert verdict.summary == (f"send Ali a Telegram message, to go out {said(at(1, '09:00'))}, "
                               'saying "Happy birthday!"')


def test_a_scheduled_post_to_his_channel_is_announced_with_its_time():
    verdict = engine().classify("send_telegram_message", {
        "to": CHANNEL, "text": "<b>Lab</b> post", "schedule_at": at(1, "09:00")})
    assert verdict.tier is Tier.AMBER
    assert verdict.summary.startswith(f"schedule a post to {CHANNEL} for {said(at(1, '09:00'))}")


@pytest.mark.parametrize("tool, args, summary", [
    ("cancel_scheduled_post", {"chat": CHANNEL, "message": "#5"},
     f"cancel scheduled post number 5 in {CHANNEL}, so it never goes out"),
    ("reschedule_telegram_post", {"chat": "Ali", "schedule_at": "now"},
     "send the scheduled post in Ali now, instead of when it was scheduled"),
])
def test_cancel_and_move_are_announced(tool, args, summary):
    verdict = engine().classify(tool, args)
    assert verdict.tier is Tier.AMBER and verdict.summary == summary


def test_a_move_is_announced_with_the_new_time():
    verdict = engine().classify("reschedule_telegram_post", {
        "chat": CHANNEL, "schedule_at": at(1, "10:30"), "message": "next"})
    assert verdict.summary == (f"move the next scheduled post in {CHANNEL} to "
                               f"{said(at(1, '10:30'))}")


@pytest.mark.parametrize("tool, args", [
    ("send_telegram_message", {"to": CHANNEL, "text": "x", "schedule_at": at(1)}),
    ("cancel_scheduled_post", {"chat": CHANNEL}),
    ("reschedule_telegram_post", {"chat": CHANNEL, "schedule_at": "now"}),
])
def test_nothing_read_can_schedule_cancel_or_move_a_post(tool, args):
    assert engine().classify(tool, args, origin="content").tier is Tier.BLACK


def test_the_brain_is_told_how():
    from jalen.brain.tools import TOOL_SPECS

    assert "schedule_at" in TOOL_SPECS["send_telegram_message"][1]
    for tool in ("scheduled_telegram_posts", "cancel_scheduled_post", "reschedule_telegram_post"):
        assert messaging.REGISTRY[tool].__name__ == tool and tool in TOOL_SPECS
    assert "get_time" in TOOL_SPECS["send_telegram_message"][0]
