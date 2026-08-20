"""
Google Calendar: what's on today, find an event, create one.

Tier note: config/safety.yaml puts create_calendar_event in GREEN, not RED,
and that is deliberate rather than an oversight. The AMBER/RED line in this
project is "destroys content or reaches other people". A calendar event on
his own calendar does neither — it is additive and he can delete it in two
clicks. Gating it would mean a spoken confirmation every time he asks to be
reminded of something, which is the friction that made the earlier build
unusable. If he ever invites other attendees, that DOES reach people, and
this module refuses rather than quietly widening the blast radius.
"""
from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from ..config import CONFIG
from ..integrations.google_auth import GoogleNotConnected, calendar_service

_MAX_RESULTS = 25


def _enabled() -> None:
    if not CONFIG.get_path("integrations.calendar.enabled", False):
        raise RuntimeError(
            "Calendar is switched off in config/jarvis.yaml "
            "(integrations.calendar.enabled)."
        )


def _local_day(offset_days: int = 0) -> tuple[str, str, _dt.date]:
    """RFC3339 start/end bounds for a local calendar day."""
    day = _dt.date.today() + _dt.timedelta(days=offset_days)
    start = _dt.datetime.combine(day, _dt.time.min).astimezone()
    end = _dt.datetime.combine(day, _dt.time.max).astimezone()
    return start.isoformat(), end.isoformat(), day


def _when(event: dict) -> str:
    start = event.get("start") or {}
    if start.get("date"):
        return "all day"
    raw = start.get("dateTime")
    if not raw:
        return "time unknown"
    try:
        return _dt.datetime.fromisoformat(raw).strftime("%H:%M")
    except ValueError:
        return raw


def _describe(event: dict) -> str:
    title = event.get("summary") or "(no title)"
    location = event.get("location")
    suffix = f" — {location}" if location else ""
    return f"- {_when(event)}  {title}{suffix}"


def read_calendar(days_ahead: int = 0) -> str:
    """
    List events on a day. 0 = today, 1 = tomorrow, and so on.

    Spoken as "what's on today" / "what have I got tomorrow".
    """
    _enabled()
    service = calendar_service()
    start, end, day = _local_day(int(days_ahead))
    events = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=start,
            timeMax=end,
            singleEvents=True,      # expand recurring series into instances
            orderBy="startTime",
            maxResults=_MAX_RESULTS,
        )
        .execute()
        .get("items", [])
    )
    label = day.strftime("%A %d %B")
    if not events:
        return f"Nothing on the calendar for {label}."
    lines = "\n".join(_describe(e) for e in events)
    return f"{len(events)} event(s) on {label}:\n{lines}"


def search_calendar(query: str, days: int = 90) -> str:
    """Find events by text, from today forward."""
    _enabled()
    service = calendar_service()
    now = _dt.datetime.now().astimezone()
    events = (
        service.events()
        .list(
            calendarId="primary",
            q=query,
            timeMin=now.isoformat(),
            timeMax=(now + _dt.timedelta(days=int(days))).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=_MAX_RESULTS,
        )
        .execute()
        .get("items", [])
    )
    if not events:
        return f"No events match {query!r} in the next {days} days."
    lines = []
    for event in events:
        start = (event.get("start") or {})
        stamp = start.get("dateTime") or start.get("date") or "?"
        lines.append(f"- {stamp[:16]}  {event.get('summary') or '(no title)'}")
    return f"{len(events)} event(s) matching {query!r}:\n" + "\n".join(lines)


_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2})?")


def create_calendar_event(
    summary: str,
    start: str,
    end: str = "",
    location: str = "",
    description: str = "",
) -> str:
    """
    Create an event on his own calendar.

    `start`/`end` are ISO: "2026-08-22T14:00" or "2026-08-22" for all-day.
    An omitted end defaults to one hour after start, which is what a person
    means by "put a meeting at two".
    """
    _enabled()
    if not _ISO.match(start.strip()):
        return (
            f"I need the start as a date or date-and-time, got {start!r}. "
            "Example: 2026-08-22T14:00"
        )

    all_day = "T" not in start and " " not in start.strip()
    if all_day:
        body: dict[str, Any] = {
            "summary": summary,
            "start": {"date": start.strip()},
            "end": {"date": (end.strip() or start.strip())},
        }
    else:
        begin = _dt.datetime.fromisoformat(start.strip().replace(" ", "T"))
        finish = (
            _dt.datetime.fromisoformat(end.strip().replace(" ", "T"))
            if end.strip()
            else begin + _dt.timedelta(hours=1)
        )
        body = {
            "summary": summary,
            "start": {"dateTime": begin.astimezone().isoformat()},
            "end": {"dateTime": finish.astimezone().isoformat()},
        }
    if location:
        body["location"] = location
    if description:
        body["description"] = description

    service = calendar_service()
    event = service.events().insert(calendarId="primary", body=body).execute()
    when = start if all_day else body["start"]["dateTime"][:16].replace("T", " ")
    return f"Added {summary!r} to your calendar for {when}. [event id: {event.get('id')}]"


def calendar_status() -> str:
    from ..integrations import google_auth

    if not CONFIG.get_path("integrations.calendar.enabled", False):
        return "Calendar is switched off in config/jarvis.yaml."
    if not google_auth.have_token():
        return (
            "Google isn't connected. Run: "
            ".venv\\Scripts\\python.exe scripts\\connect_google.py"
        )
    try:
        return read_calendar(0)
    except GoogleNotConnected as exc:
        return str(exc)


REGISTRY: dict[str, Any] = {
    "read_calendar": read_calendar,
    "search_calendar": search_calendar,
    "create_calendar_event": create_calendar_event,
    "calendar_status": calendar_status,
}


def call(tool: str, args: dict) -> str:
    fn = REGISTRY.get(tool)
    if fn is None:
        raise KeyError(tool)
    return fn(**(args or {}))
