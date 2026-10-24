"""
When a scheduled post goes out: read from what the brain passes, and said
back in his words.

"Add the scheduled posts too" (2026-10-24). A Telegram post can be scheduled
(messaging.send_telegram_message's schedule_at), moved or sent at once
(reschedule_telegram_post), listed and cancelled. The time comes from the
model, which turns "tomorrow at nine" into a date, so it is read strictly
here, and the safety question and the reply both say it back as a day and a
clock he can check - never as the number the model wrote.

Local time throughout: the laptop's clock is his clock.

A RELATIVE time is read when it is used. The gate reads "tomorrow 09:00" to
ask its question and the tool reads it again to schedule, so a question
answered across midnight would schedule a day later than it said: the brain
is told to give a date ("2026-10-25 09:00"), which reads the same both times.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

# Telegram refuses a schedule in the past, and one a few seconds ahead is an
# ordinary send by another name. A minute is the least worth scheduling.
MIN_AHEAD_S = 60
# Telegram schedules up to a year ahead; past it, SCHEDULE_DATE_TOO_LATE.
MAX_AHEAD = timedelta(days=365)

_RELATIVE = re.compile(r"^(today|tonight|tomorrow)\s+(?:at\s+)?(.+)$")
_CLOCK = re.compile(r"^(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?$")


def _now(now: datetime | None) -> datetime:
    return (now or datetime.now()).astimezone()


def _clock(text: str, evening: bool = False) -> tuple[int, int] | None:
    """(hour, minute) from "9", "09:30", "9am", "9.30 pm", "21:00"; None otherwise."""
    found = _CLOCK.match(text.strip())
    if not found:
        return None
    hour, minute = int(found.group(1)), int(found.group(2) or 0)
    half = (found.group(3) or "").replace(".", "")
    if minute > 59:
        return None
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "pm" else 0)
    elif evening and 1 <= hour <= 11:
        hour += 12                      # "tonight 9" is nine in the evening
    if hour > 23:
        return None
    return hour, minute


def _unreadable(said: str) -> str:
    return (f"I couldn't read {str(said).strip()[:40]!r} as a time - give it as "
            "2026-10-25 09:00, or as 'tomorrow 09:00'")


def parse(said: str, now: datetime | None = None) -> tuple[datetime | None, str]:
    """
    The moment `said` names, in local time - or (None, why not, in words).

    Takes a date and a clock ("2026-10-25 09:00", with a T, seconds or an
    offset as well), and "today 18:00", "tonight 9", "tomorrow at 9:30am".
    Nothing vaguer: "in the morning" is not a time, and a guess here is a post
    at the wrong hour in his channel.
    """
    now = _now(now)
    text = " ".join(str(said or "").lower().split())
    if not text:
        return None, "there's no time to schedule it for"
    relative = _RELATIVE.match(text)
    if relative:
        day, clock = relative.groups()
        hm = _clock(clock, evening=day == "tonight")
        if hm is None:
            return None, _unreadable(said)
        date = now.date() + timedelta(days=1 if day == "tomorrow" else 0)
        moment = datetime(date.year, date.month, date.day, *hm).astimezone()
    else:
        try:
            moment = datetime.fromisoformat(str(said).strip().replace("t", "T"))
        except ValueError:
            return None, _unreadable(said)
        # A time with no zone is his local time; one with a zone is converted
        # to it, so what is said back is what his clock will show.
        moment = moment.astimezone()
    ahead = (moment - now).total_seconds()
    if ahead < 0:
        return None, f"{say(moment, now)} has already passed - it's {say(now, now)} now"
    if ahead < MIN_AHEAD_S:
        return None, "that's less than a minute away - send it now, or pick a later time"
    if moment - now > MAX_AHEAD:
        return None, "that's more than a year away, and Telegram schedules up to a year ahead"
    return moment, ""


def say(moment: datetime, now: datetime | None = None) -> str:
    """"today at 18:00", "tomorrow at 09:00", "Friday 25 October at 09:00"."""
    now = _now(now)
    local = moment.astimezone()
    clock = local.strftime("%H:%M")
    days = (local.date() - now.date()).days
    if days == 0:
        return f"today at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    if days == -1:
        return f"yesterday at {clock}"
    day = f"{local.strftime('%A')} {local.day} {local.strftime('%B')}"
    if local.year != now.year:
        day += f" {local.year}"
    return f"{day} at {clock}"
